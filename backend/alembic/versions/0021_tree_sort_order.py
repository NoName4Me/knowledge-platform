"""Persist folder and document sibling order.

Revision ID: 0021_tree_sort_order
Revises: 0020_v3_source_review
Create Date: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_tree_sort_order"
down_revision: str | None = "0020_v3_source_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_folders",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "documents",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_document_folders_sibling_order",
        "document_folders",
        ["knowledge_base_id", "parent_id", "sort_order", "id"],
    )
    op.create_index(
        "ix_documents_sibling_order",
        "documents",
        ["knowledge_base_id", "folder_id", "sort_order", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_documents_sibling_order", table_name="documents")
    op.drop_index("ix_document_folders_sibling_order", table_name="document_folders")
    op.drop_column("documents", "sort_order")
    op.drop_column("document_folders", "sort_order")
