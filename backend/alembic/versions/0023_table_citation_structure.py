"""Persist table structure for document and analysis citations.

Revision ID: 0023_table_citation_structure
Revises: 0022_v4_report_automation
Create Date: 2026-10-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_table_citation_structure"
down_revision: str | None = "0022_v4_report_automation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("document_chunks", "analysis_citations"):
        op.add_column(table, sa.Column("table_number", sa.Integer()))
        op.add_column(table, sa.Column("table_row", sa.Integer()))
        op.add_column(table, sa.Column("table_headers", postgresql.JSONB()))
        op.add_column(table, sa.Column("table_cells", postgresql.JSONB()))


def downgrade() -> None:
    for table in ("analysis_citations", "document_chunks"):
        op.drop_column(table, "table_cells")
        op.drop_column(table, "table_headers")
        op.drop_column(table, "table_row")
        op.drop_column(table, "table_number")
