from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tests.conftest import add_member, make_team, make_user


def _queued_task(client, session):
    from app.models import ProcessingTask

    user = make_user(session, "worker@example.com")
    team = make_team(session, "任务团队")
    add_member(session, team, user, role="admin")
    client.post("/api/auth/login", json={"email": user.email, "password": "password123"})
    kb = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "资料"}).json()
    client.post(
        f"/api/teams/{team.id}/documents",
        data={"knowledge_base_id": kb["id"]},
        files={"file": ("a.txt", "用于任务测试的内容", "text/plain")},
    )
    session.expire_all()
    return team, session.scalars(select(ProcessingTask)).one()


def test_each_claim_gets_new_lease_token(client, session):
    from app.db import apply_context
    from app.tasks import claim_next_task

    team, first = _queued_task(client, session)
    apply_context(session, team_id=team.id)
    claimed = claim_next_task(session)
    session.commit()
    assert claimed is not None
    task_id, team_id, _owner_id, token = claimed
    assert (task_id, team_id) == (first.id, team.id)

    session.expire_all()
    first.lease_token = None
    first.status = "queued"
    session.commit()
    apply_context(session, team_id=team.id)
    second = claim_next_task(session)
    session.commit()
    assert second is not None
    assert second[3] != token


def test_stale_worker_cannot_renew_lease(client, session):
    from app.db import apply_context
    from app.models import ProcessingTask
    from app.tasks import LeaseLost, claim_next_task, renew_lease

    team, _ = _queued_task(client, session)
    apply_context(session, team_id=team.id)
    claimed = claim_next_task(session)
    assert claimed is not None
    task_id, _, _, old_token = claimed
    session.commit()

    session.expire_all()
    current = session.get(ProcessingTask, task_id)
    assert current is not None
    current.lease_token = None
    current.status = "queued"
    session.commit()

    apply_context(session, team_id=team.id)
    claimed_again = claim_next_task(session)
    assert claimed_again is not None
    _, _, _, new_token = claimed_again
    session.commit()

    session.expire_all()
    stale_view = session.get(ProcessingTask, task_id)
    assert stale_view is not None
    stale_view.lease_token = old_token
    with pytest.raises(LeaseLost):
        renew_lease(session, stale_view)
    session.rollback()


def test_two_workers_claim_different_tasks(client, session):
    """两个执行者并行领取时不能拿到同一条任务。"""
    from sqlalchemy import text

    from app.db import apply_context
    from app.tasks import claim_next_task
    from tests.conftest import AppSession

    team, first = _queued_task(client, session)
    kb_id = session.execute(
        text("select knowledge_base_id from documents where id = :doc_id"),
        {"doc_id": first.document_id},
    ).scalar_one()
    client.post(
        f"/api/teams/{team.id}/documents",
        data={"knowledge_base_id": str(kb_id)},
        files={"file": ("b.txt", "第二条任务", "text/plain")},
    )

    worker_a = AppSession()
    worker_b = AppSession()
    try:
        apply_context(worker_a, team_id=team.id)
        apply_context(worker_b, team_id=team.id)
        claimed_a = claim_next_task(worker_a)
        worker_a.commit()
        claimed_b = claim_next_task(worker_b)
        worker_b.commit()
    finally:
        worker_a.close()
        worker_b.close()

    assert claimed_a is not None and claimed_b is not None
    assert claimed_a[0] != claimed_b[0]
    assert claimed_a[3] != claimed_b[3]


def test_stale_worker_cannot_publish_chunks(client, session):
    from app.db import apply_context
    from app.models import Document, ProcessingTask
    from app.tasks import LeaseLost, claim_next_task, mark_succeeded

    team, _ = _queued_task(client, session)
    apply_context(session, team_id=team.id)
    claimed = claim_next_task(session)
    assert claimed is not None
    task_id, _, _, old_token = claimed
    session.commit()

    session.expire_all()
    task = session.get(ProcessingTask, task_id)
    assert task is not None
    task.lease_token = None
    task.status = "queued"
    document = session.get(Document, task.document_id)
    assert document is not None
    document.status = "pending"
    session.commit()

    apply_context(session, team_id=team.id)
    claimed_again = claim_next_task(session)
    assert claimed_again is not None
    _, _, _, new_token = claimed_again
    session.commit()

    stale = session.get(ProcessingTask, task_id)
    document = session.get(Document, task.document_id)
    assert stale is not None and document is not None
    stale.lease_token = old_token
    with pytest.raises(LeaseLost):
        mark_succeeded(session, stale, document)
    session.rollback()

    session.expire_all()
    current = session.get(ProcessingTask, task_id)
    assert current is not None and current.lease_token == new_token
    assert session.get(Document, document.id).status != "ready"


def test_expired_last_attempt_marks_document_failed(client, session):
    from app.db import apply_context
    from app.models import Document, ProcessingTask
    from app.tasks import claim_next_task, mark_exhausted_failed, reclaim_expired_tasks

    team, task = _queued_task(client, session)
    apply_context(session, team_id=team.id)
    claimed = claim_next_task(session)
    assert claimed is not None
    session.commit()

    session.expire_all()
    task = session.get(ProcessingTask, claimed[0])
    assert task is not None
    task.attempts = task.max_attempts
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()

    reclaimed = reclaim_expired_tasks(session)
    assert reclaimed and reclaimed[0][3] is True
    session.commit()
    mark_exhausted_failed(session, team.id, task.id, reclaimed[0][2])
    session.commit()

    session.expire_all()
    assert session.get(ProcessingTask, task.id).status == "failed"
    document = session.get(Document, task.document_id)
    assert document is not None and document.status == "failed"
