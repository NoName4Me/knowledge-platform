"""hardening team isolation: composite ownership constraints and session csrf

Revision ID: 0003_isolation
Revises: 0002_documents
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_isolation"
down_revision: str | None = "0002_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 每条检查：子表行与父表行的团队归属必须一致，否则复合外键无法建立。
CONSISTENCY_CHECKS = (
    (
        "documents.knowledge_base_id",
        """
        select count(*) from documents d
        join knowledge_bases kb on kb.id = d.knowledge_base_id
        where kb.team_id <> d.team_id
        """,
    ),
    (
        "document_chunks.document_id",
        """
        select count(*) from document_chunks c
        join documents d on d.id = c.document_id
        where d.team_id <> c.team_id
        """,
    ),
    (
        "processing_tasks.document_id",
        """
        select count(*) from processing_tasks t
        join documents d on d.id = t.document_id
        where d.team_id <> t.team_id
        """,
    ),
)


def upgrade() -> None:
    # 先确认历史数据不存在跨团队挂接；发现异常立即停止，不静默修复。
    connection = op.get_bind()
    violations = [
        (label, int(connection.execute(sa.text(query)).scalar_one()))
        for label, query in CONSISTENCY_CHECKS
    ]
    broken = [f"{label}: {count} 行" for label, count in violations if count]
    if broken:
        raise RuntimeError(
            "存在跨团队挂接的数据，需先人工修复后再迁移：" + "；".join(broken)
        )

    op.create_unique_constraint("uq_kb_id_team", "knowledge_bases", ["id", "team_id"])
    op.create_unique_constraint("uq_document_id_team", "documents", ["id", "team_id"])

    op.create_foreign_key(
        "fk_document_kb_team",
        "documents",
        "knowledge_bases",
        ["knowledge_base_id", "team_id"],
        ["id", "team_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_chunk_document_team",
        "document_chunks",
        "documents",
        ["document_id", "team_id"],
        ["id", "team_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_task_document_team",
        "processing_tasks",
        "documents",
        ["document_id", "team_id"],
        ["id", "team_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("fk_task_document_team", "processing_tasks", type_="foreignkey")
    op.drop_constraint("fk_chunk_document_team", "document_chunks", type_="foreignkey")
    op.drop_constraint("fk_document_kb_team", "documents", type_="foreignkey")
    op.drop_constraint("uq_document_id_team", "documents", type_="unique")
    op.drop_constraint("uq_kb_id_team", "knowledge_bases", type_="unique")
