"""V4 durable analysis-to-report automation

Revision ID: 0022_v4_report_automation
Revises: 0021_tree_sort_order
Create Date: 2026-10-21
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0022_v4_report_automation"
down_revision: str | None = "0021_tree_sort_order"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"非法 RLS 角色名：{ROLE_NAME}")


def upgrade() -> None:
    op.create_table(
        "knowledge_automations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("analysis_title", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("analysis_kind", sa.String(20), nullable=False),
        sa.Column("document_ids", postgresql.JSONB(), nullable=False),
        sa.Column("fields", postgresql.JSONB(), nullable=False),
        sa.Column("report_title", sa.String(200), nullable=False),
        sa.Column("report_purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("report_focus", sa.Text(), nullable=False, server_default=""),
        sa.Column("report_length", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("analysis_id", postgresql.UUID(as_uuid=True)),
        sa.Column("report_id", postgresql.UUID(as_uuid=True)),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("continue_requested_at", sa.DateTime(timezone=True)),
        sa.Column(
            "cancel_requested_after_continue",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail", sa.String(500)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'completed', 'failed', 'canceled', 'invalidated')",
            name="ck_automation_status",
        ),
        sa.CheckConstraint(
            "stage in ('queued', 'analysis', 'report', 'canceling', 'finished')",
            name="ck_automation_stage",
        ),
        sa.CheckConstraint(
            "analysis_kind in ('extraction', 'comparison')",
            name="ck_automation_analysis_kind",
        ),
        sa.CheckConstraint(
            "report_length in ('short', 'medium', 'long')",
            name="ck_automation_report_length",
        ),
        sa.CheckConstraint("document_count > 0", name="ck_automation_document_count"),
        sa.CheckConstraint("attempts >= 0 and max_attempts > 0", name="ck_automation_attempts"),
        sa.UniqueConstraint(
            "team_id",
            "created_by_id",
            "idempotency_key",
            name="uq_automation_idempotency",
        ),
        sa.UniqueConstraint(
            "team_id",
            "knowledge_base_id",
            "id",
            "created_by_id",
            name="uq_automation_owner_scope",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "team_id"],
            ["knowledge_bases.id", "knowledge_bases.team_id"],
            name="fk_automation_kb_team",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "analysis_id", "created_by_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_automation_analysis_owner",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "report_id", "created_by_id"],
            [
                "reports.team_id",
                "reports.knowledge_base_id",
                "reports.id",
                "reports.created_by_id",
            ],
            name="fk_automation_report_owner",
        ),
    )
    for column in (
        "team_id",
        "knowledge_base_id",
        "created_by_id",
        "status",
        "analysis_id",
        "report_id",
    ):
        op.create_index(f"ix_knowledge_automations_{column}", "knowledge_automations", [column])

    op.create_table(
        "knowledge_automation_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("automation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("request_id", sa.String(80)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("detail", sa.String(500)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "kind in ('start', 'analysis', 'report', 'cancel', 'continue')",
            name="ck_automation_step_kind",
        ),
        sa.CheckConstraint(
            "status in ('started', 'succeeded', 'failed', 'canceled')",
            name="ck_automation_step_status",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "automation_id", "owner_id"],
            [
                "knowledge_automations.team_id",
                "knowledge_automations.knowledge_base_id",
                "knowledge_automations.id",
                "knowledge_automations.created_by_id",
            ],
            name="fk_automation_step_owner",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_knowledge_automation_steps_automation_id",
        "knowledge_automation_steps",
        ["automation_id"],
    )

    for table in ("knowledge_automations", "knowledge_automation_steps"):
        op.execute(f"grant select, insert, update, delete on {table} to {ROLE_NAME}")
        op.execute(f"alter table {table} enable row level security")
        owner_column = "created_by_id" if table == "knowledge_automations" else "owner_id"
        op.execute(
            f"""
            create policy {table}_owner_scope on {table}
            using (team_id = app_current_team() and {owner_column} = app_current_user())
            with check (team_id = app_current_team() and {owner_column} = app_current_user())
            """
        )

    # 跨团队 worker 只领取执行所需身份；不开放私人字段、提示词或成果正文。
    op.execute(
        """
        create or replace function app_claim_knowledge_automation(lease_seconds integer)
        returns table(
          automation_id uuid, team_id uuid, owner_id uuid, lease_token uuid
        )
        language sql security definer set search_path = public as $$
          with candidate as (
            select a.id
            from knowledge_automations a
            where a.status in ('queued', 'running')
              and a.stage <> 'canceling'
              and a.attempts < a.max_attempts
              and not exists (
                select 1
                from analysis_tasks task
                where task.id = a.analysis_id
                  and task.status in ('queued', 'running')
              )
              and (
                a.status = 'queued'
                or (
                  a.status = 'running'
                  and a.lease_token is not null
                  and a.lease_expires_at < now()
                )
              )
            order by a.created_at, a.id
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
    for signature in ("app_claim_knowledge_automation(integer)",):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")


def downgrade() -> None:
    op.execute(
        f"revoke all on function app_claim_knowledge_automation(integer) from {ROLE_NAME}"
    )
    op.execute("drop function if exists app_claim_knowledge_automation(integer)")
    for table in ("knowledge_automation_steps", "knowledge_automations"):
        op.execute(f"drop policy if exists {table}_owner_scope on {table}")
        op.execute(f"alter table {table} disable row level security")
        op.execute(f"revoke all on {table} from {ROLE_NAME}")
    op.drop_table("knowledge_automation_steps")
    op.drop_table("knowledge_automations")
