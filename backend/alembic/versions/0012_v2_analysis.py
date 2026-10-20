"""V2 owner-scoped analysis tasks and evidence-backed results

Revision ID: 0012_v2_analysis
Revises: 0011_team_invitations
Create Date: 2026-10-20
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_v2_analysis"
down_revision: str | None = "0011_team_invitations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"非法的 RLS 角色名：{ROLE_NAME}")


def upgrade() -> None:
    op.create_table(
        "analysis_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_task_id", postgresql.UUID(as_uuid=True)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail", sa.String(500)),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("completed_document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind in ('extraction', 'comparison')", name="ck_analysis_kind"),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'completed', 'partial', 'failed', 'invalidated')",
            name="ck_analysis_status",
        ),
        sa.CheckConstraint(
            "stage in ('queued', 'extracting', 'comparing', 'finished')",
            name="ck_analysis_stage",
        ),
        sa.CheckConstraint("document_count > 0", name="ck_analysis_document_count"),
        sa.CheckConstraint(
            "completed_document_count >= 0 and failed_document_count >= 0",
            name="ck_analysis_document_counts",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "source_task_id", "created_by_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_source_task_owner",
        ),
        sa.UniqueConstraint(
            "team_id",
            "created_by_id",
            "idempotency_key",
            name="uq_analysis_idempotency",
        ),
        sa.UniqueConstraint(
            "team_id",
            "knowledge_base_id",
            "id",
            "created_by_id",
            name="uq_analysis_task_owner_scope",
        ),
    )
    op.create_index("ix_analysis_tasks_team_id", "analysis_tasks", ["team_id"])
    op.create_index("ix_analysis_tasks_knowledge_base_id", "analysis_tasks", ["knowledge_base_id"])
    op.create_index("ix_analysis_tasks_created_by_id", "analysis_tasks", ["created_by_id"])
    op.create_index("ix_analysis_tasks_status", "analysis_tasks", ["status"])

    op.create_table(
        "analysis_fields",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("value_type", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "value_type in ('text', 'number', 'date', 'text_list')",
            name="ck_analysis_field_type",
        ),
        sa.UniqueConstraint("task_id", "ordinal", name="uq_analysis_field_ordinal"),
        # 供字段结果与对比结论按 (id, task_id) 复合引用，不能跨任务挂接。
        sa.UniqueConstraint("id", "task_id", name="uq_analysis_field_id_task"),
        sa.UniqueConstraint(
            "id",
            "team_id",
            "knowledge_base_id",
            "task_id",
            "owner_id",
            name="uq_analysis_field_full_scope",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_field_task_owner",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_analysis_fields_task_id", "analysis_fields", ["task_id"])

    op.create_table(
        "analysis_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("source_fingerprint", sa.String(64)),
        sa.Column("expected_chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"),
        # 记录实际使用的供应商与模型，替身与真实模型的证据不能混淆。
        sa.Column("provider", sa.String(40)),
        sa.Column("model", sa.String(120)),
        sa.Column("prompt_version", sa.String(40)),
        sa.Column("schema_version", sa.String(40)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail", sa.String(500)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="ck_analysis_document_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0 and max_attempts > 0 and processed_chunk_count >= 0 "
            "and expected_chunk_count >= 0 and model_calls >= 0",
            name="ck_analysis_document_counts",
        ),
        sa.UniqueConstraint("task_id", "document_id", name="uq_analysis_document"),
        sa.UniqueConstraint("task_id", "ordinal", name="uq_analysis_document_ordinal"),
        # 供字段结果与依据按 (id, task_id) 复合引用，不能跨任务挂接。
        sa.UniqueConstraint("id", "task_id", name="uq_analysis_document_id_task"),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_document_task_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "team_id"],
            ["documents.id", "documents.team_id"],
            name="fk_analysis_document_source_team",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_analysis_documents_task_id", "analysis_documents", ["task_id"])
    op.create_index("ix_analysis_documents_document_id", "analysis_documents", ["document_id"])
    op.create_index(
        "ix_analysis_documents_claim",
        "analysis_documents",
        ["status", "created_at"],
    )

    op.create_table(
        "analysis_field_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("value_text", sa.Text()),
        sa.Column("value_number", sa.Numeric(30, 10)),
        sa.Column("value_date", sa.Date()),
        sa.Column("value_list", postgresql.JSONB()),
        sa.Column("raw_value", sa.Text()),
        sa.Column("unit", sa.String(40)),
        sa.Column("date_precision", sa.String(20)),
        sa.Column("note", sa.String(1000)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status in ('extracted', 'not_mentioned', 'needs_review', 'failed')",
            name="ck_analysis_result_status",
        ),
        sa.UniqueConstraint("document_execution_id", "field_id", name="uq_analysis_field_result"),
        sa.UniqueConstraint(
            "id",
            "team_id",
            "knowledge_base_id",
            "task_id",
            "owner_id",
            name="uq_analysis_result_scope",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_result_task_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_execution_id", "task_id"],
            ["analysis_documents.id", "analysis_documents.task_id"],
            name="fk_analysis_result_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["field_id", "task_id"],
            ["analysis_fields.id", "analysis_fields.task_id"],
            name="fk_analysis_result_field",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_analysis_field_results_document_execution_id",
        "analysis_field_results",
        ["document_execution_id"],
    )

    op.create_unique_constraint(
        "uq_chunk_id_document_team",
        "document_chunks",
        ["id", "document_id", "team_id"],
    )
    op.create_table(
        "analysis_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_result_id", postgresql.UUID(as_uuid=True)),
        sa.Column("conclusion_id", postgresql.UUID(as_uuid=True)),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("locator_label", sa.String(200), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "(field_result_id is not null) <> (conclusion_id is not null)",
            name="ck_analysis_citation_one_target",
        ),
        sa.UniqueConstraint(
            "field_result_id", "ordinal", name="uq_analysis_field_citation_ordinal"
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_citation_task_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_execution_id", "task_id"],
            ["analysis_documents.id", "analysis_documents.task_id"],
            name="fk_analysis_citation_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "field_result_id",
                "team_id",
                "knowledge_base_id",
                "task_id",
                "owner_id",
            ],
            [
                "analysis_field_results.id",
                "analysis_field_results.team_id",
                "analysis_field_results.knowledge_base_id",
                "analysis_field_results.task_id",
                "analysis_field_results.owner_id",
            ],
            name="fk_analysis_citation_result",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id", "document_id", "team_id"],
            ["document_chunks.id", "document_chunks.document_id", "document_chunks.team_id"],
            name="fk_analysis_citation_chunk_team",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_analysis_citations_task_id", "analysis_citations", ["task_id"])
    op.create_index("ix_analysis_citations_document_id", "analysis_citations", ["document_id"])

    op.create_table(
        "analysis_conclusions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("document_ids", postgresql.JSONB(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind in ('common', 'difference', 'needs_review')",
            name="ck_analysis_conclusion_kind",
        ),
        sa.UniqueConstraint("task_id", "ordinal", name="uq_analysis_conclusion_ordinal"),
        sa.UniqueConstraint(
            "id",
            "team_id",
            "knowledge_base_id",
            "task_id",
            "owner_id",
            name="uq_analysis_conclusion_scope",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_conclusion_task_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            [
                "field_id",
                "team_id",
                "knowledge_base_id",
                "task_id",
                "owner_id",
            ],
            [
                "analysis_fields.id",
                "analysis_fields.team_id",
                "analysis_fields.knowledge_base_id",
                "analysis_fields.task_id",
                "analysis_fields.owner_id",
            ],
            name="fk_analysis_conclusion_field",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_analysis_conclusions_task_id", "analysis_conclusions", ["task_id"])

    # 结论也是分析产物：依据若指向结论，必须指向同一任务、同一拥有者的结论。
    op.create_foreign_key(
        "fk_analysis_citation_conclusion",
        "analysis_citations",
        "analysis_conclusions",
        ["conclusion_id", "team_id", "knowledge_base_id", "task_id", "owner_id"],
        ["id", "team_id", "knowledge_base_id", "task_id", "owner_id"],
        ondelete="CASCADE",
    )

    for table in (
        "analysis_tasks",
        "analysis_fields",
        "analysis_documents",
        "analysis_field_results",
        "analysis_conclusions",
        "analysis_citations",
    ):
        op.execute(f"grant select, insert, update, delete on {table} to {ROLE_NAME}")
        op.execute(f"alter table {table} enable row level security")
        op.execute(
            f"""
            create policy {table}_owner_scope on {table}
            using (
              team_id = app_current_team()
              and {
                "created_by_id = app_current_user()"
                if table == "analysis_tasks"
                else "owner_id = app_current_user()"
            }
            )
            with check (
              team_id = app_current_team()
              and {
                "created_by_id = app_current_user()"
                if table == "analysis_tasks"
                else "owner_id = app_current_user()"
            }
            )
            """
        )

    # worker 领取时使用受限函数，只暴露任务身份、文档及租约，不读取源片段或结果正文。
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
            select d.id
            from analysis_documents d
            join analysis_tasks t on t.id = d.task_id
            where t.status in ('queued', 'running')
              and t.stage in ('queued', 'extracting')
              and d.status = 'queued'
              and d.attempts < d.max_attempts
              and not exists (
                select 1 from analysis_documents prior
                where prior.task_id = d.task_id
                  and prior.ordinal < d.ordinal
                  and prior.status in ('queued', 'running')
              )
            order by t.created_at, t.id, d.ordinal
            for update of d skip locked
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
        create or replace function app_reclaim_analysis_documents()
        returns table(
          document_execution_id uuid,
          task_id uuid,
          team_id uuid,
          owner_id uuid,
          exhausted boolean
        )
        language sql security definer set search_path = public as $$
          with expired as (
            select d.id, d.task_id, d.team_id, d.owner_id,
                   d.attempts >= d.max_attempts as exhausted
            from analysis_documents d
            where d.status = 'running'
              and d.lease_expires_at < now()
            for update of d skip locked
          ),
          updated as (
            update analysis_documents target
            set status = case when e.exhausted then 'failed' else 'queued' end,
                lease_token = null,
                lease_expires_at = null,
                error_code = case when e.exhausted then 'ANALYSIS_EXHAUSTED' else null end,
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
    # 活跃任务数按实时状态统计，而不是维护计数器：任务结束或失效后自然释放，
    # 不存在重复释放或漏释放的路径。
    op.execute(
        """
        create or replace function app_count_active_analysis_tasks(
          target_team uuid, target_user uuid
        )
        returns table(user_active integer, team_active integer)
        language sql security definer set search_path = public as $$
          select
            count(*) filter (
              where t.created_by_id = target_user
            )::integer as user_active,
            count(*)::integer as team_active
          from analysis_tasks t
          where t.team_id = target_team
            and t.status in ('queued', 'running')
        $$
        """
    )
    for signature in (
        "app_claim_analysis_document(integer)",
        "app_reclaim_analysis_documents()",
        "app_count_active_analysis_tasks(uuid, uuid)",
    ):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")


def downgrade() -> None:
    for signature in (
        "app_claim_analysis_document(integer)",
        "app_reclaim_analysis_documents()",
        "app_count_active_analysis_tasks(uuid, uuid)",
    ):
        op.execute(f"revoke all on function {signature} from {ROLE_NAME}")
        op.execute(f"drop function if exists {signature}")
    for table in (
        "analysis_citations",
        "analysis_conclusions",
        "analysis_field_results",
        "analysis_documents",
        "analysis_fields",
        "analysis_tasks",
    ):
        op.execute(f"drop policy if exists {table}_owner_scope on {table}")
        op.execute(f"alter table {table} disable row level security")
        op.execute(f"revoke all on {table} from {ROLE_NAME}")
        op.drop_table(table)
    # 本迁移为“依据关联来源片段”新增的唯一约束，回退时一并移除。
    op.drop_constraint("uq_chunk_id_document_team", "document_chunks", type_="unique")
