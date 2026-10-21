"""V3 text/web ingestion and manual document replacement

Revision ID: 0018_v3_ingestion
Revises: 0017_v3_knowledge_products
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018_v3_ingestion"
down_revision: str | None = "0017_v3_knowledge_products"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("source_url", sa.String(2000), nullable=True))
    op.add_column(
        "documents", sa.Column("replaces_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_index("ix_documents_replaces_id", "documents", ["replaces_id"])


def downgrade() -> None:
    op.drop_index("ix_documents_replaces_id", table_name="documents")
    op.drop_column("documents", "replaces_id")
    op.drop_column("documents", "source_url")
