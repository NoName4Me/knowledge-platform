"""knowledge-base visibility and member access

Revision ID: 0026_kb_access
Revises: 0025_revoke_inflight_claims
Create Date: 2026-10-22
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_kb_access"
down_revision: str | None = "0025_revoke_inflight_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"non-identifier RLS role: {ROLE_NAME}")


def upgrade() -> None:
    # Existing knowledge bases keep the old team-wide behavior. ``owner_id`` is
    # nullable because older bases predate per-base ownership.
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default="team",
        ),
    )
    op.add_column(
        "knowledge_bases",
        sa.Column(
            "team_permission",
            sa.String(20),
            nullable=False,
            server_default="inherit",
        ),
    )
    op.create_check_constraint(
        "ck_kb_visibility",
        "knowledge_bases",
        "visibility in ('team', 'private')",
    )
    op.create_check_constraint(
        "ck_kb_team_permission",
        "knowledge_bases",
        "team_permission in ('inherit', 'view', 'edit', 'manage')",
    )
    op.create_index("ix_knowledge_bases_owner_id", "knowledge_bases", ["owner_id"])

    op.create_table(
        "knowledge_base_access",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "permission in ('view', 'edit', 'manage')",
            name="ck_kb_access_permission",
        ),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "user_id",
            name="uq_kb_access_member",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "team_id"],
            ["knowledge_bases.id", "knowledge_bases.team_id"],
            ondelete="CASCADE",
            name="fk_kb_access_scope",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_kb_access_user",
        ),
    )
    op.create_index(
        "ix_kb_access_knowledge_base_id",
        "knowledge_base_access",
        ["knowledge_base_id"],
    )
    op.create_index(
        "ix_kb_access_user_id",
        "knowledge_base_access",
        ["user_id"],
    )

    op.execute(
        f"grant select, insert, update, delete on knowledge_base_access to {ROLE_NAME}"
    )
    op.execute(f"grant select, insert, update on knowledge_bases to {ROLE_NAME}")
    op.execute("alter table knowledge_base_access enable row level security")
    op.execute(
        """
        create policy knowledge_base_access_team_scope on knowledge_base_access
        using (team_id = app_current_team())
        with check (team_id = app_current_team())
        """
    )

    # One security-definer function is the source of truth for API and worker
    # authorization. It never returns content, only the effective permission.
    op.execute(
        """
        create or replace function app_kb_permission(
          target_kb uuid, target_user uuid
        ) returns text
        language sql stable security definer set search_path = public as $$
          select case
            when kb.owner_id = target_user then 'manage'
            when kb.visibility = 'team' and membership.role = 'admin' then 'manage'
            when kb.visibility = 'private' then access.permission
            else case
              when (
                case coalesce(access.permission, '')
                  when 'manage' then 3
                  when 'edit' then 2
                  when 'view' then 1
                  else 0
                end
              ) >= (
                case
                  when kb.team_permission = 'manage' then 3
                  when kb.team_permission = 'edit' then 2
                  when kb.team_permission = 'view' then 1
                  when membership.role = 'member' then 2
                  else 1
                end
              )
              then access.permission
              when kb.team_permission in ('view', 'edit', 'manage')
                then kb.team_permission
              when membership.role = 'member'
                then 'edit'
              else 'view'
            end
          end
          from knowledge_bases kb
          join teams team on team.id = kb.team_id
          join team_memberships membership
            on membership.team_id = kb.team_id
           and membership.user_id = target_user
          join users user_account
            on user_account.id = membership.user_id
          left join knowledge_base_access access
            on access.knowledge_base_id = kb.id
           and access.user_id = target_user
          where kb.id = target_kb
            and team.is_active
            and user_account.is_active
        $$
        """
    )
    op.execute(
        "revoke all on function app_kb_permission(uuid, uuid) from public"
    )
    op.execute(
        f"grant execute on function app_kb_permission(uuid, uuid) to {ROLE_NAME}"
    )

    # Creating a base and its owner access is one atomic operation. The runtime
    # role cannot satisfy the knowledge-base write policy before the access row
    # exists, so keep that bootstrap inside a narrowly scoped definer function.
    op.execute(
        """
        create or replace function app_create_knowledge_base(
          target_team uuid,
          target_user uuid,
          kb_name text,
          kb_visibility text
        ) returns uuid
        language plpgsql security definer set search_path = public as $$
        declare
          new_id uuid := gen_random_uuid();
          normalized_name text := btrim(kb_name);
        begin
          if normalized_name is null or normalized_name = '' then
            raise exception 'knowledge base name is required';
          end if;
          if kb_visibility not in ('team', 'private') then
            raise exception 'invalid knowledge base visibility';
          end if;
          if not app_can_access_team(target_team, target_user) then
            raise exception 'user cannot create a knowledge base in this team';
          end if;

          insert into knowledge_bases (
            id, team_id, owner_id, name, visibility, team_permission
          ) values (
            new_id, target_team, target_user, normalized_name,
            kb_visibility, 'inherit'
          );
          insert into knowledge_base_access (
            id, team_id, knowledge_base_id, user_id, permission
          ) values (
            gen_random_uuid(), target_team, new_id, target_user, 'manage'
          );
          return new_id;
        end
        $$
        """
    )
    op.execute(
        "revoke all on function "
        "app_create_knowledge_base(uuid, uuid, text, text) from public"
    )
    op.execute(
        f"grant execute on function "
        f"app_create_knowledge_base(uuid, uuid, text, text) to {ROLE_NAME}"
    )

    # Parsing jobs need the uploader's identity for the knowledge-base boundary.
    # Return only scheduling IDs and let the worker restore that context.
    op.execute("drop function app_claim_task(integer)")
    op.execute("drop function app_reclaim_tasks()")
    op.execute(
        """
        create or replace function app_claim_task(lease_seconds integer)
        returns table(
          task_id uuid, team_id uuid, owner_id uuid, lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select task.id
            from processing_tasks task
            join documents document on document.id = task.document_id
            join teams team on team.id = task.team_id
            left join users uploader on uploader.id = document.uploaded_by_id
            where task.status = 'queued'
              and task.attempts < task.max_attempts
              and document.status in ('pending', 'processing')
              and team.is_active
              and (document.uploaded_by_id is null or uploader.is_active)
            order by task.created_at, task.id
            for update of task skip locked
            limit 1
          )
          update processing_tasks target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.team_id,
                    (
                      select document.uploaded_by_id
                      from documents document
                      where document.id = target.document_id
                    ),
                    target.lease_token
        $$
        """
    )
    op.execute(
        "revoke all on function app_claim_task(integer) from public"
    )
    op.execute(f"grant execute on function app_claim_task(integer) to {ROLE_NAME}")
    op.execute(
        """
        create or replace function app_reclaim_tasks()
        returns table(
          task_id uuid, team_id uuid, owner_id uuid, exhausted boolean
        )
        language sql security definer set search_path = public as $$
          with expired as (
            select task.id, task.team_id,
                   (
                     select document.uploaded_by_id
                     from documents document
                     where document.id = task.document_id
                   ) as owner_id,
                   task.attempts >= task.max_attempts as exhausted
            from processing_tasks task
            where task.status = 'running' and task.lease_expires_at < now()
            for update skip locked
          ),
          updated as (
            update processing_tasks target
            set status = case when expired.exhausted then 'failed' else 'queued' end,
                lease_expires_at = null,
                updated_at = now()
            from expired
            where target.id = expired.id
            returning target.id, target.team_id,
                      expired.owner_id, expired.exhausted
          )
          select updated.id, updated.team_id,
                 updated.owner_id, updated.exhausted
          from updated
        $$
        """
    )
    op.execute("revoke all on function app_reclaim_tasks() from public")
    op.execute(f"grant execute on function app_reclaim_tasks() to {ROLE_NAME}")

    # Revoked work must converge even though the application role can no longer
    # read it through the knowledge-base RLS policies. This definer function
    # updates only terminal metadata and never returns content.
    op.execute(
        """
        create or replace function app_revoke_inaccessible_knowledge_work()
        returns integer
        language plpgsql security definer set search_path = public as $$
        declare
          changed integer := 0;
          affected integer := 0;
        begin
          update analysis_documents execution
          set status = 'failed',
              error_code = 'ANALYSIS_PERMISSION_REVOKED',
              error_detail = '创建者已不能访问该知识库',
              lease_token = null,
              lease_expires_at = null,
              finished_at = coalesce(execution.finished_at, now()),
              updated_at = now()
          from analysis_tasks task
          where execution.task_id = task.id
            and execution.status in ('queued', 'running')
            and app_kb_permission(task.knowledge_base_id, execution.owner_id) is null;
          get diagnostics affected = row_count;
          changed := changed + affected;

          update analysis_summaries summary
          set status = 'failed',
              error_code = 'ANALYSIS_PERMISSION_REVOKED',
              error_detail = '创建者已不能访问该知识库',
              lease_token = null,
              lease_expires_at = null,
              finished_at = coalesce(summary.finished_at, now()),
              updated_at = now()
          from analysis_tasks task
          where summary.task_id = task.id
            and summary.status in ('queued', 'running')
            and app_kb_permission(task.knowledge_base_id, summary.owner_id) is null;
          get diagnostics affected = row_count;
          changed := changed + affected;

          update analysis_tasks task
          set status = 'failed',
              stage = 'finished',
              error_code = 'ANALYSIS_PERMISSION_REVOKED',
              error_detail = '创建者已不能访问该知识库',
              finished_at = coalesce(task.finished_at, now()),
              updated_at = now()
          where task.status in ('queued', 'running')
            and app_kb_permission(task.knowledge_base_id, task.created_by_id) is null;
          get diagnostics affected = row_count;
          changed := changed + affected;

          update knowledge_automations automation
          set status = 'failed',
              stage = 'finished',
              error_code = 'AUTOMATION_PERMISSION_REVOKED',
              error_detail = '创建者已不能访问该知识库',
              lease_token = null,
              lease_expires_at = null,
              finished_at = coalesce(automation.finished_at, now()),
              updated_at = now()
          where automation.status in ('queued', 'running')
            and app_kb_permission(
              automation.knowledge_base_id, automation.created_by_id
            ) is null;
          get diagnostics affected = row_count;
          changed := changed + affected;

          return changed;
        end
        $$
        """
    )
    op.execute(
        "revoke all on function "
        "app_revoke_inaccessible_knowledge_work() from public"
    )
    op.execute(
        f"grant execute on function "
        f"app_revoke_inaccessible_knowledge_work() to {ROLE_NAME}"
    )

    # A long-lived answer stream can lose access between provider chunks. Once
    # membership is gone the normal turn update is hidden by RLS, so allow this
    # narrowly scoped maintenance operation to clear the creator's own draft and
    # persist a terminal error without returning any business content.
    op.execute(
        """
        create or replace function app_fail_revoked_turn(
          target_turn uuid,
          target_user uuid,
          target_error text
        ) returns boolean
        language plpgsql security definer set search_path = public as $$
        begin
          update turns
          set status = 'failed',
              error_code = target_error,
              answer_draft = null,
              finished_at = coalesce(finished_at, now())
          where id = target_turn
            and created_by_id = target_user
            and status = 'answering';
          return found;
        end
        $$
        """
    )
    op.execute(
        "revoke all on function "
        "app_fail_revoked_turn(uuid, uuid, text) from public"
    )
    op.execute(
        f"grant execute on function "
        f"app_fail_revoked_turn(uuid, uuid, text) to {ROLE_NAME}"
    )

    # Upload reconciliation scans across teams, then restores one valid user
    # context before reading the document. Prefer the uploader; legacy orphaned
    # rows fall back to the library owner, then an authorized manager/member.
    op.execute(
        "drop function if exists "
        "app_reconcile_upload_candidates(timestamptz, integer)"
    )
    op.execute(
        """
        create or replace function app_reconcile_upload_candidates(
          cutoff timestamptz,
          batch_limit integer
        )
        returns table(document_id uuid, team_id uuid, context_user_id uuid)
        language sql stable security definer set search_path = public as $$
          select candidate.document_id,
                 candidate.team_id,
                 coalesce(
                   case
                     when app_kb_permission(
                       candidate.knowledge_base_id,
                       candidate.uploaded_by_id
                     ) is not null
                     then candidate.uploaded_by_id
                   end,
                   case
                     when app_kb_permission(
                       candidate.knowledge_base_id,
                       candidate.owner_id
                     ) is not null
                     then candidate.owner_id
                   end,
                   (
                     select membership.user_id
                     from team_memberships membership
                     where membership.team_id = candidate.team_id
                       and app_kb_permission(
                         candidate.knowledge_base_id,
                         membership.user_id
                       ) is not null
                     order by
                       case membership.role
                         when 'admin' then 1
                         when 'member' then 2
                         else 3
                       end,
                       membership.created_at,
                       membership.user_id
                     limit 1
                   )
                 ) as context_user_id
          from (
            select document.id as document_id,
                   document.team_id,
                   document.knowledge_base_id,
                   document.uploaded_by_id,
                   knowledge_base.owner_id
            from documents document
            join knowledge_bases knowledge_base
              on knowledge_base.id = document.knowledge_base_id
            where document.status = 'uploading'
              and document.updated_at < cutoff
            order by document.created_at, document.id
            limit greatest(least(batch_limit, 1000), 1)
          ) candidate
        $$
        """
    )
    op.execute(
        "revoke all on function "
        "app_reconcile_upload_candidates(timestamptz, integer) from public"
    )
    op.execute(
        f"grant execute on function "
        f"app_reconcile_upload_candidates(timestamptz, integer) to {ROLE_NAME}"
    )

    # Existing team policies establish tenant isolation. These restrictive
    # policies add the narrower per-base boundary without weakening any
    # owner-only policy already present on personal artifacts. A new library is
    # inserted before its owner-access row, so the insert check admits a member
    # creating their own base; all child-row checks still require the complete
    # effective permission.
    op.execute(
        """
        create policy knowledge_bases_kb_access on knowledge_bases
        as restrictive
        using (
          team_id = app_current_team()
          and app_kb_permission(id, app_current_user()) is not null
        )
        with check (
          team_id = app_current_team()
          and (
            owner_id = app_current_user()
            or app_kb_permission(id, app_current_user()) is not null
          )
        )
        """
    )
    direct_kb_tables = (
        "documents",
        "document_folders",
        "conversations",
        "analysis_tasks",
        "analysis_fields",
        "analysis_documents",
        "analysis_field_results",
        "analysis_conclusions",
        "analysis_citations",
        "analysis_summaries",
        "knowledge_notes",
        "reports",
        "report_sources",
        "report_sections",
        "report_citations",
        "saved_requirements",
        "knowledge_automations",
        "knowledge_automation_steps",
        "knowledge_base_access",
    )
    for table in direct_kb_tables:
        op.execute(
            f"""
            create policy {table}_kb_access on {table}
            as restrictive
            using (
              team_id = app_current_team()
              and app_kb_permission(
                knowledge_base_id, app_current_user()
              ) is not null
            )
            with check (
              team_id = app_current_team()
              and app_kb_permission(
                knowledge_base_id, app_current_user()
              ) is not null
            )
            """
        )
    # These rows inherit their knowledge base through a parent row. They still
    # get a restrictive knowledge-base boundary so a missed API check cannot
    # reveal parsed text or private conversation evidence.
    inherited_kb_tables = (
        "document_chunks",
        "processing_tasks",
        "turns",
        "citations",
    )
    for table in inherited_kb_tables:
        if table == "document_chunks" or table == "processing_tasks":
            predicate = (
                "exists (select 1 from documents d where d.id = document_id "
                "and app_kb_permission(d.knowledge_base_id, app_current_user()) "
                "is not null)"
            )
        elif table == "turns":
            predicate = (
                "exists (select 1 from conversations c where c.id = conversation_id "
                "and app_kb_permission(c.knowledge_base_id, app_current_user()) "
                "is not null)"
            )
        else:
            predicate = (
                "exists (select 1 from turns t where t.id = turn_id "
                "and app_kb_permission(t.knowledge_base_id, app_current_user()) "
                "is not null)"
            )
        op.execute(
            f"""
            create policy {table}_kb_access on {table}
            as restrictive
            using (team_id = app_current_team() and {predicate})
            with check (team_id = app_current_team() and {predicate})
            """
        )


def downgrade() -> None:
    kb_scoped_tables = (
        "knowledge_base_access",
        "knowledge_automation_steps",
        "knowledge_automations",
        "saved_requirements",
        "report_citations",
        "report_sections",
        "report_sources",
        "reports",
        "knowledge_notes",
        "analysis_summaries",
        "analysis_citations",
        "analysis_conclusions",
        "analysis_field_results",
        "analysis_documents",
        "analysis_fields",
        "analysis_tasks",
        "citations",
        "turns",
        "conversations",
        "document_folders",
        "processing_tasks",
        "document_chunks",
        "documents",
    )
    for table in kb_scoped_tables:
        op.execute(f"drop policy if exists {table}_kb_access on {table}")
    op.execute(
        "drop policy if exists knowledge_bases_kb_access on knowledge_bases"
    )
    op.execute(
        f"revoke all on function "
        f"app_create_knowledge_base(uuid, uuid, text, text) from {ROLE_NAME}"
    )
    op.execute(
        "drop function if exists "
        "app_create_knowledge_base(uuid, uuid, text, text)"
    )
    op.execute(
        "drop function if exists "
        "app_revoke_inaccessible_knowledge_work()"
    )
    op.execute("drop function if exists app_claim_task(integer)")
    op.execute("drop function if exists app_reclaim_tasks()")
    op.execute(
        """
        create or replace function app_claim_task(lease_seconds integer)
        returns table(task_id uuid, team_id uuid, lease_token uuid)
        language sql security definer set search_path = public as $$
          with candidate as (
            select task.id
            from processing_tasks task
            join documents document on document.id = task.document_id
            join teams team on team.id = task.team_id
            left join users uploader on uploader.id = document.uploaded_by_id
            where task.status = 'queued'
              and task.attempts < task.max_attempts
              and document.status in ('pending', 'processing')
              and team.is_active
              and (document.uploaded_by_id is null or uploader.is_active)
            order by task.created_at, task.id
            for update of task skip locked
            limit 1
          )
          update processing_tasks target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.team_id, target.lease_token
        $$
        """
    )
    op.execute(
        "revoke all on function app_claim_task(integer) from public"
    )
    op.execute(f"grant execute on function app_claim_task(integer) to {ROLE_NAME}")
    op.execute(
        """
        create or replace function app_reclaim_tasks()
        returns table(task_id uuid, team_id uuid, exhausted boolean)
        language sql security definer set search_path = public as $$
          with expired as (
            select task.id, task.team_id,
                   task.attempts >= task.max_attempts as exhausted
            from processing_tasks task
            where task.status = 'running' and task.lease_expires_at < now()
            for update skip locked
          ),
          updated as (
            update processing_tasks target
            set status = case when expired.exhausted then 'failed' else 'queued' end,
                lease_expires_at = null,
                updated_at = now()
            from expired
            where target.id = expired.id
            returning target.id, target.team_id, expired.exhausted
          )
          select updated.id, updated.team_id, updated.exhausted
          from updated
        $$
        """
    )
    op.execute("revoke all on function app_reclaim_tasks() from public")
    op.execute(f"grant execute on function app_reclaim_tasks() to {ROLE_NAME}")
    op.execute(f"revoke all on function app_kb_permission(uuid, uuid) from {ROLE_NAME}")
    op.execute("drop function if exists app_kb_permission(uuid, uuid)")
    op.execute("drop policy if exists knowledge_base_access_team_scope on knowledge_base_access")
    op.execute("alter table knowledge_base_access disable row level security")
    op.drop_table("knowledge_base_access")
    op.drop_index("ix_knowledge_bases_owner_id", table_name="knowledge_bases")
    op.drop_constraint("ck_kb_team_permission", "knowledge_bases", type_="check")
    op.drop_constraint("ck_kb_visibility", "knowledge_bases", type_="check")
    op.drop_column("knowledge_bases", "team_permission")
    op.drop_column("knowledge_bases", "visibility")
    op.drop_column("knowledge_bases", "owner_id")
