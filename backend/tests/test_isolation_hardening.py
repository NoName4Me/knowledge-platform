from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import add_member, make_team, make_user

ORIGIN = "http://localhost:5173"


def _login(client, email: str, password: str = "password123") -> str:
    return client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "csrf_token"
    ]


def _setup(client, session, role: str = "admin", email: str = "a@example.com"):
    user = make_user(session, email)
    team = make_team(session, f"团队-{email}")
    add_member(session, team, user, role=role)
    _login(client, email)
    return user, team


def test_write_without_csrf_token_is_rejected(client, session):
    make_user(session, "a@example.com")
    response = client.request(
        "POST",
        "/api/auth/login",
        json={"email": "a@example.com", "password": "password123"},
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_write_without_origin_is_rejected(client, session):
    make_user(session, "a@example.com")
    response = client.request(
        "POST",
        "/api/auth/login",
        json={"email": "a@example.com", "password": "password123"},
        headers={"x-csrf-token": "whatever"},
    )
    assert response.status_code == 403


def test_write_from_foreign_origin_is_rejected(client, session):
    make_user(session, "a@example.com")
    csrf = client.get("/api/auth/csrf").json()["csrf_token"]
    response = client.request(
        "POST",
        "/api/auth/login",
        json={"email": "a@example.com", "password": "password123"},
        headers={"origin": "http://evil.example.com", "x-csrf-token": csrf},
    )
    assert response.status_code == 403


def test_stale_session_token_is_rejected_after_login(client, session):
    make_user(session, "a@example.com")
    stale = client.get("/api/auth/csrf").json()["csrf_token"]
    _login(client, "a@example.com")
    response = client.request(
        "POST",
        "/api/auth/logout",
        headers={"origin": ORIGIN, "x-csrf-token": stale},
    )
    assert response.status_code == 403


def test_login_can_replace_stale_session_cookie(client, session):
    make_user(session, "a@example.com")
    client.cookies.set("kp_session", "expired-or-rotated-session")
    prelogin = client.get("/api/auth/csrf").json()["csrf_token"]

    response = client.request(
        "POST",
        "/api/auth/login",
        json={"email": "a@example.com", "password": "password123"},
        headers={"origin": ORIGIN, "x-csrf-token": prelogin},
    )

    assert response.status_code == 200
    assert client.get("/api/auth/me").status_code == 200


def test_member_cannot_list_members(client, session):
    _, team = _setup(client, session, role="member")
    assert client.get(f"/api/teams/{team.id}/members").status_code == 403


def test_admin_lists_members(client, session):
    admin, team = _setup(client, session, role="admin")
    other = make_user(session, "b@example.com")
    add_member(session, team, other, role="member")

    response = client.get(f"/api/teams/{team.id}/members")
    assert response.status_code == 200
    emails = {item["email"] for item in response.json()}
    assert emails == {"a@example.com", "b@example.com"}
    assert admin.email in emails


def test_member_cannot_remove_member(client, session):
    _, team = _setup(client, session, role="member")
    other = make_user(session, "b@example.com")
    add_member(session, team, other)
    response = client.delete(f"/api/teams/{team.id}/members/{other.id}")
    assert response.status_code == 403


def test_admin_removes_member_and_access_is_revoked(client, session):
    admin, team = _setup(client, session, role="admin")
    member = make_user(session, "b@example.com")
    add_member(session, team, member)

    assert client.delete(f"/api/teams/{team.id}/members/{member.id}").status_code == 204

    client.post("/api/auth/logout")
    _login(client, "b@example.com")
    assert client.get(f"/api/teams/{team.id}").status_code == 403


def test_last_admin_cannot_be_removed(client, session):
    admin, team = _setup(client, session, role="admin")
    response = client.delete(f"/api/teams/{team.id}/members/{admin.id}")
    assert response.status_code == 409

    # 团队仍保留管理员，未被静默降级。
    assert client.get(f"/api/teams/{team.id}").json()["role"] == "admin"


def test_second_admin_can_be_removed(client, session):
    admin, team = _setup(client, session, role="admin")
    second = make_user(session, "b@example.com")
    add_member(session, team, second, role="admin")

    assert client.delete(f"/api/teams/{team.id}/members/{second.id}").status_code == 204


def test_removing_unknown_member_is_idempotent(client, session):
    _, team = _setup(client, session, role="admin")
    response = client.delete(f"/api/teams/{team.id}/members/{uuid.uuid4()}")
    assert response.status_code == 204


def test_member_cannot_delete_document(client, session):
    admin, team = _setup(client, session, role="admin")
    kb = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "制度库"}).json()
    doc = client.post(
        f"/api/teams/{team.id}/documents",
        data={"knowledge_base_id": kb["id"]},
        files={"file": ("a.txt", "团队资料", "text/plain")},
    ).json()

    member = make_user(session, "b@example.com")
    add_member(session, team, member, role="member")
    client.post("/api/auth/logout")
    _login(client, "b@example.com")

    assert client.delete(f"/api/teams/{team.id}/documents/{doc['id']}").status_code == 403

    client.post("/api/auth/logout")
    _login(client, "a@example.com")
    assert client.delete(f"/api/teams/{team.id}/documents/{doc['id']}").status_code == 204


