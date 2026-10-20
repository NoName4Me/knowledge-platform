from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.models import Conversation, Document, DocumentFolder, KnowledgeBase
from tests.conftest import add_member, make_team, make_user


def setup_workspace(client, session):
    owner = make_user(session, "management@example.com")
    team = make_team(session, "管理验证")
    add_member(session, team, owner, "admin")
    client.post("/api/auth/login", json={"email": owner.email, "password": "password123"})
    base = f"/api/teams/{team.id}"
    kb = client.post(f"{base}/knowledge-bases", json={"name": "资料库"}).json()["id"]
    return owner, team, base, kb


def upload(client, base, kb, folder=None, key=None):
    data = {"knowledge_base_id": kb}
    if folder:
        data["folder_id"] = folder
    return client.post(
        f"{base}/documents",
        data=data,
        files={"file": ("original.txt", b"public sample", "text/plain")},
        headers={"Idempotency-Key": key or str(uuid.uuid4())},
    )


def test_folder_upload_rename_move_preserves_document_and_storage(client, session):
    _, _, base, kb = setup_workspace(client, session)
    path = f"{base}/knowledge-bases/{kb}/folders"
    folder = client.post(path, json={"name": "  制度  "})
    assert folder.status_code == 201
    folder_id = folder.json()["id"]
    doc = upload(client, base, kb, folder_id).json()
    original_key = session.get(Document, uuid.UUID(doc["id"])).storage_key
    response = client.patch(
        f"{base}/documents/{doc['id']}", json={"filename": "新制度.txt", "folder_id": None}
    )
    assert response.status_code == 200
    assert response.json()["filename"] == "新制度.txt"
    assert response.json()["title"] == "新制度"
    assert response.json()["folder_id"] is None
    session.expire_all()
    assert session.get(Document, uuid.UUID(doc["id"])).storage_key == original_key
    content = client.get(f"{base}/documents/{doc['id']}/content")
    assert content.status_code == 200
    assert content.content == b"public sample"
    assert "filename*=UTF-8''" in content.headers["content-disposition"]
    assert client.patch(f"{path}/{folder_id}", json={"name": "归档"}).status_code == 200
    assert client.delete(f"{path}/{folder_id}").status_code == 204


def test_folder_duplicate_names_invalid_names_and_nonempty_delete(client, session):
    _, _, base, kb = setup_workspace(client, session)
    path = f"{base}/knowledge-bases/{kb}/folders"
    folder = client.post(path, json={"name": "制度"}).json()["id"]
    assert client.post(path, json={"name": " 制度 "}).status_code == 409
    assert client.post(path, json={"name": "   "}).status_code == 422
    assert client.post(path, json={"name": "a/b"}).status_code == 422
    doc = upload(client, base, kb, folder).json()
    child = client.post(path, json={"name": "子目录", "parent_id": folder}).json()
    assert child["parent_id"] == folder
    assert client.post(path, json={"name": "子目录", "parent_id": folder}).status_code == 409
    assert client.delete(f"{path}/{folder}").status_code == 204
    session.expire_all()
    assert session.get(Document, uuid.UUID(doc["id"])).folder_id is None
    assert session.get(DocumentFolder, uuid.UUID(child["id"])).parent_id is None
    assert (
        client.get(f"{base}/documents", params={"knowledge_base_id": kb}).json()[0]["id"]
        == doc["id"]
    )


def test_folder_tree_move_rejects_cycles_and_respects_scope(client, session):
    _, _, base, kb = setup_workspace(client, session)
    path = f"{base}/knowledge-bases/{kb}/folders"
    root = client.post(path, json={"name": "一级"}).json()["id"]
    child = client.post(path, json={"name": "二级", "parent_id": root}).json()["id"]
    grandchild = client.post(path, json={"name": "三级", "parent_id": child}).json()["id"]
    assert client.patch(f"{path}/{child}/parent", json={"parent_id": root}).status_code == 200
    assert client.patch(f"{path}/{root}/parent", json={"parent_id": child}).status_code == 409
    assert client.patch(f"{path}/{root}/parent", json={"parent_id": grandchild}).status_code == 409
    moved = client.patch(f"{path}/{child}/parent", json={"parent_id": None})
    assert moved.json()["parent_id"] is None
    other_kb = client.post(f"{base}/knowledge-bases", json={"name": "结构库"}).json()["id"]
    alien = client.post(
        f"{base}/knowledge-bases/{other_kb}/folders", json={"name": "越界"}
    ).json()["id"]
    assert client.patch(f"{path}/{root}/parent", json={"parent_id": alien}).status_code == 404
    assert client.post(path, json={"name": "越界", "parent_id": alien}).status_code == 404


