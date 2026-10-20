"""V2 batch extraction and evidence-backed comparison summary

Revision ID: 0013_v2_batch_comparison
Revises: 0012_v2_analysis
Create Date: 2026-10-20
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_v2_batch_comparison"
down_revision: str | None = "0012_v2_analysis"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"非法的 RLS 角色名：{ROLE_NAME}")


def upgrade() -> None:
    # 汇总结果沿用一个逐文档执行行，以便复用其团队、创建者、任务与来源约束。
    op.create_unique_constraint(
        "uq_analysis_document_summary_scope",
        "analysis_documents",
        ["id", "team_id", "knowledge_base_id", "task_id", "owner_id"],
    )

    op.create_table(
        "analysis_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("provider", sa.String(40)),
        sa.Column("model", sa.String(120)),
        sa.Column("prompt_version", sa.String(40)),
        sa.Column("schema_version", sa.String(40)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail", sa.String(500)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
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
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="ck_analysis_summary_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0 and max_attempts > 0",
            name="ck_analysis_summary_counts",
        ),
        sa.UniqueConstraint("task_id", name="uq_analysis_summary_task"),
        sa.UniqueConstraint(
            "id",
            "team_id",
            "knowledge_base_id",
            "task_id",
            "owner_id",
            name="uq_analysis_summary_scope",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_summary_task_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "document_execution_id",
                "team_id",
                "knowledge_base_id",
                "task_id",
                "owner_id",
            ],
            [
                "analysis_documents.id",
                "analysis_documents.team_id",
                "analysis_documents.knowledge_base_id",
                "analysis_documents.task_id",
                "analysis_documents.owner_id",
            ],
            name="fk_analysis_summary_document_scope",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_analysis_summaries_task_id", "analysis_summaries", ["task_id"])
    op.create_index(
        "ix_analysis_summaries_claim",
        "analysis_summaries",
        ["status", "created_at"],
    )

    # 结论作为独立的持久目标，关联依据不能借字段结果的外键绕过归属检查。
    # analysis_summaries 必须在此引用检查之前创建；租约发布时再插入其行。
    # 既有 conclusion 外键已保证结论属于同任务/同一创建者；结论文本与
    # 汇总结果无额外列级依赖。汇总记录仍由任务复合外键约束。

    op.execute(f"grant select, insert, update, delete on analysis_summaries to {ROLE_NAME}")
    op.execute("alter table analysis_summaries enable row level security")
    op.execute(
        """
        create policy analysis_summaries_owner_scope on analysis_summaries
        using (team_id = app_current_team() and owner_id = app_current_user())
        with check (team_id = app_current_team() and owner_id = app_current_user())
        """
    )

    # 汇总领取与租约回收同样只向下暴露调度身份，不返回提取值或原文。
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
            select s.id
            from analysis_summaries s
            join analysis_tasks t on t.id = s.task_id
            where t.kind = 'comparison'
              and t.status = 'running'
              and t.stage = 'comparing'
              and s.status = 'queued'
              and s.attempts < s.max_attempts
            order by t.created_at, t.id
            for update of s skip locked
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
        create or replace function app_reclaim_analysis_summaries()
        returns table(
          summary_id uuid,
          task_id uuid,
          team_id uuid,
          owner_id uuid,
          exhausted boolean
        )
        language sql security definer set search_path = public as $$
          with expired as (
            select s.id, s.task_id, s.team_id, s.owner_id,
                   s.attempts >= s.max_attempts as exhausted
            from analysis_summaries s
            where s.status = 'running'
              and s.lease_expires_at < now()
            for update of s skip locked
          ),
          updated as (
            update analysis_summaries target
            set status = case when e.exhausted then 'failed' else 'queued' end,
                lease_token = null,
                lease_expires_at = null,
                error_code = case when e.exhausted then 'ANALYSIS_EXHAUSTED' else null end,
                finished_at = case when e.exhausted then now() else target.finished_at end,
                updated_at = now()
            from expired e
            where target.id = e.id
            returning target.id, target.task_id, target.team_id, target.owner_id,
                      e.exhausted
          )
          select * from updated
        $$
        """
    )
    for signature in (
        "app_claim_analysis_summary(integer)",
        "app_reclaim_analysis_summaries()",
    ):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")


def downgrade() -> None:
    for signature in (
        "app_claim_analysis_summary(integer)",
        "app_reclaim_analysis_summaries()",
    ):
        op.execute(f"revoke all on function {signature} from {ROLE_NAME}")
        op.execute(f"drop function if exists {signature}")
    op.drop_index("ix_analysis_summaries_claim", table_name="analysis_summaries")
    op.drop_index("ix_analysis_summaries_task_id", table_name="analysis_summaries")
    op.drop_table("analysis_summaries")
    op.drop_constraint(
        "uq_analysis_document_summary_scope", "analysis_documents", type_="unique"
    )
