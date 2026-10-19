"""cancel tasks whose source has already been deleted

Revision ID: 0008_task_canceled
Revises: 0007_conversations
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_task_canceled"
down_revision: str | None = "0007_conversations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_task_status", "processing_tasks", type_="check")
    op.create_check_constraint(
        "ck_task_status",
        "processing_tasks",
        "status in ('queued', 'running', 'succeeded', 'failed', 'canceled')",
    )


def downgrade() -> None:
    op.execute("update processing_tasks set status = 'failed' where status = 'canceled'")
    op.drop_constraint("ck_task_status", "processing_tasks", type_="check")
    op.create_check_constraint(
        "ck_task_status",
        "processing_tasks",
        "status in ('queued', 'running', 'succeeded', 'failed')",
    )
