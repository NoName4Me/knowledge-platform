from __future__ import annotations

import uuid

from sqlalchemy import select

from tests.conftest import add_member, make_team, make_user


def _setup(client, session):
    user = make_user(session, "upload@example.com")
    team = make_team(session, "上传团队")
    add_member(session, team, user, role="admin")
    client.post("/api/auth/login", json={"email": user.email, "password": "password123"})
    kb = client.post(
        f"/api/teams/{team.id}/knowledge-bases", json={"name": "资料库"}
    ).json()
    return team, kb["id"]


def _upload(client, team_id, kb_id, key: uuid.UUID, content: bytes = b"same content"):
    return client.post(
        f"/api/teams/{team_id}/documents",
        headers={"Idempotency-Key": str(key)},
        data={"knowledge_base_id": kb_id},
        files={"file": ("note.txt", content, "text/plain")},
    )


def test_reusing_idempotency_key_returns_same_document(client, session):
    team, kb_id = _setup(client, session)
    key = uuid.uuid4()

    first = _upload(client, team.id, kb_id, key)
    second = _upload(client, team.id, kb_id, key)

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert (
        len(client.get(f"/api/teams/{team.id}/documents?knowledge_base_id={kb_id}").json())
        == 1
    )


def test_same_idempotency_key_with_different_content_conflicts(client, session):
    team, kb_id = _setup(client, session)
    key = uuid.uuid4()

    assert _upload(client, team.id, kb_id, key, b"first").status_code == 201
    assert _upload(client, team.id, kb_id, key, b"second").status_code == 409


def test_same_content_with_different_keys_is_allowed(client, session):
    team, kb_id = _setup(client, session)
    assert _upload(client, team.id, kb_id, uuid.uuid4()).status_code == 201
    assert _upload(client, team.id, kb_id, uuid.uuid4()).status_code == 201


def test_deleted_document_is_hidden_and_quota_released_once(client, session):
    from app.models import Document, TeamQuota

    team, kb_id = _setup(client, session)
    created = _upload(client, team.id, kb_id, uuid.uuid4())
    assert created.status_code == 201
    document_id = created.json()["id"]

    session.expire_all()
    quota = session.get(TeamQuota, team.id)
    assert quota is not None
    before_docs = quota.reserved_documents
    before_bytes = quota.reserved_bytes

    assert client.delete(f"/api/teams/{team.id}/documents/{document_id}").status_code == 204
    session.expire_all()
    quota = session.get(TeamQuota, team.id)
    assert quota is not None
    assert quota.reserved_documents == before_docs - 1
    assert quota.reserved_bytes == before_bytes - created.json()["size_bytes"]

    # 重复删除是幂等的，额度不能再次减少。
    assert client.delete(f"/api/teams/{team.id}/documents/{document_id}").status_code == 204
    session.expire_all()
    quota = session.get(TeamQuota, team.id)
    assert quota is not None
    assert quota.reserved_documents == before_docs - 1

    documents = client.get(
        f"/api/teams/{team.id}/documents?knowledge_base_id={kb_id}"
    ).json()
    assert documents == []
    assert session.get(Document, document_id).status == "deleted"


def test_quota_rejects_when_document_limit_reached(client, session):
    from app.config import get_settings
    from app.models import TeamQuota

    team, kb_id = _setup(client, session)
    limit = get_settings().team_max_documents
    session.expire_all()
    quota = session.get(TeamQuota, team.id)
    assert quota is not None
    quota.reserved_documents = limit
    session.commit()

    assert _upload(client, team.id, kb_id, uuid.uuid4()).status_code == 409


def test_queued_task_is_invalidated_when_deleted(client, session):
    from app.models import Document, ProcessingTask

    team, kb_id = _setup(client, session)
    created = _upload(client, team.id, kb_id, uuid.uuid4()).json()
    assert (
        client.delete(f"/api/teams/{team.id}/documents/{created['id']}").status_code == 204
    )

    session.expire_all()
    task = session.scalars(
        select(ProcessingTask).where(ProcessingTask.document_id == created["id"])
    ).one()
    assert task.status == "canceled"
    assert session.get(Document, created["id"]).status == "deleted"


