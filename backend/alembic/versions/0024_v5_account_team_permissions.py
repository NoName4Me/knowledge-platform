"""V5 account, team and role management

Revision ID: 0024_v5_accounts
Revises: 0023_table_citation_structure
Create Date: 2026-10-22
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0024_v5_accounts"
down_revision: str | None = "0023_table_citation_structure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"non-identifier RLS role: {ROLE_NAME}")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("teams", sa.Column("description", sa.String(500)))
    op.add_column(
        "teams",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column("teams", sa.Column("disabled_at", sa.DateTime(timezone=True)))

    op.drop_constraint("ck_membership_role", "team_memberships", type_="check")
    op.create_check_constraint(
        "ck_membership_role",
        "team_memberships",
        "role in ('admin', 'member', 'viewer')",
    )
    op.drop_constraint("ck_invitation_role", "team_invitations", type_="check")
    op.create_check_constraint(
        "ck_invitation_role",
        "team_invitations",
        "role in ('admin', 'member', 'viewer')",
    )

    op.execute(
        """
        create or replace view my_teams with (security_invoker = false) as
        select t.id, t.name, t.description, t.is_active
        from teams t
        where t.id = app_current_team()
           or exists (
             select 1 from team_memberships m
             where m.team_id = t.id and m.user_id = app_current_user()
           )
        """
    )
    op.create_table(
        "management_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("teams.id", ondelete="SET NULL"), index=True),
        sa.Column("target_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("action", sa.String(80), nullable=False, index=True),
        sa.Column("request_id", sa.String(100)),
        sa.Column(
            "details",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        f"grant select, insert, update, delete on management_events to {ROLE_NAME}"
    )
    op.execute("alter table management_events enable row level security")
    op.execute(
        """
        create policy management_events_scope on management_events
        using (
          (team_id is not null and team_id = app_current_team())
          or (team_id is null and actor_user_id = app_current_user())
        )
        with check (
          (team_id is not null and team_id = app_current_team())
          or (team_id is null and actor_user_id = app_current_user())
        )
        """
    )

    op.execute(
        f"grant select (id, email, display_name, is_active, created_at) on users to {ROLE_NAME}"
    )
    op.execute(
        f"grant select (id, name, description, is_active, created_at) on teams to {ROLE_NAME}"
    )
    op.execute(
        "grant update (display_name, password_hash, must_change_password, is_active) "
        f"on users to {ROLE_NAME}"
    )
    op.execute(
        f"grant update (name, description, is_active, disabled_at) on teams to {ROLE_NAME}"
    )
    op.execute(
        f"grant select, insert, update, delete on team_memberships to {ROLE_NAME}"
    )

    op.execute(
        """
        create or replace function app_is_platform_admin() returns boolean
        language sql stable security definer set search_path = public as $$
          select exists(
            select 1 from users
            where id = app_current_user() and is_platform_admin and is_active
          )
        $$
        """
    )
    op.execute(
        f"grant execute on function app_is_platform_admin() to {ROLE_NAME}"
    )
    op.execute(
        """
        create or replace function app_can_access_team(
          target_team uuid, target_user uuid
        ) returns boolean
        language sql stable security definer set search_path = public as $$
          select exists(
            select 1
            from team_memberships m
            join teams t on t.id = m.team_id
            join users u on u.id = m.user_id
            where m.team_id = target_team
              and m.user_id = target_user
              and t.is_active
              and u.is_active
          )
        $$
        """
    )
    op.execute(
        "revoke all on function app_can_access_team(uuid, uuid) from public"
    )
    op.execute(
        f"grant execute on function app_can_access_team(uuid, uuid) to {ROLE_NAME}"
    )

    # Platform reads expose account/team metadata only. Private business tables remain
    # protected by their existing team and owner policies.
    op.execute(
        """
        create or replace function app_platform_teams()
        returns table (
          id uuid,
          name text,
          description text,
          is_active boolean,
          created_at timestamptz,
          administrator_count bigint,
          pending_administrator_invitation_count bigint
        )
        language sql stable security definer set search_path = public as $$
          select t.id, t.name, t.description, t.is_active, t.created_at,
                 (
                   select count(*)
                   from team_memberships m
                   join users u on u.id = m.user_id
                   where m.team_id = t.id
                     and m.role = 'admin'
                     and u.is_active
                 ) as administrator_count,
                 (
                   select count(*)
                   from team_invitations i
                   where i.team_id = t.id
                     and i.role = 'admin'
                     and i.accepted_at is null
                     and i.expires_at > now()
                 ) as pending_administrator_invitation_count
            from teams t
           where app_is_platform_admin()
           order by t.created_at desc, t.name
        $$
        """
    )
    op.execute(
        """
        create or replace function app_platform_users()
        returns table (
          id uuid,
          email text,
          display_name text,
          is_active boolean,
          must_change_password boolean,
          created_at timestamptz
        )
        language sql stable security definer set search_path = public as $$
          select u.id, u.email, u.display_name, u.is_active,
                 u.must_change_password, u.created_at
            from users u
           where app_is_platform_admin()
           order by u.email
        $$
        """
    )
    # Worker claim functions must stop selecting work whose owner has been
    # disabled or removed from the team. They still expose only scheduling IDs.
    op.execute("drop function app_claim_task(integer)")
    op.execute("drop function app_claim_analysis_document(integer)")
    op.execute("drop function app_claim_analysis_summary(integer)")
    op.execute("drop function app_claim_knowledge_automation(integer)")
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
        """
        create or replace function app_claim_analysis_document(lease_seconds integer)
        returns table(
          document_execution_id uuid,
          task_id uuid,
          team_id uuid,
          owner_id uuid,
          document_id uuid,
          lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select execution.id
            from analysis_documents execution
            join analysis_tasks task on task.id = execution.task_id
            join teams team on team.id = execution.team_id
            join users owner_user on owner_user.id = execution.owner_id
            where task.status in ('queued', 'running')
              and task.stage in ('queued', 'extracting')
              and execution.status = 'queued'
              and execution.attempts < execution.max_attempts
              and team.is_active
              and owner_user.is_active
              and exists (
                select 1
                from team_memberships membership
                where membership.team_id = execution.team_id
                  and membership.user_id = execution.owner_id
              )
              and not exists (
                select 1
                from analysis_documents prior
                where prior.task_id = execution.task_id
                  and prior.ordinal < execution.ordinal
                  and prior.status in ('queued', 'running')
              )
            order by task.created_at, task.id, execution.ordinal
            for update of execution skip locked
            limit 1
          )
          update analysis_documents target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.task_id, target.team_id, target.owner_id,
                    target.document_id, target.lease_token
        $$
        """
    )
    op.execute(
        """
        create or replace function app_claim_analysis_summary(lease_seconds integer)
        returns table(
          summary_id uuid,
          task_id uuid,
          team_id uuid,
          owner_id uuid,
          lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select summary.id
            from analysis_summaries summary
            join analysis_tasks task on task.id = summary.task_id
            join teams team on team.id = summary.team_id
            join users owner_user on owner_user.id = summary.owner_id
            where task.kind = 'comparison'
              and task.status = 'running'
              and task.stage = 'comparing'
              and summary.status = 'queued'
              and summary.attempts < summary.max_attempts
              and team.is_active
              and owner_user.is_active
              and exists (
                select 1
                from team_memberships membership
                where membership.team_id = summary.team_id
                  and membership.user_id = summary.owner_id
              )
            order by task.created_at, task.id
            for update of summary skip locked
            limit 1
          )
          update analysis_summaries target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.task_id, target.team_id, target.owner_id,
                    target.lease_token
        $$
        """
    )
    op.execute(
        """
        create or replace function app_claim_knowledge_automation(lease_seconds integer)
        returns table(
          automation_id uuid, team_id uuid, owner_id uuid, lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select automation.id
            from knowledge_automations automation
            join teams team on team.id = automation.team_id
            join users owner_user on owner_user.id = automation.created_by_id
            where automation.status in ('queued', 'running')
              and automation.stage <> 'canceling'
              and automation.attempts < automation.max_attempts
              and team.is_active
              and owner_user.is_active
              and exists (
                select 1
                from team_memberships membership
                where membership.team_id = automation.team_id
                  and membership.user_id = automation.created_by_id
              )
              and not exists (
                select 1
                from analysis_tasks task
                where task.id = automation.analysis_id
                  and task.status in ('queued', 'running')
              )
              and (
                automation.status = 'queued'
                or (
                  automation.status = 'running'
                  and automation.lease_token is not null
                  and automation.lease_expires_at < now()
                )
              )
            order by automation.created_at, automation.id
            for update skip locked
            limit 1
          )
          update knowledge_automations target
          set status = 'running',
              attempts = target.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              started_at = coalesce(target.started_at, now()),
              updated_at = now()
          from candidate c
          where target.id = c.id
          returning target.id, target.team_id, target.created_by_id, target.lease_token
        $$
        """
    )
    for signature in (
        "app_claim_task(integer)",
        "app_claim_analysis_document(integer)",
        "app_claim_analysis_summary(integer)",
        "app_claim_knowledge_automation(integer)",
    ):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")
    for signature in ("app_platform_teams()", "app_platform_users()"):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")
    op.execute(
        """
        create or replace function app_record_platform_team_event(
          event_action text, actor uuid, target_team uuid
        ) returns void
        language plpgsql security definer set search_path = public as $$
        begin
          if not app_is_platform_admin() or actor <> app_current_user() then
            raise exception 'platform administrator required';
          end if;
          insert into management_events (id, actor_user_id, team_id, action, details)
          values (gen_random_uuid(), actor, target_team, event_action, '{}'::json);
        end
        $$
        """
    )
    op.execute(
        """
        create or replace function app_record_platform_team_created(
          target_team uuid, actor uuid
        ) returns void
        language plpgsql security definer set search_path = public as $$
        begin
          if not app_is_platform_admin() or actor <> app_current_user() then
            raise exception 'platform administrator required';
          end if;
          insert into management_events (id, actor_user_id, team_id, action, details)
          values (gen_random_uuid(), actor, target_team, 'team.created', '{}'::jsonb);
        end
        $$
        """
    )
    op.execute(
        """
        create or replace function app_record_platform_account_event(
          event_action text, actor uuid, target_account uuid
        ) returns void
        language plpgsql security definer set search_path = public as $$
        begin
          if not app_is_platform_admin() or actor <> app_current_user() then
            raise exception 'platform administrator required';
          end if;
          insert into management_events
            (id, actor_user_id, target_user_id, action, details)
          values (gen_random_uuid(), actor, target_account, event_action, '{}'::jsonb);
        end
        $$
        """
    )
    op.execute(
        """
        create or replace function app_create_platform_team(
          team_name text,
          team_description text,
          administrator_email text,
          invitation_token_hash text,
          invitation_expires_at timestamptz
        )
        returns table(team_id uuid, administrator_status text)
        language plpgsql security definer set search_path = public as $$
        declare
          new_team_id uuid := gen_random_uuid();
          target_user_id uuid;
          target_user_active boolean;
          actor_id uuid := app_current_user();
        begin
          if not app_is_platform_admin() then
            raise exception 'platform administrator required';
          end if;
          if nullif(trim(team_name), '') is null then
            raise exception 'team name required';
          end if;
          if nullif(trim(administrator_email), '') is null then
            raise exception 'administrator email required';
          end if;

          insert into teams (id, name, description, is_active)
          values (new_team_id, trim(team_name), nullif(trim(team_description), ''), true);

          target_user_id := null;
          target_user_active := null;
          select id, is_active into target_user_id, target_user_active
            from users
           where lower(email) = lower(trim(administrator_email))
           for update;

          if target_user_id is not null and not target_user_active then
            raise exception 'administrator account disabled';
          end if;

          if target_user_id is not null then
            insert into team_memberships (id, team_id, user_id, role)
            values (gen_random_uuid(), new_team_id, target_user_id, 'admin')
            on conflict on constraint uq_team_membership
            do update set role = 'admin';

            insert into management_events
              (id, actor_user_id, team_id, target_user_id, action, details)
            values (
              gen_random_uuid(), actor_id, new_team_id, target_user_id,
              'team.created', jsonb_build_object('administrator', 'joined')
            );
            return query select new_team_id, 'joined'::text;
          else
            update team_invitations
               set expires_at = now()
             where team_invitations.team_id = new_team_id
               and role = 'admin'
               and accepted_at is null
               and expires_at > now();

            insert into team_invitations
              (id, team_id, email, role, token_hash, invited_by_id, expires_at)
            values (
              gen_random_uuid(), new_team_id, lower(trim(administrator_email)), 'admin',
              invitation_token_hash, actor_id, invitation_expires_at
            );

            insert into management_events
              (id, actor_user_id, team_id, action, details)
            values (
              gen_random_uuid(), actor_id, new_team_id, 'team.created',
              jsonb_build_object('administrator', 'invited')
            );
            return query select new_team_id, 'invited'::text;
          end if;
        end
        $$
        """
    )
    op.execute(
        """
        create or replace function app_platform_assign_team_admin(
          target_team uuid,
          administrator_email text,
          invitation_token_hash text,
          invitation_expires_at timestamptz
        )
        returns table(status text)
        language plpgsql security definer set search_path = public as $$
        declare
          target_user_id uuid;
          target_user_active boolean;
          actor_id uuid := app_current_user();
        begin
          if not app_is_platform_admin() then
            raise exception 'platform administrator required';
          end if;
          if nullif(trim(administrator_email), '') is null then
            raise exception 'administrator email required';
          end if;

          perform 1 from teams where id = target_team for update;
          if not found then
            return query select 'not_found'::text;
            return;
          end if;

          target_user_id := null;
          target_user_active := null;
          select id, is_active into target_user_id, target_user_active
            from users
           where lower(email) = lower(trim(administrator_email))
           for update;
          if target_user_id is not null and not target_user_active then
            return query select 'account_disabled'::text;
            return;
          end if;

          if target_user_id is not null then
            insert into team_memberships (id, team_id, user_id, role)
            values (gen_random_uuid(), target_team, target_user_id, 'admin')
            on conflict on constraint uq_team_membership
            do update set role = 'admin';

            insert into management_events
              (id, actor_user_id, team_id, target_user_id, action, details)
            values (
              gen_random_uuid(), actor_id, target_team, target_user_id,
              'team.administrator_assigned', '{}'::jsonb
            );
            return query select 'joined'::text;
          else
            update team_invitations
               set expires_at = now()
             where team_invitations.team_id = target_team
               and role = 'admin'
               and accepted_at is null
               and expires_at > now();

            insert into team_invitations
              (id, team_id, email, role, token_hash, invited_by_id, expires_at)
            values (
              gen_random_uuid(), target_team, lower(trim(administrator_email)), 'admin',
              invitation_token_hash, actor_id, invitation_expires_at
            );
            insert into management_events
              (id, actor_user_id, team_id, action, details)
            values (
              gen_random_uuid(), actor_id, target_team,
              'team.administrator_invited',
              jsonb_build_object('email', lower(trim(administrator_email)))
            );
            return query select 'invited'::text;
          end if;
        end
        $$
        """
    )
    op.execute(
        """
        create or replace function app_platform_set_team_active(
          target_team uuid, desired_active boolean
        )
        returns table(status text, is_active boolean)
        language plpgsql security definer set search_path = public as $$
        declare
          actor_id uuid := app_current_user();
        begin
          if not app_is_platform_admin() then
            raise exception 'platform administrator required';
          end if;
          perform 1 from teams where id = target_team for update;
          if not found then
            return query select 'not_found'::text, null::boolean;
            return;
          end if;
          update teams
             set is_active = desired_active,
                 disabled_at = case when desired_active then null else now() end
           where id = target_team;
          insert into management_events (id, actor_user_id, team_id, action, details)
          values (
            gen_random_uuid(), actor_id, target_team,
            case when desired_active then 'team.enabled' else 'team.disabled' end,
            '{}'::jsonb
          );
          return query select 'ok'::text, desired_active;
        end
        $$
        """
    )
    op.execute(
        """
        create or replace function app_platform_set_account_active(
          target_account uuid, desired_active boolean
        )
        returns table(status text, blocking_teams text[])
        language plpgsql security definer set search_path = public as $$
        declare
          actor_id uuid := app_current_user();
          candidate record;
          blockers text[] := array[]::text[];
        begin
          if not app_is_platform_admin() then
            raise exception 'platform administrator required';
          end if;
          perform 1 from users where id = target_account for update;
          if not found then
            return query select 'not_found'::text, null::text[];
            return;
          end if;

          if not desired_active then
            for candidate in
              select t.id, t.name
                from teams t
                join team_memberships m on m.team_id = t.id
               where m.user_id = target_account
                 and m.role = 'admin'
                 and t.is_active
               order by t.id
               for update of t
            loop
              if not exists (
                select 1
                  from team_memberships other
                  join users other_user on other_user.id = other.user_id
                 where other.team_id = candidate.id
                   and other.user_id <> target_account
                   and other.role = 'admin'
                   and other_user.is_active
              ) then
                blockers := array_append(blockers, candidate.name);
              end if;
            end loop;

            if cardinality(blockers) > 0 then
              return query select 'blocked'::text, blockers;
              return;
            end if;
          end if;

          update users set is_active = desired_active where id = target_account;
          if not desired_active then
            delete from session_tokens where user_id = target_account;
          end if;
          insert into management_events
            (id, actor_user_id, target_user_id, action, details)
          values (
            gen_random_uuid(), actor_id, target_account,
            case when desired_active then 'account.enabled' else 'account.disabled' end,
            '{}'::jsonb
          );
          return query select 'ok'::text, array[]::text[];
        end
        $$
        """
    )
    for signature in (
        "app_record_platform_team_event(text, uuid, uuid)",
        "app_record_platform_team_created(uuid, uuid)",
        "app_record_platform_account_event(text, uuid, uuid)",
        "app_create_platform_team(text, text, text, text, timestamptz)",
        "app_platform_assign_team_admin(uuid, text, text, timestamptz)",
        "app_platform_set_team_active(uuid, boolean)",
        "app_platform_set_account_active(uuid, boolean)",
    ):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")

    # The invitation preview must return the exact address that the browser should
    # prefill and lock. The redeem function still enforces email equality server-side.
    op.execute(
        """
        create or replace function app_preview_invitation(invitation_token_hash text)
        returns table (status text, team_name text, email text)
        language sql stable security definer set search_path = public as $$
          select case
                   when i.accepted_at is not null then 'used'
                   when i.expires_at <= now() or not t.is_active then 'expired'
                   else 'ok'
                 end,
                 t.name,
                 i.email
          from team_invitations i
          join teams t on t.id = i.team_id
          where i.token_hash = invitation_token_hash
        $$
        """
    )
    op.execute(
        """
        create or replace function app_accept_invitation(
          invitation_token_hash text,
          target_user uuid,
          target_email text
        )
        returns table (status text, team_id uuid, team_name text, role text)
        language plpgsql security definer set search_path = public as $$
        declare
          invitation team_invitations%rowtype;
          joined_role text;
          active_team boolean;
        begin
          select * into invitation
            from team_invitations
           where token_hash = invitation_token_hash
           for update;
          if not found then
            return query select 'not_found'::text, null::uuid, null::text, null::text;
            return;
          end if;
          if invitation.accepted_at is not null then
            return query select 'used'::text, null::uuid, null::text, null::text;
            return;
          end if;
          if invitation.expires_at <= now() then
            return query select 'expired'::text, null::uuid, null::text, null::text;
            return;
          end if;
          if lower(invitation.email) <> lower(target_email) then
            return query select 'email_mismatch'::text, null::uuid, null::text, null::text;
            return;
          end if;
          select t.is_active into active_team from teams t where t.id = invitation.team_id;
          if not coalesce(active_team, false) then
            return query select 'expired'::text, null::uuid, null::text, null::text;
            return;
          end if;
          if not exists (
            select 1 from users u where u.id = target_user and u.is_active
          ) then
            return query select 'account_disabled'::text, null::uuid, null::text, null::text;
            return;
          end if;
          insert into team_memberships (id, team_id, user_id, role)
          values (gen_random_uuid(), invitation.team_id, target_user, invitation.role)
          on conflict on constraint uq_team_membership do nothing;
          update team_invitations
             set accepted_at = now(), accepted_by_id = target_user
           where id = invitation.id;
          select m.role into joined_role
            from team_memberships m
           where m.team_id = invitation.team_id and m.user_id = target_user;
          return query
            select 'ok'::text, invitation.team_id, t.name::text, joined_role::text
              from teams t where t.id = invitation.team_id;
        end
        $$
        """
    )
    op.execute(
        f"grant execute on function app_accept_invitation(text, uuid, text) to {ROLE_NAME}"
    )
    op.execute(
        f"grant execute on function app_preview_invitation(text) to {ROLE_NAME}"
    )


def downgrade() -> None:
    for signature in (
        "app_record_platform_team_event(text, uuid, uuid)",
        "app_record_platform_team_created(uuid, uuid)",
        "app_record_platform_account_event(text, uuid, uuid)",
        "app_create_platform_team(text, text, text, text, timestamptz)",
        "app_platform_assign_team_admin(uuid, text, text, timestamptz)",
        "app_platform_set_team_active(uuid, boolean)",
        "app_platform_set_account_active(uuid, boolean)",
    ):
        op.execute(f"drop function if exists {signature}")
    op.execute("drop function if exists app_platform_users()")
    op.execute("drop function if exists app_platform_teams()")
    op.execute("drop function if exists app_can_access_team(uuid, uuid)")
    op.execute("drop function if exists app_is_platform_admin()")
    op.execute("drop policy if exists management_events_scope on management_events")
    op.drop_table("management_events")

    # Revert only rows using the V5 role; existing admin/member data remains valid.
    op.execute("delete from team_invitations where role = 'viewer'")
    op.execute("delete from team_memberships where role = 'viewer'")
    op.drop_constraint("ck_invitation_role", "team_invitations", type_="check")
    op.create_check_constraint(
        "ck_invitation_role",
        "team_invitations",
        "role in ('admin', 'member')",
    )
    op.drop_constraint("ck_membership_role", "team_memberships", type_="check")
    op.create_check_constraint(
        "ck_membership_role",
        "team_memberships",
        "role in ('admin', 'member')",
    )
    op.drop_column("teams", "disabled_at")
    op.drop_column("teams", "is_active")
    op.drop_column("teams", "description")
    op.drop_column("users", "must_change_password")
    op.drop_column("users", "is_platform_admin")
