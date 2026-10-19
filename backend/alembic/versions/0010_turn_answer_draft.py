"""persist streaming answer draft

Revision ID: 0010_turn_draft
Revises: 0009_turn_reconcile_rls
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_turn_draft"
down_revision: str | None = "0009_turn_reconcile_rls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 流式内容是草稿：进程崩溃或客户端断流后仍可读到已生成部分，
    # 最终答案与校验过的引用才写入 answer。
    op.add_column("turns", sa.Column("answer_draft", sa.Text()))


def downgrade() -> None:
    op.drop_column("turns", "answer_draft")
