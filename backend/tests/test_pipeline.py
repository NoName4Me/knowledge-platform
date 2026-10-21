from __future__ import annotations

from sqlalchemy import select

from tests.conftest import add_member, make_team, make_user


def _prepare(client, session):
    user = make_user(session, "a@example.com")
    team = make_team(session, "团队A")
    add_member(session, team, user, role="admin")
    client.post("/api/auth/login", json={"email": "a@example.com", "password": "password123"})
    kb = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "制度库"}).json()
    return team, kb["id"]


def _upload(
    client, team_id, kb_id, filename="policy.txt", content="员工出差交通费用凭有效票据报销。"
):
    return client.post(
        f"/api/teams/{team_id}/documents",
        data={"knowledge_base_id": kb_id},
        files={"file": (filename, content, "text/plain")},
    ).json()


def _run_worker_once():
    from app.worker import run_once

    return run_once()


def test_upload_creates_task_and_worker_processes(client, session):
    from app.models import Document, ProcessingTask

    team, kb_id = _prepare(client, session)
    doc = _upload(client, team.id, kb_id)
    assert doc["status"] == "pending"

    assert _run_worker_once() is True

    session.expire_all()
    stored = session.get(Document, doc["id"])
    assert stored.status == "ready"

    tasks = session.scalars(
        select(ProcessingTask).where(ProcessingTask.document_id == doc["id"])
    ).all()
    assert len(tasks) == 1
    assert tasks[0].status == "succeeded"


def test_worker_is_idempotent_on_reprocess(client, session):
    from app.models import DocumentChunk, ProcessingTask

    team, kb_id = _prepare(client, session)
    doc = _upload(client, team.id, kb_id)
    _run_worker_once()

    first_count = len(
        session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc["id"])).all()
    )
    assert first_count >= 1

    task = session.scalars(
        select(ProcessingTask).where(ProcessingTask.document_id == doc["id"])
    ).one()
    task.status = "queued"
    task.attempts = 0
    session.commit()

    _run_worker_once()
    session.expire_all()
    second_count = len(
        session.scalars(select(DocumentChunk).where(DocumentChunk.document_id == doc["id"])).all()
    )
    assert second_count == first_count


def test_retrieval_scoped_to_team_and_kb(client, session):
    from app.retrieval import retrieve

    team, kb_id = _prepare(client, session)
    _upload(client, team.id, kb_id, content="公司报销制度：差旅交通费用凭有效票据报销。")
    _run_worker_once()

    results = retrieve(session, team.id, kb_id, "报销 交通费用", top_k=5)
    assert len(results) >= 1
    assert "报销" in results[0].content


def test_retrieval_excludes_other_team(client, session):
    from app.retrieval import retrieve

    team_a, kb_a = _prepare(client, session)
    _upload(client, team_a.id, kb_a, content="团队A的报销规定。")
    _run_worker_once()

    other_team = make_team(session, "团队B")
    results = retrieve(session, other_team.id, kb_a, "报销", top_k=5)
    assert results == []


def test_retrieval_rejects_unrelated_nearest_chunks(client, session):
    from app.retrieval import retrieve

    team, kb_id = _prepare(client, session)
    _upload(client, team.id, kb_id, content="公开资料只说明报销需要有效票据。")
    _run_worker_once()

    results = retrieve(
        session,
        team.id,
        kb_id,
        "PostgreSQL VACUUM 最多允许多少个并行工作进程？",
        distance_limit=0.0,
        min_lexical_score=1.0,
    )
    assert results == []


def test_retrieval_matches_keyword_even_when_vector_distance_is_large(client, session):
    from app.retrieval import retrieve

    team, kb_id = _prepare(client, session)
    _upload(
        client,
        team.id,
        kb_id,
        content="制度规定：差旅报销必须提交交通发票和出差审批单。",
    )
    _run_worker_once()

    results = retrieve(
        session,
        team.id,
        kb_id,
        "差旅报销需要哪些材料？",
        distance_limit=0.0,
        min_lexical_score=0.1,
    )
    assert results
    assert "交通发票" in results[0].content


def test_retrieval_keyword_recall_finds_identifier_outside_vector_candidates(client, session):
    from app.retrieval import retrieve

    team, kb_id = _prepare(client, session)
    for index in range(5):
        _upload(
            client,
            team.id,
            kb_id,
            filename=f"general-{index}.txt",
            content=f"普通说明第 {index + 1} 份，内容与本问题无关。",
        )
    target = _upload(
        client,
        team.id,
        kb_id,
        filename="numbered.txt",
        content="编号 FAQ-1042 对应备份保留周期为三十天。",
    )
    for _ in range(6):
        _run_worker_once()

    results = retrieve(
        session,
        team.id,
        kb_id,
        "FAQ-1042 的保留周期是多少？",
        top_k=1,
        candidate_k=1,
        distance_limit=0.0,
        min_lexical_score=0.1,
    )

    assert results
    assert str(results[0].document_id) == target["id"]
    assert "三十天" in results[0].content


