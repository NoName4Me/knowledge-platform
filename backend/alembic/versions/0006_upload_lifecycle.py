"""upload reservation and logical deletion lifecycle

Revision ID: 0006_upload_lifecycle
Revises: 0005_lease_tokens
Create Date: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_upload_lifecycle"
down_revision: str | None = "0005_lease_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_document_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_document_status",
        "documents",
        "status in ('uploading', 'pending', 'processing', 'ready', 'failed', 'deleted')",
    )
    op.add_column("documents", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("documents", sa.Column("quota_released_at", sa.DateTime(timezone=True)))

    op.create_table(
        "team_quotas",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("reserved_documents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("reserved_documents >= 0", name="ck_quota_documents_nonnegative"),
        sa.CheckConstraint("reserved_bytes >= 0", name="ck_quota_bytes_nonnegative"),
    )
    op.execute(
        """
        insert into team_quotas(team_id, reserved_documents, reserved_bytes)
        select t.id, count(d.id), coalesce(sum(d.size_bytes), 0)
        from teams t
        left join documents d on d.team_id = t.id and d.status <> 'deleted'
        group by t.id
        """
    )

    op.create_table(
        "upload_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "team_id", "user_id", "idempotency_key", name="uq_upload_idempotency"
        ),
    )
    op.create_index(
        "ix_upload_requests_expires_at", "upload_requests", ["expires_at"]
    )

    # 只给运行角色新增管理权限；原始文件与索引清理仍需业务层判断。
    op.execute(
        f"""
        grant select, insert, update, delete on team_quotas, upload_requests
        to {__import__("os").environ.get("KP_RLS_ROLE", "kp_app")}
        """
    )
    for table in ("team_quotas", "upload_requests"):
        op.execute(f"alter table {table} enable row level security")
        op.execute(
            f"""
            create policy {table}_team_scope on {table}
            using (team_id = app_current_team())
            with check (team_id = app_current_team())
            """
        )
    # 新建团队时写入额度行；创建团队后立即在同一事务内创建相关行。
    op.execute(
        """
        create or replace function create_team_quota() returns trigger
        language plpgsql security definer set search_path = public as $$
        begin
          insert into team_quotas(team_id) values (new.id);
          return new;
        end
        $$
        """
    )
    op.execute(
        """
        create trigger teams_create_quota
        after insert on teams
        for each row execute function create_team_quota()
        """
    )


def downgrade() -> None:
    op.execute("drop trigger if exists teams_create_quota on teams")
    op.execute("drop function if exists create_team_quota()")
    op.drop_table("upload_requests")
    op.drop_table("team_quotas")
    op.drop_column("documents", "quota_released_at")
    op.drop_column("documents", "deleted_at")
    op.drop_constraint("ck_document_status", "documents", type_="check")
    op.create_check_constraint(
        "ck_document_status",
        "documents",
        "status in ('pending', 'processing', 'ready', 'failed')",
    )
