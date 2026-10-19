from __future__ import annotations

from sqlalchemy import select


def test_cli_uses_admin_identity_to_bootstrap_team(client, session):
    from app import cli
    from app.models import Team, TeamMembership, User
    from tests.conftest import AppSession

    original_session = cli.SessionLocal
    cli.SessionLocal = AppSession
    try:
        cli.create_admin(
            "bootstrap@example.com",
            "初始化管理员",
            "safe-password-123",
            "初始化团队",
        )
    finally:
        cli.SessionLocal = original_session

    user = session.scalars(
        select(User).where(User.email == "bootstrap@example.com")
    ).one()
    team = session.scalars(select(Team).where(Team.name == "初始化团队")).one()
    membership = session.scalars(
        select(TeamMembership).where(
            TeamMembership.user_id == user.id,
            TeamMembership.team_id == team.id,
        )
    ).one()
    assert membership.role == "admin"


def test_cli_reads_password_from_environment(monkeypatch):
    import sys

    from app import cli

    captured: dict[str, str] = {}

    def fake_create_admin(email: str, name: str, password: str, team_name: str) -> None:
        captured.update(
            email=email,
            name=name,
            password=password,
            team_name=team_name,
        )

    monkeypatch.setattr(cli, "create_admin", fake_create_admin)
    monkeypatch.setenv("KP_BOOTSTRAP_PASSWORD", "from-environment")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "app",
            "--email",
            "admin@example.com",
            "--name",
            "管理员",
            "--team",
            "团队",
            "--password-env",
            "KP_BOOTSTRAP_PASSWORD",
        ],
    )
    cli.main()
    assert captured["password"] == "from-environment"
