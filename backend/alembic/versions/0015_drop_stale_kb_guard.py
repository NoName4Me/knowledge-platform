"""drop the stale knowledge-base delete guard

Revision ID: 0015_drop_stale_kb_guard
Revises: 0014_document_folders
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0015_drop_stale_kb_guard"
down_revision: str | None = "0014_document_folders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 早期本地数据库曾手工加过 guard_kb_delete，它会在知识库非空时直接拒绝删除，
    # 与“删除知识库时一并清理资料、目录、问答与分析结果”的产品行为冲突。
    # 该对象从未进入迁移，只残留在个别环境里，这里统一清理。
    op.execute("drop trigger if exists guard_kb_delete on knowledge_bases")
    op.execute("drop function if exists app_guard_kb_delete()")


def downgrade() -> None:
    # 该触发器属于历史遗留对象，不在迁移体系内，降级时不重建。
    pass
