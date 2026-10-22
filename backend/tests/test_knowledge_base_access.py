from __future__ import annotations

import io
import uuid

from sqlalchemy import select, text

from app.models import KnowledgeBase, KnowledgeBaseAccess
from tests.conftest import add_member, make_team, make_user


def _login(client, email: str, password: str = "password123") -> dict:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _switch(client, email: str) -> None:
    client.post("/api/auth/logout")
    _login(client, email)


def _setup(client, session):
    admin = make_user(session, "kb-admin@example.com", name="管理员")
    member = make_user(session, "kb-member@example.com", name="成员")
    viewer = make_user(session, "kb-viewer@example.com", name="查阅者")
    outsider = make_user(session, "kb-outsider@example.com", name="外部成员")
    team = make_team(session, "知识库团队")
    add_member(session, team, admin, "admin")
    add_member(session, team, member, "member")
    add_member(session, team, viewer, "viewer")
    other_team = make_team(session, "另一团队")
    add_member(session, other_team, outsider, "admin")
    _login(client, member.email)
    return admin, member, viewer, outsider, team, other_team


def _create(client, team_id, name: str, visibility: str = "team") -> dict:
    response = client.post(
        f"/api/teams/{team_id}/knowledge-bases",
        json={"name": name, "visibility": visibility},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _upload(client, team_id, kb_id) -> dict:
    response = client.post(
        f"/api/teams/{team_id}/documents",
        data={"knowledge_base_id": kb_id},
        files={"file": ("policy.txt", io.BytesIO("住宿上限五百元。".encode()), "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_access(client, team_id, kb_id, rows: list[tuple[uuid.UUID, str]]):
    return client.put(
        f"/api/teams/{team_id}/knowledge-bases/{kb_id}/access",
        json=[
            {"user_id": str(user_id), "permission": permission}
            for user_id, permission in rows
        ],
    )


def test_any_team_member_can_create_private_library_without_admin_visibility(
    client, session
):
    admin, member, _viewer, _outsider, team, _other_team = _setup(client, session)
    library = _create(client, team.id, "成员私人库", "private")
    assert library["permission"] == "manage"
    assert library["visibility"] == "private"

    _switch(client, admin.email)
    assert client.get(f"/api/teams/{team.id}/knowledge-bases").json() == []
    assert (
        client.get(
            f"/api/teams/{team.id}/documents",
            params={"knowledge_base_id": library["id"]},
        ).status_code
        == 404
    )
    assert admin.id != member.id


def test_private_library_explicit_access_and_revocation(client, session):
    _admin, member, viewer, _outsider, team, _other_team = _setup(client, session)
    library = _create(client, team.id, "私人资料", "private")
    response = _set_access(client, team.id, library["id"], [(viewer.id, "view")])
    assert response.status_code == 204, response.text

    _switch(client, viewer.email)
    rows = client.get(f"/api/teams/{team.id}/knowledge-bases").json()
    assert [(row["name"], row["permission"]) for row in rows] == [
        ("私人资料", "view")
    ]

    _switch(client, member.email)
    assert _set_access(client, team.id, library["id"], []).status_code == 204
    _switch(client, viewer.email)
    assert client.get(f"/api/teams/{team.id}/knowledge-bases").json() == []
    assert (
        client.get(
            f"/api/teams/{team.id}/documents",
            params={"knowledge_base_id": library["id"]},
        ).status_code
        == 404
    )


def test_team_library_permissions_and_explicit_promotion(client, session):
    _admin, member, viewer, _outsider, team, _other_team = _setup(client, session)
    library = _create(client, team.id, "共享资料", "team")
    assert _upload(client, team.id, library["id"])["status"] == "pending"

    _switch(client, viewer.email)
    assert (
        client.post(
            f"/api/teams/{team.id}/documents",
            data={"knowledge_base_id": library["id"]},
            files={"file": ("blocked.txt", io.BytesIO(b"blocked"), "text/plain")},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/teams/{team.id}/knowledge-bases/{library['id']}/folders",
            json={"name": "目录"},
        ).status_code
        == 403
    )

    _switch(client, member.email)
    # Personal access can only strengthen a visible team library.
    assert (
        _set_access(client, team.id, library["id"], [(viewer.id, "view")]).status_code
        == 422
    )
    promoted = _set_access(
        client, team.id, library["id"], [(viewer.id, "manage")]
    )
    assert promoted.status_code == 204, promoted.text
    access = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{library['id']}/access"
    ).json()
    viewer_access = next(row for row in access if row["user_id"] == str(viewer.id))
    assert viewer_access["permission"] == "manage"
    assert viewer_access["explicit_permission"] == "manage"
    assert viewer_access["source"] == "explicit"

    _switch(client, viewer.email)
    assert (
        client.post(
            f"/api/teams/{team.id}/knowledge-bases/{library['id']}/folders",
            json={"name": "可管理目录"},
        ).status_code
        == 201
    )


def test_cross_team_and_worker_publication_are_denied_after_revocation(
    client, session
):
    _admin, member, _viewer, outsider, team, other_team = _setup(client, session)
    library = _create(client, team.id, "撤权资料", "private")
    _switch(client, outsider.email)
    assert (
        client.get(
            f"/api/teams/{other_team.id}/knowledge-bases/{library['id']}/folders"
        ).status_code
        == 404
    )

    _switch(client, member.email)
    assert _set_access(client, team.id, library["id"], []).status_code == 204
    permission = session.execute(
        text("select app_kb_permission(:kb_id, :user_id)"),
        {"kb_id": library["id"], "user_id": member.id},
    ).scalar_one()
    assert permission == "manage"
    kb = session.get(KnowledgeBase, library["id"])
    assert kb is not None
    assert session.scalars(
        select(KnowledgeBaseAccess).where(
            KnowledgeBaseAccess.knowledge_base_id == library["id"],
            KnowledgeBaseAccess.user_id == member.id,
        )
    ).one_or_none() is None


def test_library_list_returns_effective_permissions_for_team_defaults(
    client, session
):
    admin, _member, viewer, _outsider, team, _other_team = _setup(client, session)
    library = _create(client, team.id, "全员查阅", "team")
    response = client.patch(
        f"/api/teams/{team.id}/knowledge-bases/{library['id']}",
        json={"team_permission": "view"},
    )
    assert response.status_code == 200, response.text

    _switch(client, viewer.email)
    rows = client.get(f"/api/teams/{team.id}/knowledge-bases").json()
    assert rows[0]["permission"] == "view"
    _switch(client, admin.email)
    rows = client.get(f"/api/teams/{team.id}/knowledge-bases").json()
    assert rows[0]["permission"] == "manage"


def test_private_library_access_survives_role_change_but_not_team_removal(
    client, session
):
    from app.models import TeamMembership

    admin, member, viewer, _outsider, team, _other_team = _setup(client, session)
    library = _create(client, team.id, "私人撤权资料", "private")
    assert _set_access(
        client, team.id, library["id"], [(viewer.id, "edit")]
    ).status_code == 204

    _switch(client, viewer.email)
    assert (
        client.get(
            f"/api/teams/{team.id}/knowledge-bases/{library['id']}/folders"
        ).status_code
        == 200
    )

    membership = session.scalars(
        select(TeamMembership).where(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == viewer.id,
        )
    ).one()
    session.delete(membership)
    session.commit()

    assert (
        client.get(
            f"/api/teams/{team.id}/knowledge-bases/{library['id']}/folders"
        ).status_code
        == 403
    )
    assert client.get(f"/api/teams/{team.id}/knowledge-bases").status_code == 403
    assert admin.id != member.id


def test_private_documents_and_products_deny_direct_sql_after_revocation(
    client, session
):
    from tests.conftest import AppSession

    _admin, member, viewer, _outsider, team, _other_team = _setup(client, session)
    library = _create(client, team.id, "数据库边界", "private")
    document = _upload(client, team.id, library["id"])
    assert _set_access(
        client, team.id, library["id"], [(viewer.id, "view")]
    ).status_code == 204
    _switch(client, viewer.email)
    assert (
        client.post(
            f"/api/teams/{team.id}/knowledge-bases/{library['id']}/notes",
            json={"title": "查看者笔记", "content": "仅本人可见", "citations": []},
        ).status_code
        == 201
    )

    restricted = AppSession()
    try:
        restricted.execute(
            text(
                "select set_config('kp.team_id', :team_id, true), "
                "set_config('kp.user_id', :user_id, true)"
            ),
            {"team_id": str(team.id), "user_id": str(viewer.id)},
        )
        assert (
            restricted.scalar(
                text("select count(*) from documents where id = :id"),
                {"id": document["id"]},
            )
            == 1
        )
        assert (
            restricted.scalar(text("select count(*) from knowledge_notes"))
            == 1
        )
        restricted.commit()

        restricted.execute(
            text(
                "select set_config('kp.team_id', :team_id, true), "
                "set_config('kp.user_id', :user_id, true)"
            ),
            {"team_id": str(team.id), "user_id": str(viewer.id)},
        )
        restricted.execute(
            text("delete from knowledge_base_access where user_id = :user_id"),
            {"user_id": viewer.id},
        )
        restricted.commit()
        restricted.execute(
            text(
                "select set_config('kp.team_id', :team_id, true), "
                "set_config('kp.user_id', :user_id, true)"
            ),
            {"team_id": str(team.id), "user_id": str(viewer.id)},
        )
        assert (
            restricted.scalar(
                text("select count(*) from documents where id = :id"),
                {"id": document["id"]},
            )
            == 0
        )
        assert (
            restricted.scalar(text("select count(*) from knowledge_notes"))
            == 0
        )
    finally:
        restricted.rollback()
        restricted.close()
    assert member.id != viewer.id
