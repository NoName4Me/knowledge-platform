"""owner-scoped conversations, turns and citations

Revision ID: 0007_conversations
Revises: 0006_upload_lifecycle
Create Date: 2026-10-19
"""

from __future__ import annotations

import os
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_conversations"
down_revision: str | None = "0006_upload_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")


def upgrade() -> None:
    op.create_table(
        "conversations",
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
        sa.Column("title", sa.String(100), nullable=False, server_default="新会话"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "team_id", "knowledge_base_id", "id", "created_by_id",
            name="uq_conversation_owner_scope",
        ),
    )
    op.create_index("ix_conversations_team_id", "conversations", ["team_id"])
    op.create_index(
        "ix_conversations_knowledge_base_id", "conversations", ["knowledge_base_id"]
    )
    op.create_index("ix_conversations_created_by_id", "conversations", ["created_by_id"])

    op.create_table(
        "turns",
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
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "created_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="answering"),
        sa.Column("answer", sa.Text()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "team_id", "knowledge_base_id", "id", "created_by_id",
            name="uq_turn_id_owner_scope",
        ),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_turn_seq"),
        sa.UniqueConstraint(
            "conversation_id", "client_request_id", name="uq_turn_request"
        ),
        sa.CheckConstraint(
            "status in ('answering', 'completed', 'insufficient', 'failed', 'interrupted')",
            name="ck_turn_status",
        ),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "conversation_id", "created_by_id"],
            [
                "conversations.team_id",
                "conversations.knowledge_base_id",
                "conversations.id",
                "conversations.created_by_id",
            ],
            name="fk_turn_conversation_owner",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_turns_conversation_id", "turns", ["conversation_id"])
    op.create_index("ix_turns_status", "turns", ["status"])

    op.create_table(
        "citations",
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
            "turn_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "chunk_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_chunks.id", ondelete="SET NULL"),
        ),
        sa.Column("filename_snapshot", sa.String(300), nullable=False),
        sa.Column("locator_label_snapshot", sa.String(200), nullable=False),
        sa.UniqueConstraint("turn_id", "ordinal", name="uq_citation_ordinal"),
        sa.ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "turn_id", "owner_id"],
            [
                "turns.team_id",
                "turns.knowledge_base_id",
                "turns.id",
                "turns.created_by_id",
            ],
            name="fk_citation_turn_owner",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "team_id"],
            ["documents.id", "documents.team_id"],
            name="fk_citation_document_team",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_citations_turn_id", "citations", ["turn_id"])

    for table in ("conversations", "turns", "citations"):
        op.execute(
            f"grant select, insert, update, delete on {table} to {ROLE_NAME}"
        )
        op.execute(f"alter table {table} enable row level security")
        owner_check = "created_by_id = app_current_user()"
        if table == "citations":
            owner_check = "owner_id = app_current_user()"
        op.execute(
            f"""
            create policy {table}_owner_scope on {table}
            using (team_id = app_current_team() and {owner_check})
            with check (team_id = app_current_team() and {owner_check})
            """
        )


def downgrade() -> None:
    op.drop_table("citations")
    op.drop_table("turns")
    op.drop_table("conversations")
