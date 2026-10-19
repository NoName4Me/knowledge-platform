from __future__ import annotations

from tests.conftest import add_member, make_team, make_user


def _setup_team_with_kb(client, session, role="member"):
    user = make_user(session, "a@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, user, role=role)
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    resp = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "制度库"})
    if resp.status_code == 201:
        return team, resp.json()["id"]
    return team, None


def _setup_admin(client, session):
    user = make_user(session, "admin@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, user, role="admin")
    client.post("/api/auth/login", json={"email": "admin@example.com", "password": "password123"})
    resp = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "制度库"})
    return team, resp.json()["id"]


def test_upload_text_document(client, session):
    team, kb_id = _setup_admin(client, session)
    resp = client.post(
        f"/api/teams/{team.id}/documents",
        data={"knowledge_base_id": kb_id},
        files={"file": ("policy.txt", "员工出差交通费用凭有效票据报销。", "text/plain")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["filename"] == "policy.txt"


def test_upload_rejects_unsupported_type(client, session):
    team, kb_id = _setup_admin(client, session)
    resp = client.post(
        f"/api/teams/{team.id}/documents",
        data={"knowledge_base_id": kb_id},
        files={"file": ("photo.png", b"\x89PNG", "image/png")},
    )
    assert resp.status_code == 415


def test_upload_rejects_empty_file(client, session):
    team, kb_id = _setup_admin(client, session)
    resp = client.post(
        f"/api/teams/{team.id}/documents",
        data={"knowledge_base_id": kb_id},
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert resp.status_code == 400


def test_member_can_upload(client, session):
    user = make_user(session, "m@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, user, role="admin")
    client.post("/api/auth/login", json={"email": "m@example.com", "password": "password123"})
    kb = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "制度库"}).json()

    user2 = make_user(session, "m2@example.com")
    add_member(session, team, user2, role="member")
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": "m2@example.com", "password": "password123"})

    resp = client.post(
        f"/api/teams/{team.id}/documents",
        data={"knowledge_base_id": kb["id"]},
        files={"file": ("note.md", "# 标题\n内容", "text/markdown")},
    )
    assert resp.status_code == 201


def test_documents_isolated_between_teams(client, session):
    user = make_user(session, "a@example.com")
    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")
    add_member(session, team_a, user, role="admin")
    add_member(session, team_b, user, role="admin")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    kb_a = client.post(f"/api/teams/{team_a.id}/knowledge-bases", json={"name": "A库"}).json()
    kb_b = client.post(f"/api/teams/{team_b.id}/knowledge-bases", json={"name": "B库"}).json()

    client.post(
        f"/api/teams/{team_a.id}/documents",
        data={"knowledge_base_id": kb_a["id"]},
        files={"file": ("a.txt", "团队A的资料", "text/plain")},
    )
    client.post(
        f"/api/teams/{team_b.id}/documents",
        data={"knowledge_base_id": kb_b["id"]},
        files={"file": ("b.txt", "团队B的资料", "text/plain")},
    )

    a_docs = client.get(f"/api/teams/{team_a.id}/documents").json()
    b_docs = client.get(f"/api/teams/{team_b.id}/documents").json()
    assert [d["filename"] for d in a_docs] == ["a.txt"]
    assert [d["filename"] for d in b_docs] == ["b.txt"]


def test_cannot_upload_to_other_team_knowledge_base(client, session):
    user = make_user(session, "a@example.com")
    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")
    add_member(session, team_a, user, role="admin")
    add_member(session, team_b, user, role="admin")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    kb_b = client.post(f"/api/teams/{team_b.id}/knowledge-bases", json={"name": "B库"}).json()

    resp = client.post(
        f"/api/teams/{team_a.id}/documents",
        data={"knowledge_base_id": kb_b["id"]},
        files={"file": ("a.txt", "内容", "text/plain")},
    )
    assert resp.status_code == 404


def test_cannot_download_other_team_document(client, session):
    user = make_user(session, "a@example.com")
    team_a = make_team(session, "团队A")
    team_b = make_team(session, "团队B")
    add_member(session, team_a, user, role="admin")
    add_member(session, team_b, user, role="admin")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    kb_b = client.post(f"/api/teams/{team_b.id}/knowledge-bases", json={"name": "B库"}).json()
    doc_b = client.post(
        f"/api/teams/{team_b.id}/documents",
        data={"knowledge_base_id": kb_b["id"]},
        files={"file": ("b.txt", "团队B的资料", "text/plain")},
    ).json()

    resp = client.get(f"/api/teams/{team_a.id}/documents/{doc_b['id']}/content")
    assert resp.status_code == 404
