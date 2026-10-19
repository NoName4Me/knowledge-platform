from __future__ import annotations

import argparse
import getpass
import os

from sqlalchemy import select, text

from app.db import SessionLocal
from app.models import Team, TeamMembership, User
from app.security import hash_password


def create_admin(email: str, display_name: str, password: str, team_name: str) -> None:
    with SessionLocal() as session:
        # 应用连接默认切换为受限角色；初始化命令需要以迁移账号创建账号与团队。
        session.execute(text("reset role"))
        user = session.scalars(select(User).where(User.email == email.lower())).one_or_none()
        if user is None:
            user = User(
                email=email.lower(),
                display_name=display_name,
                password_hash=hash_password(password),
            )
            session.add(user)
            session.flush()

        team = session.scalars(select(Team).where(Team.name == team_name)).one_or_none()
        if team is None:
            team = Team(name=team_name)
            session.add(team)
            session.flush()

        membership = session.scalars(
            select(TeamMembership).where(
                TeamMembership.team_id == team.id, TeamMembership.user_id == user.id
            )
        ).one_or_none()
        if membership is None:
            session.add(TeamMembership(team_id=team.id, user_id=user.id, role="admin"))
        else:
            membership.role = "admin"
        session.commit()
        print(f"已创建/更新管理员 {user.email}，团队「{team.name}」")


def main() -> None:
    parser = argparse.ArgumentParser(description="创建初始管理员与团队")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--team", required=True)
    parser.add_argument("--password", help="不提供则交互输入；自动化环境可改用 --password-env")
    parser.add_argument(
        "--password-env",
        help="从指定环境变量读取密码，避免密码出现在 shell 历史中",
    )
    args = parser.parse_args()

    if args.password and args.password_env:
        parser.error("--password 与 --password-env 只能使用一个")
    password = (
        os.environ.get(args.password_env, "")
        if args.password_env
        else args.password or getpass.getpass("密码: ")
    )
    if not password:
        parser.error("密码不能为空")
    create_admin(args.email, args.name, password, args.team)


if __name__ == "__main__":
    main()
