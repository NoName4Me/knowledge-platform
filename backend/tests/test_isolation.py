from __future__ import annotations

import uuid

from tests.conftest import add_member, make_team, make_user


def test_cannot_access_team_not_member(client, session):
    make_user(session, "a@example.com")
    other_team = make_team(session, "团队B")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})

    resp = client.get(f"/api/teams/{other_team.id}")
    assert resp.status_code == 403


def test_can_access_own_team(client, session):
    user = make_user(session, "a@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, user)
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})

    resp = client.get(f"/api/teams/{team.id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "团队A"


def test_multi_team_user_scoped_per_team(client, session):
    user = make_user(session, "a@example.com")
    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")
    add_member(session, team_a, user, role="admin")
    add_member(session, team_b, user, role="member")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})

    assert client.get(f"/api/teams/{team_a.id}").json()["role"] == "admin"
    assert client.get(f"/api/teams/{team_b.id}").json()["role"] == "member"


def test_unknown_team_returns_403_not_404(client, session):
    make_user(session, "a@example.com")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    resp = client.get(f"/api/teams/{uuid.uuid4()}")
    assert resp.status_code == 403


def test_member_can_create_knowledge_base(client, session):
    user = make_user(session, "a@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, user, role="member")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})

    resp = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "制度库"})
    assert resp.status_code == 201
    assert resp.json()["permission"] == "manage"
    assert resp.json()["owner_id"] == str(user.id)


def test_admin_can_create_knowledge_base(client, session):
    user = make_user(session, "a@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, user, role="admin")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})

    resp = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "制度库"})
    assert resp.status_code == 201
    assert resp.json()["name"] == "制度库"


def test_knowledge_bases_isolated_between_teams(client, session):
    user = make_user(session, "a@example.com")
    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")
    add_member(session, team_a, user, role="admin")
    add_member(session, team_b, user, role="admin")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})

    client.post(f"/api/teams/{team_a.id}/knowledge-bases", json={"name": "A制度库"})
    client.post(f"/api/teams/{team_b.id}/knowledge-bases", json={"name": "B制度库"})

    a_names = [kb["name"] for kb in client.get(f"/api/teams/{team_a.id}/knowledge-bases").json()]
    b_names = [kb["name"] for kb in client.get(f"/api/teams/{team_b.id}/knowledge-bases").json()]
    assert a_names == ["A制度库"]
    assert b_names == ["B制度库"]
