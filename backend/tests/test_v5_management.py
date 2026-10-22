from __future__ import annotations

import uuid

from sqlalchemy import select, text

from app.models import Document, KnowledgeBase, ProcessingTask, TeamQuota, User
from tests.conftest import add_member, make_platform_admin, make_team, make_user


def _login(client, email: str, password: str = "password123") -> dict:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _admin_team(client, session, *, platform: bool = False):
    if platform:
        actor = make_platform_admin(session, "platform@example.com")
    else:
        actor = make_user(session, "actor@example.com")
    team = make_team(session, "权限团队")
    admin = make_user(session, "team-admin@example.com")
    add_member(session, team, admin, "admin")
    _login(client, actor.email)
    return actor, team, admin


def test_platform_admin_creates_team_with_existing_account(client, session):
    actor = make_platform_admin(session, "platform@example.com")
    administrator = make_user(session, "owner@example.com")
    _login(client, actor.email)

    response = client.post(
        "/api/platform/teams",
        json={
            "name": "新团队",
            "description": "团队说明",
            "administrator_email": administrator.email,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    membership = session.execute(
        text(
            "select role from team_memberships "
            "where team_id = :team_id and user_id = :user_id"
        ),
        {"team_id": body["id"], "user_id": administrator.id},
    ).one()
    assert membership.role == "admin"
    assert body["administrator_count"] == 1
    assert body["administrator_invitation_token"] is None


def test_unregistered_team_administrator_invitation_can_be_redeemed(client, session):
    actor = make_platform_admin(session, "platform@example.com")
    _login(client, actor.email)
    created = client.post(
        "/api/platform/teams",
        json={
            "name": "待交接团队",
            "administrator_email": "invited-admin@example.com",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    token = body["administrator_invitation_token"]
    assert token
    assert body["pending_administrator_invitation_count"] == 1

    preview = client.post("/api/invitations/preview", json={"token": token})
    assert preview.status_code == 200
    assert preview.json() == {
        "status": "ok",
        "team_name": "待交接团队",
        "email_masked": "i***@example.com",
        "email": "invited-admin@example.com",
    }
    registered = client.post(
        "/api/invitations/register",
        json={
            "token": token,
            "email": "invited-admin@example.com",
            "display_name": "受邀管理员",
            "password": "password123",
        },
    )
    assert registered.status_code == 201, registered.text
    team = registered.json()["teams"][0]
    assert team["id"] == body["id"]
    assert team["name"] == "待交接团队"
    assert team["role"] == "admin"

    reused = client.post(
        "/api/invitations/preview",
        json={"token": token},
    )
    assert reused.json()["status"] == "used"


def test_assigning_new_administrator_revokes_old_administrator_invitation(client, session):
    actor = make_platform_admin(session, "platform@example.com")
    initial_admin = make_user(session, "initial-admin@example.com")
    team = make_team(session, "邀请交接团队")
    add_member(session, team, initial_admin, "admin")
    _login(client, actor.email)
    first = client.post(
        f"/api/platform/teams/{team.id}/administrator",
        json={"administrator_email": "first@example.com"},
    )
    assert first.status_code == 200, first.text
    second = client.post(
        f"/api/platform/teams/{team.id}/administrator",
        json={"administrator_email": "second@example.com"},
    )
    assert second.status_code == 200, second.text

    first_preview = client.post(
        "/api/invitations/preview",
        json={"token": first.json()["administrator_invitation_token"]},
    )
    second_preview = client.post(
        "/api/invitations/preview",
        json={"token": second.json()["administrator_invitation_token"]},
    )
    assert first_preview.json()["status"] == "expired"
    assert second_preview.json()["email"] == "second@example.com"


def test_platform_endpoints_reject_team_administrator(client, session):
    _, team, admin = _admin_team(client, session)
    assert client.get("/api/platform/teams").status_code == 403
    assert (
        client.patch(
            f"/api/platform/teams/{team.id}/status",
            json={"is_active": False},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/platform/users/{admin.id}/reset-password",
            json={"temporary_password": "user1234"},
        ).status_code
        == 403
    )


def test_account_deactivation_requires_admin_handoff(client, session):
    actor, team, admin = _admin_team(client, session, platform=True)
    blocked = client.patch(
        f"/api/platform/users/{admin.id}/status",
        json={"is_active": False},
    )
    assert blocked.status_code == 409
    assert "权限团队" in blocked.json()["detail"]

    second = make_user(session, "second-admin@example.com")
    add_member(session, team, second, "admin")
    allowed = client.patch(
        f"/api/platform/users/{admin.id}/status",
        json={"is_active": False},
    )
    assert allowed.status_code == 200
    assert allowed.json()["is_active"] is False
    assert actor.email == "platform@example.com"


def test_disabled_team_denies_access_without_deleting_data(client, session):
    actor = make_platform_admin(session, "platform@example.com")
    admin = make_user(session, "disabled-team-admin@example.com")
    team = make_team(session, "可停用团队")
    add_member(session, team, admin, "admin")
    knowledge_base = KnowledgeBase(team_id=team.id, name="保留资料库")
    session.add(knowledge_base)
    session.commit()
    _login(client, actor.email)

    disabled = client.patch(
        f"/api/platform/teams/{team.id}/status",
        json={"is_active": False},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["is_active"] is False
    session.expire_all()
    assert session.get(KnowledgeBase, knowledge_base.id) is not None

    client.post("/api/auth/logout")
    _login(client, admin.email)
    assert client.get(f"/api/teams/{team.id}").status_code == 403
    assert client.get("/api/platform/teams").status_code == 403

    client.post("/api/auth/logout")
    _login(client, actor.email)
    enabled = client.patch(
        f"/api/platform/teams/{team.id}/status",
        json={"is_active": True},
    )
    assert enabled.status_code == 200


def test_password_reset_revokes_sessions_and_forces_change(client, session):
    actor = make_platform_admin(session, "platform@example.com")
    target = make_user(session, "target@example.com", password="old-password-123")
    team = make_team(session, "密码团队")
    add_member(session, team, target, "viewer")
    _login(client, target.email, "old-password-123")
    old_csrf = client.get("/api/auth/me").json()["csrf_token"]

    client.post("/api/auth/logout")
    _login(client, actor.email)
    reset = client.post(
        f"/api/platform/users/{target.id}/reset-password",
        json={"temporary_password": "user1234"},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["must_change_password"] is True

    # 平台操作会清除目标账号的全部旧会话。
    assert session.scalar(
        text("select count(*) from session_tokens where user_id = :user_id"),
        {"user_id": target.id},
    ) == 0
    assert old_csrf

    client.post("/api/auth/logout")
    logged_in = _login(client, target.email, "user1234")
    assert logged_in["user"]["must_change_password"] is True
    assert client.get(f"/api/teams/{team.id}").status_code == 403

    changed = client.post(
        "/api/auth/password",
        json={
            "current_password": "user1234",
            "new_password": "new-password-123",
        },
    )
    assert changed.status_code == 204
    client.post("/api/auth/logout")
    _login(client, target.email, "new-password-123")
    assert client.get(f"/api/teams/{team.id}").status_code == 200


def test_platform_metadata_does_not_expose_private_team_data(client, session):
    actor = make_platform_admin(session, "platform@example.com")
    team = make_team(session, "私有团队")
    knowledge_base = KnowledgeBase(team_id=team.id, name="秘密知识库")
    session.add(knowledge_base)
    session.commit()
    _login(client, actor.email)

    teams = client.get("/api/platform/teams").json()
    users = client.get("/api/platform/users").json()
    assert "秘密知识库" not in str(teams)
    assert "password_hash" not in str(users).lower()
    assert client.get(f"/api/teams/{team.id}").status_code == 403


def test_team_administrator_can_reset_member_password_with_fixed_value(client, session):
    _, team, admin = _admin_team(client, session)
    member = make_user(session, "reset-me@example.com", password="previous-password")
    add_member(session, team, member, "member")
    _login(client, member.email, "previous-password")
    client.post("/api/auth/logout")
    _login(client, admin.email)

    response = client.post(
        f"/api/teams/{team.id}/members/{member.id}/reset-password",
    )
    assert response.status_code == 204, response.text
    assert session.scalar(
        text("select count(*) from session_tokens where user_id = :user_id"),
        {"user_id": member.id},
    ) == 0

    client.post("/api/auth/logout")
    logged_in = _login(client, member.email, "user1234")
    assert logged_in["user"]["must_change_password"] is True
    assert client.get(f"/api/teams/{team.id}").status_code == 403
    changed = client.post(
        "/api/auth/password",
        json={
            "current_password": "user1234",
            "new_password": "new-member-password",
        },
    )
    assert changed.status_code == 204

    other = make_user(session, "outside@example.com")
    other_team = make_team(session, "其他团队")
    add_member(session, other_team, other, "member")
    client.post("/api/auth/logout")
    _login(client, admin.email)
    assert (
        client.post(
            f"/api/teams/{team.id}/members/{other.id}/reset-password"
        ).status_code
        == 404
    )

    client.post("/api/auth/logout")
    _login(client, member.email, "new-member-password")
    assert (
        client.post(
            f"/api/teams/{team.id}/members/{admin.id}/reset-password"
        ).status_code
        == 403
    )


def test_disabled_uploader_blocks_new_parsing_claims(client, session):
    actor = make_platform_admin(session, "platform@example.com")
    uploader = make_user(session, "uploader@example.com")
    team = make_team(session, "解析团队")
    add_member(session, team, uploader, "member")
    knowledge_base = KnowledgeBase(team_id=team.id, name="解析库")
    session.add(knowledge_base)
    session.flush()
    document = Document(
        team_id=team.id,
        knowledge_base_id=knowledge_base.id,
        uploaded_by_id=uploader.id,
        title="待解析",
        filename="pending.txt",
        content_type="text/plain",
        size_bytes=1,
        storage_key=f"teams/{team.id}/{uuid.uuid4()}.txt",
        status="pending",
    )
    session.add(document)
    session.flush()
    task = ProcessingTask(team_id=team.id, document_id=document.id)
    session.add(task)
    session.commit()
    uploader.is_active = False
    session.commit()

    row = session.execute(
        text("select count(*) from app_claim_task(120)")
    ).scalar_one()
    assert row == 0
    assert actor.email == "platform@example.com"


def test_last_active_administrator_cannot_be_demoted_or_leave(client, session):
    admin = make_user(session, "admin@example.com")
    team = make_team(session, "单管理员团队")
    add_member(session, team, admin, "admin")
    _login(client, admin.email)
    assert (
        client.patch(
            f"/api/teams/{team.id}/members/{admin.id}/role",
            json={"role": "viewer"},
        ).status_code
        == 409
    )
    assert client.delete(f"/api/teams/{team.id}/leave").status_code == 409
    resolved = session.scalar(
        select(User.is_active).where(User.id == admin.id)
    )
    assert resolved is True


def test_viewer_can_read_and_use_knowledge_but_cannot_write_public_content(
    client, session
):
    admin = make_user(session, "viewer-admin@example.com")
    viewer = make_user(session, "viewer@example.com")
    team = make_team(session, "查阅团队")
    add_member(session, team, admin, "admin")
    add_member(session, team, viewer, "viewer")
    _login(client, admin.email)
    base = f"/api/teams/{team.id}"
    kb_id = client.post(f"{base}/knowledge-bases", json={"name": "共享资料"}).json()["id"]
    folder_id = client.post(
        f"{base}/knowledge-bases/{kb_id}/folders", json={"name": "制度"}
    ).json()["id"]
    uploaded = client.post(
        f"{base}/documents",
        data={"knowledge_base_id": kb_id, "folder_id": folder_id},
        files={"file": ("制度.txt", "差旅住宿上限为 500 元。", "text/plain")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert uploaded.status_code == 201, uploaded.text
    document = uploaded.json()
    from app.worker import run_once

    for _ in range(5):
        run_once()
        session.expire_all()
        listed = client.get(
            f"{base}/documents", params={"knowledge_base_id": kb_id}
        ).json()
        current = next(item for item in listed if item["id"] == document["id"])
        if current["status"] == "ready":
            break
    else:
        raise AssertionError(f"资料未就绪：{current['status']}")

    client.post("/api/auth/logout")
    _login(client, viewer.email)
    assert client.get(f"{base}/knowledge-bases").status_code == 200
    assert client.get(
        f"{base}/documents", params={"knowledge_base_id": kb_id}
    ).status_code == 200
    assert client.get(f"{base}/knowledge-bases/{kb_id}/folders").status_code == 200
    assert client.get(f"{base}/documents/{document['id']}/content").status_code == 200
    assert (
        client.post(
            f"{base}/ask",
            json={"knowledge_base_id": kb_id, "question": "住宿上限是多少？"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{base}/knowledge-bases/{kb_id}/notes",
            json={"title": "本人笔记", "content": "仅本人可见", "citations": []},
        ).status_code
        == 201
    )
    report = client.post(
        f"{base}/knowledge-bases/{kb_id}/reports/from-sources",
        json={
            "title": "本人报告",
            "document_ids": [document["id"]],
            "purpose": "核对",
            "focus": "住宿",
            "length": "short",
        },
    )
    assert report.status_code == 201, report.text
    analysis = client.post(
        f"{base}/knowledge-bases/{kb_id}/analyses",
        json={
            "kind": "extraction",
            "document_ids": [document["id"]],
            "fields": [
                {
                    "name": "住宿上限",
                    "description": "住宿费用上限",
                    "value_type": "number",
                }
            ],
        },
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert analysis.status_code == 202, analysis.text

    key = str(uuid.uuid4())
    assert (
        client.post(
            f"{base}/documents",
            data={"knowledge_base_id": kb_id, "folder_id": folder_id},
            files={"file": ("越权.txt", "不能写入", "text/plain")},
            headers={"Idempotency-Key": key},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{base}/documents/text",
            json={
                "knowledge_base_id": kb_id,
                "folder_id": folder_id,
                "title": "越权粘贴",
                "content": "不能写入",
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{base}/documents/web",
            json={
                "knowledge_base_id": kb_id,
                "folder_id": folder_id,
                "url": "https://example.com",
            },
        ).status_code
        == 403
    )
    assert client.post(f"{base}/documents/{document['id']}/retry").status_code == 403
    assert (
        client.patch(
            f"{base}/documents/{document['id']}", json={"filename": "越权.txt"}
        ).status_code
        == 403
    )
    assert client.delete(f"{base}/documents/{document['id']}").status_code == 403
    assert (
        client.post(
            f"{base}/documents/{document['id']}/replace",
            data={"knowledge_base_id": kb_id},
            files={"file": ("替换.txt", "不能替换", "text/plain")},
        ).status_code
        == 403
    )
    folders = f"{base}/knowledge-bases/{kb_id}/folders"
    assert client.post(folders, json={"name": "越权目录"}).status_code == 403
    assert client.patch(f"{folders}/{folder_id}", json={"name": "越权"}).status_code == 403
    assert (
        client.patch(
            f"{folders}/{folder_id}/parent", json={"parent_id": None}
        ).status_code
        == 403
    )
    assert client.delete(f"{folders}/{folder_id}").status_code == 403
    assert client.get(f"{base}/members").status_code == 403


def test_member_uploads_into_existing_folder_without_directory_management(
    client, session
):
    admin = make_user(session, "folder-admin@example.com")
    member = make_user(session, "folder-member@example.com")
    team = make_team(session, "成员上传团队")
    add_member(session, team, admin, "admin")
    add_member(session, team, member, "member")
    _login(client, admin.email)
    base = f"/api/teams/{team.id}"
    kb_id = client.post(f"{base}/knowledge-bases", json={"name": "成员资料"}).json()["id"]
    folder_id = client.post(
        f"{base}/knowledge-bases/{kb_id}/folders", json={"name": "已有目录"}
    ).json()["id"]

    client.post("/api/auth/logout")
    _login(client, member.email)
    uploaded = client.post(
        f"{base}/documents",
        data={"knowledge_base_id": kb_id, "folder_id": folder_id},
        files={"file": ("成员上传.txt", "成员可添加公共资料", "text/plain")},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["folder_id"] == folder_id
    assert (
        client.post(
            f"{base}/documents/text",
            json={
                "knowledge_base_id": kb_id,
                "folder_id": folder_id,
                "title": "粘贴资料",
                "content": "成员可添加",
            },
        ).status_code
        == 201
    )
    folders = f"{base}/knowledge-bases/{kb_id}/folders"
    assert client.post(folders, json={"name": "无权目录"}).status_code == 403
    assert client.patch(f"{folders}/{folder_id}", json={"name": "无权改名"}).status_code == 403
    assert client.delete(f"{folders}/{folder_id}").status_code == 403
    assert client.get(f"{base}/members").status_code == 403
    session.expire_all()
    assert session.scalar(
        select(TeamQuota.reserved_documents).where(TeamQuota.team_id == team.id)
    ) == 2
