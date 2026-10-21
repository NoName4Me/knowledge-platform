"""V3 report source replacement review state

Revision ID: 0020_v3_source_review
Revises: 0019_v3_report_gaps
Create Date: 2026-10-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_v3_source_review"
down_revision: str | None = "0019_v3_report_gaps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "source_review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "reports",
        sa.Column("source_review_note", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "source_review_note")
    op.drop_column("reports", "source_review_required")
