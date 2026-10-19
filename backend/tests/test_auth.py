from __future__ import annotations

from tests.conftest import add_member, make_team, make_user


def test_login_success(client, session):
    user = make_user(session, "a@example.com", password="password123")
    team = make_team(session, "团队A")
    add_member(session, team, user, role="admin")

    resp = client.post(
        "/api/auth/login", json={"email": "a@example.com", "password": "password123"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "a@example.com"
    assert len(body["teams"]) == 1
    assert body["teams"][0]["role"] == "admin"


def test_login_wrong_password(client, session):
    make_user(session, "a@example.com", password="password123")
    resp = client.post("/api/auth/login", json={"email": "a@example.com", "password": "wrongpass1"})
    assert resp.status_code == 401


def test_me_requires_session(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_after_login(client, session):
    user = make_user(session, "a@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, user)
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "a@example.com"


def test_logout(client, session):
    make_user(session, "a@example.com")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