def test_document_cannot_cross_team_knowledge_base(session):
    """数据库层拒绝把文档挂到其他团队的知识库。"""
    from app.models import Document

    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")
    from app.models import KnowledgeBase

    kb_a = KnowledgeBase(team_id=team_a.id, name="A库")
    session.add(kb_a)
    session.commit()

    session.add(
        Document(
            team_id=team_b.id,
            knowledge_base_id=kb_a.id,
            title="越权",
            filename="x.txt",
            content_type="text/plain",
            size_bytes=1,
            storage_key=f"teams/{team_b.id}/x.txt",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_chunk_cannot_cross_team_document(session):
    from app.models import Document, DocumentChunk, KnowledgeBase

    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")
    kb_a = KnowledgeBase(team_id=team_a.id, name="A库")
    session.add(kb_a)
    session.commit()

    doc_a = Document(
        team_id=team_a.id,
        knowledge_base_id=kb_a.id,
        title="A资料",
        filename="a.txt",
        content_type="text/plain",
        size_bytes=1,
        storage_key=f"teams/{team_a.id}/a.txt",
    )
    session.add(doc_a)
    session.commit()

    session.add(
        DocumentChunk(
            team_id=team_b.id,
            document_id=doc_a.id,
            ordinal=1,
            content="越权片段",
            locator_label="片段 1",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_runtime_role_cannot_bypass_rls(session):
    """直接执行 SQL 也必须受团队上下文限制，不能依赖路由恰好写了过滤条件。"""
    from sqlalchemy import text

    from app.models import Document, KnowledgeBase

    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")
    kb_a = KnowledgeBase(team_id=team_a.id, name="A库")
    kb_b = KnowledgeBase(team_id=team_b.id, name="B库")
    session.add_all([kb_a, kb_b])
    session.commit()
    session.add_all(
        [
            Document(
                team_id=team_a.id,
                knowledge_base_id=kb_a.id,
                title="A资料",
                filename="a.txt",
                content_type="text/plain",
                size_bytes=1,
                storage_key=f"teams/{team_a.id}/a.txt",
            ),
            Document(
                team_id=team_b.id,
                knowledge_base_id=kb_b.id,
                title="B资料",
                filename="b.txt",
                content_type="text/plain",
                size_bytes=1,
                storage_key=f"teams/{team_b.id}/b.txt",
            ),
        ]
    )
    session.commit()

    # 不设置团队上下文：受限角色看不到任何团队资料。
    from tests.conftest import app_engine

    with app_engine.begin() as conn:
        conn.execute(text("SET ROLE kp_app"))
        assert conn.scalar(text("select count(*) from documents")) == 0
        conn.execute(
            text("select set_config('kp.team_id', :team_id, true)"),
            {"team_id": str(team_a.id)},
        )
        rows = conn.execute(text("select title from documents")).scalars().all()
        assert rows == ["A资料"]


def test_runtime_db_role_has_no_bypass_or_superuser(session):
    from sqlalchemy import text

    from tests.conftest import app_engine

    with app_engine.connect() as conn:
        row = conn.execute(
            text(
                "select rolsuper, rolbypassrls from pg_roles "
                "where rolname = current_user"
            )
        ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False


def test_team_context_does_not_leak_on_pooled_connection(session):
    """同一连接事务结束后不得保留上一个团队的上下文。"""
    from sqlalchemy import text

    from app.db import apply_context
    from tests.conftest import AppSession

    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")

    pooled = AppSession()
    try:
        apply_context(pooled, team_id=team_a.id)
        assert pooled.execute(
            text("select current_setting('kp.team_id')")
        ).scalar_one() == str(team_a.id)
        pooled.commit()
        assert pooled.execute(
            text("select current_setting('kp.team_id', true)")
        ).scalar_one() in (None, "")
        apply_context(pooled, team_id=team_b.id)
        assert pooled.execute(
            text("select current_setting('kp.team_id')")
        ).scalar_one() == str(team_b.id)
    finally:
        pooled.close()


def test_team_context_requires_actual_membership(client, session):
    """仅有团队 UUID 不能伪造上下文；入口仍须校验成员关系。"""
    make_user(session, "a@example.com")
    team = make_team(session, "未加入团队")
    _login(client, "a@example.com")
    assert client.get(f"/api/teams/{team.id}").status_code == 403


def test_invitation_style_session_replay_is_not_enough_without_csrf(client, session):
    """只拿到会话 Cookie 的跨站请求不能被接受。"""
    make_user(session, "a@example.com")
    team = make_team(session, "团队A")
    _login(client, "a@example.com")
    response = client.request(
        "DELETE",
        f"/api/teams/{team.id}/members/{uuid.uuid4()}",
        headers={"origin": ORIGIN},
    )
    assert response.status_code == 403
