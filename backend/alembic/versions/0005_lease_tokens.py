"""unique lease token per task claim

Revision ID: 0005_lease_tokens
Revises: 0004_rls
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_lease_tokens"
down_revision: str | None = "0004_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_tasks",
        sa.Column("lease_token", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # 旧执行者无法再依赖“状态仍为 running”判断所有权；排队时清空令牌。
    op.execute(
        "update processing_tasks set lease_token = gen_random_uuid() where status = 'running'"
    )
    op.execute("drop function app_claim_task(integer)")
    op.execute(
        """
        create or replace function app_claim_task(lease_seconds integer)
        returns table(task_id uuid, team_id uuid, lease_token uuid)
        language sql security definer set search_path = public as $$
          with candidate as (
            select t.id from processing_tasks t
            where t.status = 'queued' and t.attempts < t.max_attempts
            order by t.created_at, t.id
            for update skip locked
            limit 1
          )
          update processing_tasks p
          set status = 'running',
              attempts = p.attempts + 1,
              lease_token = gen_random_uuid(),
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              updated_at = now()
          from candidate c
          where p.id = c.id
          returning p.id, p.team_id, p.lease_token
        $$
        """
    )
    op.execute(
        """
        create or replace function app_reclaim_tasks()
        returns table(task_id uuid, team_id uuid, exhausted boolean)
        language sql security definer set search_path = public as $$
          with expired as (
            select t.id, t.team_id, t.attempts >= t.max_attempts as exhausted
            from processing_tasks t
            where t.status = 'running' and t.lease_expires_at < now()
            for update skip locked
          ),
          updated as (
            update processing_tasks p
            set status = case when e.exhausted then 'failed' else 'queued' end,
                lease_token = null,
                lease_expires_at = null,
                updated_at = now()
            from expired e
            where p.id = e.id
            returning p.id, p.team_id, e.exhausted
          )
          select u.id, u.team_id, u.exhausted from updated u
        $$
        """
    )


def downgrade() -> None:
    op.drop_column("processing_tasks", "lease_token")