def test_move_and_upload_reject_other_library_and_team_folder(client, session):
    _, team, base, kb = setup_workspace(client, session)
    other_kb = client.post(f"{base}/knowledge-bases", json={"name": "另一库"}).json()["id"]
    path = f"{base}/knowledge-bases/{other_kb}/folders"
    folder_id = client.post(path, json={"name": "不能移动到这里"}).json()["id"]
    doc = upload(client, base, kb).json()
    assert upload(client, base, kb, folder_id).status_code == 404
    assert (
        client.patch(f"{base}/documents/{doc['id']}", json={"folder_id": folder_id}).status_code
        == 404
    )
    # 数据库复合外键也拒绝绕过接口后把资料放进其他知识库的文件夹。
    with pytest.raises(IntegrityError):
        session.execute(
            text("update documents set folder_id=:folder where id=:doc"),
            {"folder": folder_id, "doc": doc["id"]},
        )
        session.commit()
    session.rollback()
    alien_team = make_team(session, "隔离团队")
    alien_kb = KnowledgeBase(team_id=alien_team.id, name="隔离库")
    session.add(alien_kb)
    session.flush()
    alien_folder = DocumentFolder(
        team_id=alien_team.id, knowledge_base_id=alien_kb.id, name="隔离夹"
    )
    session.add(alien_folder)
    session.commit()
    assert (
        client.patch(
            f"{base}/documents/{doc['id']}", json={"folder_id": str(alien_folder.id)}
        ).status_code
        == 404
    )
    assert client.get(f"{base}/knowledge-bases/{alien_kb.id}/folders").status_code == 404
    assert (
        client.patch(f"{base}/knowledge-bases/{alien_kb.id}", json={"name": "非法"}).status_code
        == 404
    )
    assert client.delete(f"{base}/knowledge-bases/{alien_kb.id}").status_code == 404
    # 当前团队文件夹列表不会包含隔离团队。
    assert client.get(f"{base}/knowledge-bases/{kb}/folders").json() == []


def test_member_can_browse_upload_but_cannot_manage(client, session):
    _, team, base, kb = setup_workspace(client, session)
    path = f"{base}/knowledge-bases/{kb}/folders"
    folder = client.post(path, json={"name": "文件夹"}).json()["id"]
    doc = upload(client, base, kb).json()
    member = make_user(session, "reader@example.com")
    add_member(session, team, member)
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": member.email, "password": "password123"})
    assert client.get(path).status_code == 200
    assert upload(client, base, kb, folder).status_code == 201
    assert client.post(path, json={"name": "非法"}).status_code == 403
    assert client.patch(f"{path}/{folder}", json={"name": "非法"}).status_code == 403
    assert client.delete(f"{path}/{folder}").status_code == 403
    assert (
        client.patch(f"{base}/documents/{doc['id']}", json={"filename": "非法.txt"}).status_code
        == 403
    )
    assert client.patch(f"{base}/knowledge-bases/{kb}", json={"name": "非法"}).status_code == 403
    assert client.delete(f"{base}/knowledge-bases/{kb}").status_code == 403


def test_upload_idempotency_includes_folder_and_rename_preserves_replay(client, session):
    _, _, base, kb = setup_workspace(client, session)
    path = f"{base}/knowledge-bases/{kb}/folders"
    folder = client.post(path, json={"name": "文件夹"}).json()["id"]
    key = str(uuid.uuid4())
    doc = upload(client, base, kb, key=key).json()
    assert upload(client, base, kb, folder, key).status_code == 409
    assert (
        client.patch(f"{base}/documents/{doc['id']}", json={"filename": "改名.txt"}).status_code
        == 200
    )
    replay = upload(client, base, kb, key=key)
    assert replay.status_code == 200
    assert replay.json()["id"] == doc["id"]
    assert replay.json()["filename"] == "改名.txt"


@pytest.mark.parametrize("filename", ["  ", "bad.pdf", "bad/name.txt", "bad\\name.txt", None])
def test_rename_rejects_invalid_filename(client, session, filename):
    _, _, base, kb = setup_workspace(client, session)
    doc = upload(client, base, kb).json()
    assert (
        client.patch(f"{base}/documents/{doc['id']}", json={"filename": filename}).status_code
        == 422
    )


def test_kb_rename_duplicate_and_empty_delete(client, session):
    _, _, base, kb = setup_workspace(client, session)
    path = f"{base}/knowledge-bases"
    assert client.patch(f"{path}/{kb}", json={"name": "  新资料库  "}).json()["name"] == "新资料库"
    assert client.post(path, json={"name": "新资料库"}).status_code == 409
    assert client.patch(f"{path}/{kb}", json={"name": "   "}).status_code == 422
    assert client.delete(f"{path}/{kb}").status_code == 204
    assert client.get(path).json() == []
    assert client.get(f"{path}/{kb}/folders").status_code == 404


def test_kb_delete_cleans_documents_and_private_history(client, session):
    _, team, base, kb = setup_workspace(client, session)
    doc = upload(client, base, kb).json()
    assert client.delete(f"{base}/knowledge-bases/{kb}").status_code == 204
    private_kb = client.post(f"{base}/knowledge-bases", json={"name": "历史库"}).json()["id"]
    member = make_user(session, "private@example.com")
    add_member(session, team, member)
    conversation = Conversation(
        team_id=team.id,
        knowledge_base_id=uuid.UUID(private_kb),
        created_by_id=member.id,
        title="private secret",
    )
    session.add(conversation)
    session.commit()
    conversation_id = conversation.id
    document_id = uuid.UUID(doc["id"])
    response = client.delete(f"{base}/knowledge-bases/{private_kb}")
    assert response.status_code == 204
    assert "private secret" not in response.text
    session.expire_all()
    assert session.scalar(select(Conversation.id).where(Conversation.id == conversation_id)) is None
    assert session.get(Document, document_id) is None
    assert client.get(f"{base}/documents/{doc['id']}/content").status_code == 404
