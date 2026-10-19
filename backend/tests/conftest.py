from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get(
    "KP_TEST_DATABASE_URL", "postgresql+psycopg://kp:kp@localhost:5432/kp_test"
)

engine = create_engine(TEST_DATABASE_URL, future=True)
TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

# 应用请求必须走受限角色，否则表所有者会绕过行级安全，测试就失去意义。
app_engine = create_engine(TEST_DATABASE_URL, future=True)
AppSession = sessionmaker(bind=app_engine, autoflush=False, expire_on_commit=False, future=True)


@pytest.fixture(scope="session", autouse=True)
def _migrate() -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    from alembic.config import Config

    from alembic import command

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")


@pytest.fixture()
def session() -> Iterator[Session]:
    with TestSession() as s:
        yield s
        s.rollback()
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE users, teams, team_memberships, knowledge_bases, session_tokens, "
                "documents, document_chunks, processing_tasks, team_quotas, "
                "upload_requests, conversations, turns, citations CASCADE"
            )
        )


@pytest.fixture()
def client(session: Session) -> Iterator[TestClient]:
    from app import db
    from app.main import app

    db.engine = app_engine
    db.SessionLocal = AppSession
    db.configure_engine(app_engine)
    with TestClient(app) as c:
        _install_csrf(c)
        yield c


def _install_csrf(client: TestClient) -> None:
    """为测试客户端自动附带来源与 CSRF 令牌，等价于浏览器行为。"""
    origin = "http://localhost:5173"

    def current_token() -> str:
        if client.cookies.get("kp_session"):
            resp = client.get("/api/auth/me")
            if resp.status_code == 200:
                return resp.json()["csrf_token"]
        return client.get("/api/auth/csrf").json()["csrf_token"]

    for method_name in ("post", "put", "patch", "delete"):
        original = getattr(client, method_name)

        def wrapper(*args, _original=original, _method=method_name, **kwargs):
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("origin", origin)
            headers.setdefault("x-csrf-token", current_token())
            if _method == "post" and "/documents" in str(args[0] if args else ""):
                headers.setdefault("Idempotency-Key", str(uuid.uuid4()))
            return _original(*args, headers=headers, **kwargs)

        setattr(client, method_name, wrapper)


def make_user(session: Session, email: str, password: str = "password123", name: str = "用户"):
    from app.models import User
    from app.security import hash_password

    user = User(email=email, display_name=name, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    return user


def make_team(session: Session, name: str):
    from app.models import Team

    team = Team(name=name)
    session.add(team)
    session.commit()
    return team


def add_member(session: Session, team, user, role: str = "member"):
    from app.models import TeamMembership

    session.add(TeamMembership(team_id=team.id, user_id=user.id, role=role))
    session.commit()
