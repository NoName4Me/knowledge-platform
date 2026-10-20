from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tests.conftest import add_member, make_team, make_user


def _setup(client, session):
    user = make_user(session, "owner@example.com")
    team = make_team(session, "问答团队")
    add_member(session, team, user, role="admin")
    client.post("/api/auth/login", json={"email": user.email, "password": "password123"})
    kb = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "资料库"}).json()
    client.post(
        f"/api/teams/{team.id}/documents?knowledge_base_id={kb['id']}",
        data={"knowledge_base_id": kb["id"]},
        files={"file": ("policy.txt", "年假规定：入职满一年可享五天年假。", "text/plain")},
    )
    from app.worker import run_once

    run_once()
    return user, team, kb


def _conversation(client, team_id, kb_id):
    response = client.post(
        f"/api/teams/{team_id}/knowledge-bases/{kb_id}/conversations",
        json={"title": "年假咨询"},
    )
    assert response.status_code == 201
    return response.json()


def test_conversation_is_private_to_creator(client, session):
    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    admin = make_user(session, "other-admin@example.com")
    add_member(session, team, admin, role="admin")
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"email": admin.email, "password": "password123"})

    assert client.get(f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations").json() == []
    assert (
        client.get(
            f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/"
            f"{conversation['id']}/turns"
        ).status_code
        == 404
    )


def test_turn_is_persisted_with_citations(client, session):
    from app.models import CitationRecord, Turn

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    request_id = uuid.uuid4()
    response = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns",
        json={"client_request_id": str(request_id), "question": "年假有几天？"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["citations"]

    session.expire_all()
    assert len(session.scalars(select(Turn)).all()) == 1
    assert len(session.scalars(select(CitationRecord)).all()) >= 1
    history = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns"
    ).json()
    assert history[0]["id"] == body["id"]


def test_duplicate_turn_request_is_idempotent(client, session):
    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    request_id = str(uuid.uuid4())
    path = (
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns"
    )
    first = client.post(path, json={"client_request_id": request_id, "question": "年假？"})
    second = client.post(path, json={"client_request_id": request_id, "question": "年假？"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]

    conflict = client.post(path, json={"client_request_id": request_id, "question": "另一个问题"})
    assert conflict.status_code == 409


def _stream_events(response) -> list[dict]:
    import json

    events: list[dict] = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload and payload != "{}":
            events.append(json.loads(payload))
    return events


def test_stream_turn_emits_deltas_and_final_turn(client, session):
    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    request_id = uuid.uuid4()
    response = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/"
        f"{conversation['id']}/turns/stream",
        json={"client_request_id": str(request_id), "question": "年假有几天？"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _stream_events(response)
    assert events[0]["type"] == "turn"
    deltas = "".join(event["text"] for event in events if event["type"] == "delta")
    assert "五天" in deltas
    final = [event["turn"] for event in events if event["type"] == "turn"][-1]
    assert final["status"] == "completed"
    assert final["answer"] == deltas
    assert final["answer_draft"] is None
    assert final["citations"]


def test_stream_reuses_duplicate_request_without_second_turn(client, session):
    from app.models import Turn

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    path = (
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/"
        f"{conversation['id']}/turns/stream"
    )
    request_id = str(uuid.uuid4())
    first = _stream_events(
        client.post(
            path,
            json={"client_request_id": request_id, "question": "年假有几天？"},
        )
    )
    second = _stream_events(
        client.post(
            path,
            json={"client_request_id": request_id, "question": "年假有几天？"},
        )
    )
    assert first[-1]["turn"]["id"] == second[0]["turn"]["id"]
    assert second[0]["turn"]["status"] == "completed"
    assert len(session.scalars(select(Turn)).all()) == 1


def test_stream_draft_is_visible_after_interrupted_connection(client, session):
    from app.models import Turn

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    request_id = uuid.uuid4()
    stream = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/"
        f"{conversation['id']}/turns/stream",
        json={"client_request_id": str(request_id), "question": "年假有几天？"},
    )
    turn_id = uuid.UUID(_stream_events(stream)[0]["turn"]["id"])

    # 模拟已生成部分文本但进程/客户端在最终发布前断开。
    stored = session.get(Turn, turn_id)
    assert stored is not None
    stored.status = "answering"
    stored.answer = None
    stored.answer_draft = "年假规定：入职满一年可享"
    session.commit()

    history = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns"
    ).json()
    current = next(item for item in history if item["id"] == str(turn_id))
    assert current["answer_draft"] == "年假规定：入职满一年可享"


def test_stream_model_failure_emits_error_and_persists_failed_status(client, session, monkeypatch):
    from app import conversations

    class FailingProvider:
        def stream(self, question, chunks):
            yield "未完成的草稿"
            raise RuntimeError("model disconnected")

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    monkeypatch.setattr(conversations, "get_answer_provider", lambda: FailingProvider())
    response = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/"
        f"{conversation['id']}/turns/stream",
        json={"client_request_id": str(uuid.uuid4()), "question": "年假有几天？"},
    )
    events = _stream_events(response)
    assert [event["type"] for event in events][-1] == "error"
    assert events[-1]["error_code"] == "MODEL_UNAVAILABLE"

    history = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns"
    ).json()
    assert history[0]["status"] == "failed"
    assert history[0]["error_code"] == "MODEL_UNAVAILABLE"
    assert history[0]["answer_draft"] is None


