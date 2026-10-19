"""one-time team invitations

Revision ID: 0011_team_invitations
Revises: 0010_turn_draft
Create Date: 2026-10-19
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_team_invitations"
down_revision: str | None = "0010_turn_draft"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"非法的 RLS 角色名：{ROLE_NAME}")


def upgrade() -> None:
    op.create_table(
        "team_invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("teams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "invited_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accepted_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("role in ('admin', 'member')", name="ck_invitation_role"),
        sa.UniqueConstraint("token_hash", name="uq_invitation_token"),
    )
    op.create_index("ix_team_invitations_team_id", "team_invitations", ["team_id"])

    op.execute(f"grant select, insert, update, delete on team_invitations to {ROLE_NAME}")
    op.execute("alter table team_invitations enable row level security")
    op.execute(
        """
        create policy team_invitations_team_scope on team_invitations
        using (team_id = app_current_team())
        with check (team_id = app_current_team())
        """
    )

    # 兑换时被邀请人还没有团队上下文，运行角色读不到邀请行；
    # 这两个函数只按令牌哈希处理单条邀请，不返回团队成员或资料内容。
    op.execute(
        """
        create or replace function app_preview_invitation(invitation_token_hash text)
        returns table (status text, team_name text, email text)
        language sql stable security definer set search_path = public as $$
          select case
                   when i.accepted_at is not null then 'used'
                   when i.expires_at <= now() then 'expired'
                   else 'ok'
                 end,
                 t.name,
                 i.email
          from team_invitations i
          join teams t on t.id = i.team_id
          where i.token_hash = invitation_token_hash
        $$
        """
    )
    op.execute(
        """
        create or replace function app_accept_invitation(
          invitation_token_hash text,
          target_user uuid,
          target_email text
        )
        returns table (status text, team_id uuid, team_name text, role text)
        language plpgsql security definer set search_path = public as $$
        declare
          invitation team_invitations%rowtype;
          joined_role text;
        begin
          select * into invitation
            from team_invitations
           where token_hash = invitation_token_hash
           for update;

          if not found then
            return query select 'not_found'::text, null::uuid, null::text, null::text;
            return;
          end if;
          if invitation.accepted_at is not null then
            return query select 'used'::text, null::uuid, null::text, null::text;
            return;
          end if;
          if invitation.expires_at <= now() then
            return query select 'expired'::text, null::uuid, null::text, null::text;
            return;
          end if;
          if lower(invitation.email) <> lower(target_email) then
            return query select 'email_mismatch'::text, null::uuid, null::text, null::text;
            return;
          end if;

          -- 已是成员时保持原角色，邀请只标记为已使用。
          insert into team_memberships (id, team_id, user_id, role)
          values (gen_random_uuid(), invitation.team_id, target_user, invitation.role)
          on conflict on constraint uq_team_membership do nothing;

          update team_invitations
             set accepted_at = now(), accepted_by_id = target_user
           where id = invitation.id;

          select m.role into joined_role
            from team_memberships m
           where m.team_id = invitation.team_id and m.user_id = target_user;

          return query
            select 'ok'::text, invitation.team_id, t.name::text, joined_role::text
              from teams t
             where t.id = invitation.team_id;
        end
        $$
        """
    )
    for signature in (
        "app_preview_invitation(text)",
        "app_accept_invitation(text, uuid, text)",
    ):
        op.execute(f"revoke all on function {signature} from public")
        op.execute(f"grant execute on function {signature} to {ROLE_NAME}")


def downgrade() -> None:
    op.execute(
        "revoke execute on function app_accept_invitation(text, uuid, text) "
        f"from {ROLE_NAME}"
    )
    op.execute(
        f"revoke execute on function app_preview_invitation(text) from {ROLE_NAME}"
    )
    op.execute("drop function if exists app_accept_invitation(text, uuid, text)")
    op.execute("drop function if exists app_preview_invitation(text)")
    op.execute("drop policy if exists team_invitations_team_scope on team_invitations")
    op.execute("alter table team_invitations disable row level security")
    op.execute(f"revoke all on team_invitations from {ROLE_NAME}")
    op.drop_index("ix_team_invitations_team_id", table_name="team_invitations")
    op.drop_table("team_invitations")
