"""row level security and a non-owner runtime role

Revision ID: 0004_rls
Revises: 0003_isolation
Create Date: 2026-10-19
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "0004_rls"
down_revision: str | None = "0003_isolation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 运行角色名来自配置；只接受普通标识符，避免把配置拼接进 DDL。
ROLE_NAME = os.environ.get("KP_RLS_ROLE", "kp_app")
if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", ROLE_NAME):
    raise RuntimeError(f"非法的 RLS 角色名：{ROLE_NAME}")

TEAM_SCOPED_TABLES = (
    "knowledge_bases",
    "documents",
    "document_chunks",
    "processing_tasks",
)

HELPERS = (
    "app_current_user()",
    "app_current_team()",
    "app_lock_team(uuid)",
    "app_claim_task(integer)",
    "app_reclaim_tasks()",
)


def upgrade() -> None:
    # 上下文读取：缺失或空值时返回 NULL，策略随之拒绝。
    op.execute(
        """
        create or replace function app_current_user() returns uuid
        language sql stable as $$
          select case
            when nullif(current_setting('kp.user_id', true), '')
                 ~ '^[0-9a-fA-F-]{36}$'
            then nullif(current_setting('kp.user_id', true), '')::uuid
            else null
          end
        $$
        """
    )
    op.execute(
        """
        create or replace function app_current_team() returns uuid
        language sql stable as $$
          select case
            when nullif(current_setting('kp.team_id', true), '')
                 ~ '^[0-9a-fA-F-]{36}$'
            then nullif(current_setting('kp.team_id', true), '')::uuid
            else null
          end
        $$
        """
    )

    # worker 的跨团队调度入口：只返回下一条任务的标识与团队，不返回原文或问答内容。
    op.execute(
        """
        create or replace function app_lock_team(target_team uuid)
        returns boolean
        language sql security definer set search_path = public as $$
          select exists(
            select 1 from teams
            where id = target_team and id = app_current_team()
            for update
          )
        $$
        """
    )
    op.execute(
        """
        create or replace function app_claim_task(lease_seconds integer)
        returns table(task_id uuid, team_id uuid)
        language sql security definer set search_path = public as $$
          with candidate as (
            select t.id from processing_tasks t
            where t.status = 'queued' and t.attempts < t.max_attempts
            order by t.created_at, t.id
            for update skip locked
            limit 1
          )
          update processing_tasks p
          set status = 'running',
              attempts = p.attempts + 1,
              lease_expires_at = now() + make_interval(secs => lease_seconds),
              updated_at = now()
          from candidate c
          where p.id = c.id
          returning p.id, p.team_id
        $$
        """
    )
    op.execute(
        """
        create or replace function app_reclaim_tasks()
        returns table(task_id uuid, team_id uuid, exhausted boolean)
        language sql security definer set search_path = public as $$
          with expired as (
            select t.id, t.team_id, t.attempts >= t.max_attempts as exhausted
            from processing_tasks t
            where t.status = 'running' and t.lease_expires_at < now()
            for update skip locked
          ),
          updated as (
            update processing_tasks p
            set status = case when e.exhausted then 'failed' else 'queued' end,
                lease_expires_at = null,
                updated_at = now()
            from expired e
            where p.id = e.id
            returning p.id, p.team_id, e.exhausted
          )
          select u.id, u.team_id, u.exhausted from updated u
        $$
        """
    )

    op.execute(
        f"""
        do $$
        begin
          if not exists (select 1 from pg_roles where rolname = '{ROLE_NAME}') then
            execute 'create role {ROLE_NAME} nologin nosuperuser nobypassrls';
          end if;
          -- 迁移身份可切换到该角色，应用连接用 SET ROLE 生效。
          execute 'grant {ROLE_NAME} to current_user';
        end
        $$
        """
    )
    op.execute(f"grant usage on schema public to {ROLE_NAME}")
    op.execute(
        f"grant select, insert, update, delete on all tables in schema public to {ROLE_NAME}"
    )
    op.execute(f"grant select, usage on all sequences in schema public to {ROLE_NAME}")
    op.execute(
        f"""
        alter default privileges in schema public
        grant select, insert, update, delete on tables to {ROLE_NAME}
        """
    )
    for helper in HELPERS:
        op.execute(f"grant execute on function {helper} to {ROLE_NAME}")

    # 团队资料：只允许当前团队上下文读写；缺失上下文即拒绝。
    for table in TEAM_SCOPED_TABLES:
        op.execute(f"alter table {table} enable row level security")
        op.execute(
            f"""
            create policy {table}_team_scope on {table}
            using (team_id = app_current_team())
            with check (team_id = app_current_team())
            """
        )

    # 成员关系：本人可读自己的成员关系（用于列出“我的团队”），当前团队可读本团队成员。
    op.execute("alter table team_memberships enable row level security")
    op.execute(
        """
        create policy team_memberships_scope on team_memberships
        using (user_id = app_current_user() or team_id = app_current_team())
        with check (team_id = app_current_team())
        """
    )

    # teams 不直接给运行角色整表权限；只能通过普通视图读取本人所属团队。
    op.execute(f"revoke select, insert, update, delete on teams from {ROLE_NAME}")
    op.execute(
        """
        create view my_teams with (security_invoker = false) as
        select t.id, t.name
        from teams t
        where t.id = app_current_team()
           or exists (
             select 1 from team_memberships m
             where m.team_id = t.id and m.user_id = app_current_user()
           )
        """
    )
    op.execute("comment on view my_teams is '仅供当前成员列出自己所属团队'")
    op.execute(f"grant select on my_teams to {ROLE_NAME}")


def downgrade() -> None:
    op.execute("drop view if exists my_teams")
    for table in (*TEAM_SCOPED_TABLES, "team_memberships"):
        op.execute(f"drop policy if exists {table}_team_scope on {table}")
        op.execute(f"alter table {table} disable row level security")
    op.execute(f"revoke all on all tables in schema public from {ROLE_NAME}")
    for helper in HELPERS:
        op.execute(f"drop function if exists {helper}")