def test_stream_revoked_membership_prevents_answer_publication(client, session, monkeypatch):
    from app import conversations
    from app.models import TeamMembership

    class RevokingProvider:
        def stream(self, question, chunks):
            membership = session.scalars(
                select(TeamMembership).where(
                    TeamMembership.user_id == owner.id,
                    TeamMembership.team_id == team.id,
                )
            ).one()
            session.delete(membership)
            session.commit()
            yield chunks[0].content
            yield "\n\n[1]"

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    monkeypatch.setattr(conversations, "get_answer_provider", lambda: RevokingProvider())
    response = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/"
        f"{conversation['id']}/turns/stream",
        json={"client_request_id": str(uuid.uuid4()), "question": "年假有几天？"},
    )
    events = _stream_events(response)
    assert events[-1]["type"] == "error"
    assert events[-1]["error_code"] == "ACCESS_REVOKED"
    assert not any(
        event["type"] == "turn" and event["turn"]["status"] == "completed" for event in events
    )

    session.expire_all()
    from app.models import Turn

    turn = session.scalars(select(Turn)).one()
    assert turn.status == "failed"
    assert turn.error_code == "ACCESS_REVOKED"
    assert turn.answer is None
    assert turn.answer_draft is None


def test_conversation_cannot_cross_knowledge_base(client, session):
    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    other_kb = client.post(f"/api/teams/{team.id}/knowledge-bases", json={"name": "其他库"}).json()

    path = (
        f"/api/teams/{team.id}/knowledge-bases/{other_kb['id']}/conversations/"
        f"{conversation['id']}/turns"
    )
    response = client.get(path)
    assert response.status_code == 404


def test_deleted_source_hides_excerpt_but_keeps_citation(client, session):
    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    response = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns",
        json={"client_request_id": str(uuid.uuid4()), "question": "年假有几天？"},
    )
    assert response.status_code == 200
    citation = response.json()["citations"][0]
    assert citation["excerpt"]

    assert (
        client.delete(f"/api/teams/{team.id}/documents/{citation['document_id']}").status_code
        == 204
    )
    history = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns"
    ).json()
    assert history[0]["citations"][0]["excerpt"] == ""
    assert history[0]["citations"][0]["document_title"] == citation["document_title"]


def test_busy_conversation_rejects_second_turn(client, session):
    from app.models import Conversation, Turn

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    stored_conversation = session.get(Conversation, conversation["id"])
    assert stored_conversation is not None
    session.add(
        Turn(
            team_id=team.id,
            knowledge_base_id=uuid.UUID(kb["id"]),
            conversation_id=stored_conversation.id,
            created_by_id=owner.id,
            client_request_id=uuid.uuid4(),
            seq=1,
            question="正在处理的问题",
            status="answering",
        )
    )
    session.commit()

    response = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns",
        json={"client_request_id": str(uuid.uuid4()), "question": "第二个问题"},
    )
    assert response.status_code == 409


def test_stale_answering_turn_is_interrupted_and_retryable(client, session):
    from app.models import Conversation, Turn

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    stored_conversation = session.get(Conversation, conversation["id"])
    assert stored_conversation is not None
    stale = Turn(
        team_id=team.id,
        knowledge_base_id=uuid.UUID(kb["id"]),
        conversation_id=stored_conversation.id,
        created_by_id=owner.id,
        client_request_id=uuid.uuid4(),
        seq=1,
        question="中断的问题",
        status="answering",
        started_at=datetime.now(UTC) - timedelta(minutes=10),
    )
    session.add(stale)
    session.commit()

    history = client.get(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns"
    ).json()
    assert history[0]["status"] == "interrupted"
    assert history[0]["error_code"] == "TURN_TIMEOUT"

    response = client.post(
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns",
        json={"client_request_id": str(uuid.uuid4()), "question": "重新提问"},
    )
    assert response.status_code == 200


