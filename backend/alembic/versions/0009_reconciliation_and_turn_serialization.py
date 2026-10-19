"""serialize turns and expose RLS-safe upload reconciliation candidates

Revision ID: 0009_turn_reconcile_rls
Revises: 0008_task_canceled
Create Date: 2026-10-19
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from alembic import op

revision: str = "0009_turn_reconcile_rls"
down_revision: str | None = "0008_task_canceled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")


def upgrade() -> None:
    # 历史并发可能留下多个 answering；保留最早一轮，其余明确收敛为中断。
    op.execute(
        """
        with ranked as (
          select id,
                 row_number() over (
                   partition by conversation_id
                   order by started_at, id
                 ) as position
          from turns
          where status = 'answering'
        )
        update turns t
        set status = 'interrupted',
            error_code = 'TURN_CONFLICT',
            finished_at = now()
        from ranked r
        where t.id = r.id and r.position > 1
        """
    )
    op.execute(
        """
        create unique index uq_turn_one_answering
        on turns (conversation_id)
        where status = 'answering'
        """
    )

    # worker 使用受限角色也无法跨团队读取 documents。该函数只调度候选 ID，
    # 不返回正文或文档元数据；调用方随后逐团队恢复 RLS 上下文。
    op.execute(
        """
        create or replace function app_reconcile_upload_candidates(
          cutoff timestamptz,
          batch_limit integer
        )
        returns table(document_id uuid, team_id uuid)
        language sql stable security definer
        set search_path = public
        as $$
          select d.id, d.team_id
          from documents d
          where d.status = 'uploading'
            and d.updated_at < cutoff
          order by d.created_at, d.id
          limit greatest(least(batch_limit, 1000), 1)
        $$
        """
    )
    op.execute(
        "revoke all on function app_reconcile_upload_candidates(timestamptz, integer) "
        "from public"
    )
    op.execute(
        f"grant execute on function "
        f"app_reconcile_upload_candidates(timestamptz, integer) to {ROLE_NAME}"
    )


def downgrade() -> None:
    op.execute(
        "revoke execute on function "
        "app_reconcile_upload_candidates(timestamptz, integer) "
        f"from {ROLE_NAME}"
    )
    op.execute("drop function if exists app_reconcile_upload_candidates(timestamptz, integer)")
    op.execute("drop index if exists uq_turn_one_answering")
