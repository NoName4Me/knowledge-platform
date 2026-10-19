from __future__ import annotations

from tests.conftest import add_member, make_team, make_user


def test_admin_invites_new_member_once(client, session):
    admin = make_user(session, "admin@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, admin, role="admin")
    client.post(
        "/api/auth/login",
        json={"email": "admin@example.com", "password": "password123"},
    )

    created = client.post(
        f"/api/teams/{team.id}/invitations",
        json={"email": "new@example.com", "role": "member"},
    )
    assert created.status_code == 201
    token = created.json()["token"]

    registered = client.post(
        "/api/invitations/register",
        json={
            "token": token,
            "email": "new@example.com",
            "display_name": "新成员",
            "password": "password123",
        },
    )
    assert registered.status_code == 201
    assert registered.json()["teams"][0]["id"] == str(team.id)

    reused = client.post(
        "/api/invitations/register",
        json={
            "token": token,
            "email": "another@example.com",
            "display_name": "另一人",
            "password": "password123",
        },
    )
    assert reused.status_code == 409
