"""V3 personal notes, reports and reusable requirements

Revision ID: 0017_v3_knowledge_products
Revises: 0016_turn_document_scope
Create Date: 2026-10-20
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_v3_knowledge_products"
down_revision: str | None = "0016_turn_document_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"非法的 RLS 角色名：{ROLE_NAME}")


def _owned_policy(table: str) -> None:
    op.execute(f"grant select, insert, update, delete on {table} to {ROLE_NAME}")
    op.execute(f"alter table {table} enable row level security")
    op.execute(
        f"""
        create policy {table}_owner_scope on {table}
        using (team_id = app_current_team() and created_by_id = app_current_user())
        with check (team_id = app_current_team() and created_by_id = app_current_user())
        """
    )


def _child_policy(table: str) -> None:
    op.execute(f"grant select, insert, update, delete on {table} to {ROLE_NAME}")
    op.execute(f"alter table {table} enable row level security")
    op.execute(
        f"""
        create policy {table}_owner_scope on {table}
        using (team_id = app_current_team() and owner_id = app_current_user())
        with check (team_id = app_current_team() and owner_id = app_current_user())
        """
    )


def upgrade() -> None:
    op.create_table(
        "knowledge_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("source_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "citations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "source_type in ('manual', 'document', 'conversation', 'report')",
            name="ck_note_source_type",
        ),
        sa.UniqueConstraint(
            "team_id", "knowledge_base_id", "id", "created_by_id", name="uq_note_owner_scope"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "team_id"],
            ["knowledge_bases.id", "knowledge_bases.team_id"],
            name="fk_note_kb_team",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_knowledge_notes_team_id", "knowledge_notes", ["team_id"])
    op.create_index(
        "ix_knowledge_notes_knowledge_base_id", "knowledge_notes", ["knowledge_base_id"]
    )
    op.create_index("ix_knowledge_notes_created_by_id", "knowledge_notes", ["created_by_id"])

    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_base_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_analysis_id", postgresql.UUID(as_uuid=True)),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("focus", sa.Text(), nullable=False, server_default=""),
        sa.Column("length", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_note", sa.String(500)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status in ('draft', 'reviewed')", name="ck_report_status"),
        sa.UniqueConstraint(
            "team_id", "knowledge_base_id", "id", "created_by_id", name="uq_report_owner_scope"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "team_id"],
            ["knowledge_bases.id", "knowledge_bases.team_id"],
            name="fk_report_kb_team",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_reports_team_id", "reports", ["team_id"])
    op.create_index("ix_reports_knowledge_base_id", "reports", ["knowledge_base_id"])
    op.create_index("ix_reports_created_by_id", "reports", ["created_by_id"])
    op.create_index("ix_reports_source_analysis_id", "reports", ["source_analysis_id"])
    op.create_index("ix_reports_status", "reports", ["status"])

    op.create_table(
        "report_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title_snapshot", sa.String(300), nullable=False),
        sa.Column("fingerprint", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "source_type in ('analysis', 'document', 'note')", name="ck_report_source_type"
        ),
        sa.UniqueConstraint("report_id", "source_type", "source_id", name="uq_report_source"),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "report_id", "owner_id"],
            [
                "reports.team_id",
                "reports.knowledge_base_id",
                "reports.id",
                "reports.created_by_id",
            ],
            name="fk_report_source_report_owner",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_report_sources_report_id", "report_sources", ["report_id"])

    op.create_table(
        "report_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("report_id", "ordinal", name="uq_report_section_ordinal"),
        sa.UniqueConstraint("id", "report_id", name="uq_report_section_id_report"),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "report_id", "owner_id"],
            [
                "reports.team_id",
                "reports.knowledge_base_id",
                "reports.id",
                "reports.created_by_id",
            ],
            name="fk_report_section_report_owner",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_report_sections_report_id", "report_sections", ["report_id"])

    op.create_table(
        "report_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True)),
        sa.Column("document_title", sa.String(300), nullable=False),
        sa.Column("locator_label", sa.String(200), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.UniqueConstraint("section_id", "ordinal", name="uq_report_citation_ordinal"),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "report_id", "owner_id"],
            [
                "reports.team_id",
                "reports.knowledge_base_id",
                "reports.id",
                "reports.created_by_id",
            ],
            name="fk_report_citation_report_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["section_id", "report_id"],
            ["report_sections.id", "report_sections.report_id"],
            name="fk_report_citation_section",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_report_citations_report_id", "report_citations", ["report_id"])
    op.create_index("ix_report_citations_section_id", "report_citations", ["section_id"])

    op.create_table(
        "saved_requirements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind in ('analysis_fields', 'report')", name="ck_saved_requirement_kind"
        ),
        sa.UniqueConstraint(
            "team_id", "created_by_id", "kind", "name", name="uq_requirement_name"
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "team_id"],
            ["knowledge_bases.id", "knowledge_bases.team_id"],
            name="fk_requirement_kb_team",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_saved_requirements_team_id", "saved_requirements", ["team_id"])
    op.create_index(
        "ix_saved_requirements_knowledge_base_id", "saved_requirements", ["knowledge_base_id"]
    )
    op.create_index("ix_saved_requirements_created_by_id", "saved_requirements", ["created_by_id"])

    _owned_policy("knowledge_notes")
    _owned_policy("reports")
    for table in ("report_sources", "report_sections", "report_citations"):
        _child_policy(table)
    _owned_policy("saved_requirements")


def downgrade() -> None:
    for table in (
        "report_citations",
        "report_sections",
        "report_sources",
        "reports",
        "knowledge_notes",
        "saved_requirements",
    ):
        op.execute(f"drop policy if exists {table}_owner_scope on {table}")
        op.execute(f"alter table {table} disable row level security")
        op.execute(f"revoke all on {table} from {ROLE_NAME}")
        op.drop_table(table)
