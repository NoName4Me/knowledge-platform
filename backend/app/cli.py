from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Team, TeamMembership, User
from app.security import hash_password


def create_admin(email: str, display_name: str, password: str, team_name: str) -> None:
    with SessionLocal() as session:
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
    parser.add_argument("--password", help="不提供则交互输入")
    args = parser.parse_args()

    password = args.password or getpass.getpass("密码: ")
    create_admin(args.email, args.name, password, args.team)


if __name__ == "__main__":
    main()
