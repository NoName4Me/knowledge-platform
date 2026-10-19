from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.answering import get_answer_provider
from app.auth import TeamContext
from app.db import apply_context
from app.models import CitationRecord, Conversation, Document, KnowledgeBase, Turn
from app.retrieval import RetrievedChunk, retrieve
from app.schemas import Citation, TurnOut


class ConversationNotFound(Exception):
    pass


class ConversationBusy(Exception):
    pass


class InvalidAnswer(Exception):
    pass


def _restore_rls_context(session: Session, context: TeamContext) -> None:
    """提交或回滚会结束事务，也同时清空事务级 RLS 上下文。"""
    apply_context(session, user_id=context.user.id, team_id=context.team.id)


def get_owned_conversation(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> Conversation:
    conversation = session.scalars(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.team_id == context.team.id,
            Conversation.knowledge_base_id == knowledge_base_id,
            Conversation.created_by_id == context.user.id,
        )
    ).one_or_none()
    if conversation is None:
        raise ConversationNotFound
    return conversation


def create_conversation(
    session: Session,
    context: TeamContext,
    knowledge_base: KnowledgeBase,
    title: str | None,
) -> Conversation:
    conversation = Conversation(
        team_id=context.team.id,
        knowledge_base_id=knowledge_base.id,
        created_by_id=context.user.id,
        title=(title or "新会话").strip() or "新会话",
    )
    session.add(conversation)
    session.flush()
    return conversation


def _citation_items(
    session: Session, turn: Turn
) -> list[Citation]:
    rows = session.execute(
        select(CitationRecord, Document)
        .join(Document, Document.id == CitationRecord.document_id)
        .where(CitationRecord.turn_id == turn.id)
        .order_by(CitationRecord.ordinal)
    ).all()
    items: list[Citation] = []
    for citation, document in rows:
        if document.status == "deleted":
            items.append(
                Citation(
                    document_id=citation.document_id,
                    document_title=citation.filename_snapshot,
                    locator_label=citation.locator_label_snapshot,
                    excerpt="",
                )
            )
        else:
            excerpt = ""
            if citation.chunk_id is not None:
                excerpt = session.scalar(
                    text("select content from document_chunks where id = :id"),
                    {"id": citation.chunk_id},
                ) or ""
            items.append(
                Citation(
                    document_id=citation.document_id,
                    document_title=document.title,
                    locator_label=citation.locator_label_snapshot,
                    excerpt=excerpt[:400],
                )
            )
    return items


def _validate_answer(answer: str, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    references = {int(value) for value in re.findall(r"\[(\d+)\]", answer)}
    if not references or any(value < 1 or value > len(chunks) for value in references):
        raise InvalidAnswer
    return [chunks[value - 1] for value in sorted(references)]


def answer_turn(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    client_request_id: uuid.UUID,
    question: str,
) -> TurnOutData:
    conversation = get_owned_conversation(session, context, knowledge_base_id, conversation_id)
    existing = session.scalars(
        select(Turn).where(
            Turn.conversation_id == conversation.id,
            Turn.client_request_id == client_request_id,
        )
    ).one_or_none()
    if existing is not None:
        if existing.question != question:
            raise ValueError("同一请求标识对应不同问题")
        session.commit()
        _restore_rls_context(session, context)
        refreshed = session.get(Turn, existing.id)
        if refreshed is None:
            raise ConversationNotFound
        return turn_data(session, refreshed)

    seq = (
        session.scalar(select(func.max(Turn.seq)).where(Turn.conversation_id == conversation.id))
        or 0
    ) + 1
    new_turn = Turn(
        team_id=context.team.id,
        knowledge_base_id=knowledge_base_id,
        conversation_id=conversation.id,
        created_by_id=context.user.id,
        client_request_id=client_request_id,
        seq=seq,
        question=question,
        status="answering",
    )
    session.add(new_turn)
    session.flush()

    # 外部模型调用前提交占位轮次，失败时转为明确终态。
    session.commit()
    _restore_rls_context(session, context)
    turn = session.get(Turn, new_turn.id)
    if turn is None:
        raise RuntimeError("轮次创建后丢失")

    try:
        chunks = retrieve(
            session,
            team_id=context.team.id,
            knowledge_base_id=knowledge_base_id,
            question=question,
        )
        if not chunks:
            turn.status = "insufficient"
            turn.answer = "依据不足：没有检索到能够支持回答的团队资料。"
            turn.finished_at = datetime.now(UTC)
            session.commit()
            _restore_rls_context(session, context)
            refreshed = session.get(Turn, turn.id)
            if refreshed is None:
                raise ConversationNotFound
            return turn_data(session, refreshed)
        result = get_answer_provider().answer(question, chunks)
        used = _validate_answer(result.answer, chunks)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        _restore_rls_context(session, context)
        failed_turn = session.get(Turn, new_turn.id)
        if failed_turn is not None:
            failed_turn.status = "failed"
            failed_turn.error_code = "MODEL_UNAVAILABLE"
            failed_turn.finished_at = datetime.now(UTC)
            session.commit()
        raise RuntimeError("回答生成失败") from exc

    turn.answer = result.answer
    turn.status = "completed"
    turn.finished_at = datetime.now(UTC)
    for ordinal, chunk in enumerate(used, start=1):
        session.add(
            CitationRecord(
                team_id=context.team.id,
                knowledge_base_id=knowledge_base_id,
                turn_id=turn.id,
                owner_id=context.user.id,
                ordinal=ordinal,
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                filename_snapshot=chunk.document_title,
                locator_label_snapshot=chunk.locator_label,
            )
        )
    conversation.updated_at = datetime.now(UTC)
    session.commit()
    _restore_rls_context(session, context)
    refreshed = session.get(Turn, turn.id)
    if refreshed is None:
        raise ConversationNotFound
    return turn_data(session, refreshed)


def turn_data(session: Session, turn: Turn) -> TurnOutData:
    return TurnOut(
        id=turn.id,
        seq=turn.seq,
        client_request_id=turn.client_request_id,
        question=turn.question,
        status=turn.status,
        answer=turn.answer,
        error_code=turn.error_code,
        started_at=turn.started_at,
        finished_at=turn.finished_at,
        citations=_citation_items(session, turn),
    )


TurnOutData = TurnOut