def test_retrieval_respects_explicit_document_scope(client, session):
    from app.retrieval import retrieve

    team, kb_id = _prepare(client, session)
    selected = _upload(
        client,
        team.id,
        kb_id,
        filename="selected.txt",
        content="年假规定：五天。",
    )
    _upload(
        client,
        team.id,
        kb_id,
        filename="outside.txt",
        content="年假规定：十五天。",
    )
    _run_worker_once()
    _run_worker_once()

    results = retrieve(
        session,
        team.id,
        kb_id,
        "年假规定",
        document_ids=[selected["id"]],
    )

    assert results
    assert {str(result.document_id) for result in results} == {selected["id"]}


def test_ask_returns_citations(client, session):
    team, kb_id = _prepare(client, session)
    _upload(client, team.id, kb_id, content="年假规定：入职满一年可享五天年假。")
    _run_worker_once()

    resp = client.post(
        f"/api/teams/{team.id}/ask",
        json={"knowledge_base_id": kb_id, "question": "年假有几天"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["grounded"] is True
    assert len(body["citations"]) >= 1
    assert body["citations"][0]["document_title"] == "policy"


def test_ask_with_no_documents_reports_insufficient(client, session):
    team, kb_id = _prepare(client, session)
    resp = client.post(
        f"/api/teams/{team.id}/ask",
        json={"knowledge_base_id": kb_id, "question": "任意问题"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["grounded"] is False
    assert body["citations"] == []
    assert "依据不足" in body["answer"]


def test_ask_cannot_use_unknown_kb(client, session):
    team, _ = _prepare(client, session)
    resp = client.post(
        f"/api/teams/{team.id}/ask",
        json={
            "knowledge_base_id": "00000000-0000-0000-0000-000000000000",
            "question": "x",
        },
    )
    assert resp.status_code == 404


def test_failed_parse_marks_document_failed(client, session):
    from app.models import Document

    team, kb_id = _prepare(client, session)
    doc = _upload(client, team.id, kb_id, filename="note.txt", content="   ")
    _run_worker_once()
    session.expire_all()
    stored = session.get(Document, doc["id"])
    assert stored.status == "failed"
    assert stored.failure_reason


def test_expired_lease_is_reclaimed(client, session):
    from datetime import UTC, datetime, timedelta

    from app.models import ProcessingTask
    from app.tasks import reclaim_expired_tasks

    team, kb_id = _prepare(client, session)
    doc = _upload(client, team.id, kb_id)
    session.expire_all()
    task = session.scalars(
        select(ProcessingTask).where(ProcessingTask.document_id == doc["id"])
    ).one()
    task.status = "running"
    task.attempts = 1
    task.lease_expires_at = datetime.now(UTC) - timedelta(seconds=10)
    session.commit()

    reclaimed = reclaim_expired_tasks(session)
    session.commit()
    assert len(reclaimed) == 1
    assert reclaimed[0][0] == task.id
    assert reclaimed[0][1] == team.id
    assert reclaimed[0][2] is False
    session.expire_all()
    assert session.get(ProcessingTask, task.id).status == "queued"


def test_worker_cancels_claim_for_deleted_document(client, session):
    from app.db import apply_context
    from app.models import Document, DocumentChunk, ProcessingTask
    from app.tasks import claim_next_task
    from app.worker import process_task

    team, kb_id = _prepare(client, session)
    doc = _upload(client, team.id, kb_id)

    apply_context(session, team_id=team.id)
    claimed = claim_next_task(session)
    assert claimed is not None
    session.commit()

    session.expire_all()
    task = session.get(ProcessingTask, claimed[0])
    document = session.get(Document, doc["id"])
    assert task is not None and document is not None
    task.lease_token = claimed[2]
    document.status = "deleted"
    session.commit()

    apply_context(session, team_id=team.id)
    process_task(session, task, team.id, claimed[2])
    session.commit()

    session.expire_all()
    stored = session.get(Document, doc["id"])
    assert stored is not None and stored.status == "deleted"
    stored_task = session.get(ProcessingTask, task.id)
    assert stored_task is not None and stored_task.status == "canceled"
    assert (
        session.scalars(
            select(DocumentChunk).where(DocumentChunk.document_id == doc["id"])
        ).all()
        == []
    )
