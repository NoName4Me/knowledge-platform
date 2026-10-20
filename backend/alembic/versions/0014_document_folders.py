"""Document folders and safe knowledge-base maintenance."""

from __future__ import annotations

import os
import re

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0014_document_folders"
down_revision = "0013_v2_batch_comparison"
branch_labels = None
depends_on = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError("非法的 RLS 角色名")


def upgrade() -> None:
    op.create_table(
        "document_folders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True)),
        sa.UniqueConstraint("parent_id", "name", name="uq_folder_parent_name"),
        sa.UniqueConstraint("id", "knowledge_base_id", "team_id", name="uq_folder_scope"),
        sa.ForeignKeyConstraint(
            ["parent_id", "knowledge_base_id", "team_id"],
            [
                "document_folders.id",
                "document_folders.knowledge_base_id",
                "document_folders.team_id",
            ],
            ondelete="CASCADE",
            name="fk_folder_parent_scope",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id", "team_id"],
            ["knowledge_bases.id", "knowledge_bases.team_id"],
            ondelete="CASCADE",
            name="fk_folder_kb_team",
        ),
    )
    op.create_index("ix_folder_kb", "document_folders", ["knowledge_base_id"])
    op.create_index("ix_folder_parent", "document_folders", ["parent_id"])
    op.execute(
        """
        create unique index uq_folder_tree_name on document_folders
        (knowledge_base_id, coalesce(parent_id, '00000000-0000-0000-0000-000000000000'::uuid), name)
        """
    )
    op.add_column("documents", sa.Column("folder_id", postgresql.UUID(as_uuid=True)))
    op.create_index("ix_documents_folder_id", "documents", ["folder_id"])
    op.create_foreign_key(
        "fk_document_folder_scope",
        "documents",
        "document_folders",
        ["folder_id", "knowledge_base_id", "team_id"],
        ["id", "knowledge_base_id", "team_id"],
    )
    op.execute(f"grant select, insert, update, delete on document_folders to {ROLE_NAME}")
    op.execute("alter table document_folders enable row level security")
    op.execute("""
        create policy folder_team_scope on document_folders
        using (team_id = app_current_team()) with check (team_id = app_current_team())
    """)
    # 删除知识库是明确的管理员清理动作；资料、私人问答和分析记录随知识库级联清理。


def downgrade() -> None:
    op.drop_constraint("fk_document_folder_scope", "documents", type_="foreignkey")
    op.drop_index("ix_documents_folder_id", table_name="documents")
    op.drop_column("documents", "folder_id")
    op.execute("drop index if exists uq_folder_tree_name")
    op.execute("drop index if exists ix_folder_parent")
    op.drop_table("document_folders")