def test_database_rejects_turn_sequence_race(client, session):
    from app.models import Conversation, Turn

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    stored = session.get(Conversation, conversation["id"])
    assert stored is not None
    session.add(
        Turn(
            team_id=team.id,
            knowledge_base_id=uuid.UUID(kb["id"]),
            conversation_id=stored.id,
            created_by_id=owner.id,
            client_request_id=uuid.uuid4(),
            seq=1,
            question="第一个问题",
            status="answering",
        )
    )
    session.commit()
    session.add(
        Turn(
            team_id=team.id,
            knowledge_base_id=uuid.UUID(kb["id"]),
            conversation_id=stored.id,
            created_by_id=owner.id,
            client_request_id=uuid.uuid4(),
            seq=2,
            question="并发问题",
            status="answering",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_same_request_id_concurrent_calls_create_one_turn(client, session):
    from app.conversations import ConversationNotFound, answer_turn, get_owned_conversation
    from app.db import apply_context
    from app.models import TeamMembership, Turn, User
    from tests.conftest import AppSession

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    request_id = uuid.uuid4()
    user = session.get(User, owner.id)
    membership = session.scalars(
        select(TeamMembership).where(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == owner.id,
        )
    ).one()
    assert user is not None

    def ask() -> uuid.UUID:
        from app.auth import TeamContext

        work = AppSession()
        try:
            context = TeamContext(user=user, team=team, role=membership.role)
            apply_context(work, user_id=user.id, team_id=team.id)
            get_owned_conversation(
                work,
                context,
                uuid.UUID(kb["id"]),
                uuid.UUID(conversation["id"]),
            )
            work.commit()
            apply_context(work, user_id=user.id, team_id=team.id)
            return answer_turn(
                work,
                context,
                uuid.UUID(kb["id"]),
                uuid.UUID(conversation["id"]),
                request_id,
                "年假有几天？",
            ).id
        except ConversationNotFound:
            raise
        finally:
            work.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: ask(), range(2)))
    assert len(set(ids)) == 1
    session.expire_all()
    turns = session.scalars(
        select(Turn).where(Turn.conversation_id == uuid.UUID(conversation["id"]))
    ).all()
    assert len(turns) == 1


def test_different_requests_concurrent_calls_publish_one_active_turn(client, session):
    from app.auth import TeamContext
    from app.conversations import ConversationBusy, answer_turn
    from app.db import apply_context
    from app.models import TeamMembership, Turn, User
    from tests.conftest import AppSession

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    user = session.get(User, owner.id)
    membership = session.scalars(
        select(TeamMembership).where(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == owner.id,
        )
    ).one()
    assert user is not None
    context = TeamContext(user=user, team=team, role=membership.role)

    def ask(request_id: uuid.UUID) -> str:
        work = AppSession()
        try:
            apply_context(work, user_id=user.id, team_id=team.id)
            try:
                answer_turn(
                    work,
                    context,
                    uuid.UUID(kb["id"]),
                    uuid.UUID(conversation["id"]),
                    request_id,
                    "年假有几天？",
                )
                return "ok"
            except ConversationBusy:
                return "busy"
        finally:
            work.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(ask, [uuid.uuid4(), uuid.uuid4()]))
    assert sorted(results) == ["busy", "ok"]
    session.expire_all()
    turns = session.scalars(
        select(Turn).where(Turn.conversation_id == uuid.UUID(conversation["id"]))
    ).all()
    assert len(turns) == 1


def test_history_citations_preserve_sparse_answer_numbers(client, session):
    """旧轮次 ordinal 为 1/2，但正文引用 2/5；展示必须恢复正文真实编号。"""
    from app.models import CitationRecord, Turn

    owner, team, kb = _setup(client, session)
    conversation = _conversation(client, team.id, kb["id"])
    path = (
        f"/api/teams/{team.id}/knowledge-bases/{kb['id']}/conversations/{conversation['id']}/turns"
    )
    created = client.post(
        path, json={"client_request_id": str(uuid.uuid4()), "question": "年假几天？"}
    )
    assert created.status_code == 200
    turn = session.get(Turn, uuid.UUID(created.json()["id"]))
    turn.answer = "第一条结论。[2]第二条结论。[5]"
    original = session.scalar(select(CitationRecord).where(CitationRecord.turn_id == turn.id))
    session.add(
        CitationRecord(
            team_id=team.id,
            knowledge_base_id=uuid.UUID(kb["id"]),
            turn_id=turn.id,
            owner_id=owner.id,
            ordinal=2,
            document_id=original.document_id,
            chunk_id=original.chunk_id,
            filename_snapshot=original.filename_snapshot,
            locator_label_snapshot=original.locator_label_snapshot,
        )
    )
    session.commit()
    result = client.get(path).json()[0]
    assert [citation["number"] for citation in result["citations"]] == [2, 5]
    assert all(citation["excerpt"] for citation in result["citations"])
    assert (
        client.delete(f"/api/teams/{team.id}/documents/{original.document_id}").status_code == 204
    )
    deleted = client.get(path).json()[0]
    assert [citation["number"] for citation in deleted["citations"]] == [2, 5]
    assert all(citation["excerpt"] == "" for citation in deleted["citations"])