def test_failed_upload_can_be_retried(client, session):
    from app.models import Document, ProcessingTask

    team, kb_id = _setup(client, session)
    created = _upload(client, team.id, kb_id, uuid.uuid4()).json()
    session.expire_all()
    document = session.get(Document, created["id"])
    assert document is not None
    document.status = "failed"
    document.failure_reason = "临时解析失败"
    session.commit()

    retried = client.post(f"/api/teams/{team.id}/documents/{created['id']}/retry")
    assert retried.status_code == 202
    assert retried.json()["status"] == "pending"
    session.expire_all()
    assert session.get(Document, created["id"]).status == "pending"
    task = session.scalars(
        select(ProcessingTask).where(ProcessingTask.document_id == created["id"])
    ).one()
    assert task.status == "queued"


def test_reconciliation_queues_stale_upload_without_duplicate_task(client, session, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from app.models import Document, ProcessingTask
    from app.reconciliation import reconcile_uploads_once
    from app.storage import get_object_store

    team, kb_id = _setup(client, session)
    created = _upload(client, team.id, kb_id, uuid.uuid4()).json()
    session.expire_all()
    document = session.get(Document, created["id"])
    assert document is not None
    # 模拟“对象已写入、完成事务未提交”。
    document.status = "uploading"
    document.updated_at = datetime.now(UTC) - timedelta(minutes=31)
    task = session.scalars(
        select(ProcessingTask).where(ProcessingTask.document_id == document.id)
    ).one()
    session.delete(task)
    session.commit()

    get_object_store().put(document.storage_key, b"same content")
    result = reconcile_uploads_once(session)
    assert result.queued == 1
    session.expire_all()
    assert session.get(Document, document.id).status == "pending"
    assert session.scalars(
        select(ProcessingTask).where(ProcessingTask.document_id == document.id)
    ).one()

    # 第二次对账不重复创建任务。
    result = reconcile_uploads_once(session)
    assert result.queued == 0


def test_reconciliation_runs_as_restricted_role_across_teams(client, session):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from app.models import Document, ProcessingTask
    from app.reconciliation import reconcile_uploads_once
    from app.storage import get_object_store
    from tests.conftest import AppSession

    stale_ids: set[uuid.UUID] = set()
    for index in range(2):
        user = make_user(session, f"reconcile-{index}@example.com")
        team = make_team(session, f"对账团队{index}")
        add_member(session, team, user, role="admin")
        client.post(
            "/api/auth/login",
            json={"email": user.email, "password": "password123"},
        )
        kb = client.post(
            f"/api/teams/{team.id}/knowledge-bases",
            json={"name": f"资料库{index}"},
        ).json()
        created = _upload(
            client,
            team.id,
            kb["id"],
            uuid.uuid4(),
            content=f"对账内容{index}".encode(),
        ).json()
        session.expire_all()
        document = session.get(Document, created["id"])
        assert document is not None
        document.status = "uploading"
        document.updated_at = datetime.now(UTC) - timedelta(minutes=31)
        task = session.scalars(
            select(ProcessingTask).where(ProcessingTask.document_id == document.id)
        ).one()
        session.delete(task)
        session.commit()
        get_object_store().put(document.storage_key, f"对账内容{index}".encode())
        stale_ids.add(document.id)

    restricted = AppSession()
    try:
        assert restricted.execute(text("select current_user")).scalar_one() == "kp_app"
        assert (
            restricted.execute(text("select count(*) from documents")).scalar_one() == 0
        )
        restricted.commit()
        result = reconcile_uploads_once(restricted, limit=1)
        assert result.scanned == 1
        assert result.queued == 1
        result = reconcile_uploads_once(restricted, limit=10)
        assert result.scanned == 1
        assert result.queued == 1
    finally:
        restricted.close()

    session.expire_all()
    assert {document.id for document in session.scalars(select(Document)).all()} == stale_ids
    assert session.scalars(
        select(ProcessingTask).where(ProcessingTask.document_id.in_(stale_ids))
    ).all()
