from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.answering import (
    ConversationContext,
    get_answer_provider,
    indicates_insufficient_grounding,
)
from app.auth import TeamContext
from app.db import apply_context
from app.models import CitationRecord, Conversation, Document, KnowledgeBase, Turn
from app.retrieval import RetrievedChunk, retrieve
from app.schemas import Citation, TurnOut


class ConversationNotFound(Exception):
    pass


class ConversationBusy(Exception):
    pass


class InvalidDocumentScope(Exception):
    pass


class InvalidAnswer(Exception):
    pass


class SourceDeleted(Exception):
    """生成结束前来源已删除，本轮不得发布旧引用。"""


class AccessRevoked(Exception):
    """长连接生成期间成员关系或会话归属已失效。"""


@dataclass
class TurnStreamEvent:
    """SSE 事件载荷；与前端共享同一套字段名。"""

    type: str
    turn: TurnOut | None = None
    text: str | None = None
    error_code: str | None = None
    message: str | None = None


def _restore_rls_context(session: Session, context: TeamContext) -> None:
    """提交或回滚会结束事务，也同时清空事务级 RLS 上下文。"""
    apply_context(session, user_id=context.user.id, team_id=context.team.id)


def get_owned_conversation(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> Conversation:
    statement = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.team_id == context.team.id,
        Conversation.knowledge_base_id == knowledge_base_id,
        Conversation.created_by_id == context.user.id,
    )
    if for_update:
        statement = statement.with_for_update()
    conversation = session.scalars(statement).one_or_none()
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


def _citation_items(session: Session, turn: Turn) -> list[Citation]:
    rows = session.execute(
        select(CitationRecord, Document)
        .join(Document, Document.id == CitationRecord.document_id)
        .where(CitationRecord.turn_id == turn.id)
        .order_by(CitationRecord.ordinal)
    ).all()
    items: list[Citation] = []
    # 历史记录按使用顺序压缩保存 ordinal；正文可能实际使用 [2]、[5]。
    # 从同一份已保存正文恢复真实编号，保证抽屉和上标一一对应。
    references = sorted({int(value) for value in re.findall(r"\[(\d+)\]", turn.answer or "")})
    for index, (citation, document) in enumerate(rows):
        number = references[index] if len(references) == len(rows) else citation.ordinal
        if document.status == "deleted":
            items.append(
                Citation(
                    number=number,
                    document_id=citation.document_id,
                    document_title=citation.filename_snapshot,
                    locator_label=citation.locator_label_snapshot,
                    excerpt="",
                )
            )
        else:
            excerpt = ""
            if citation.chunk_id is not None:
                excerpt = (
                    session.scalar(
                        text("select content from document_chunks where id = :id"),
                        {"id": citation.chunk_id},
                    )
                    or ""
                )
            items.append(
                Citation(
                    number=number,
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


def _answer_is_insufficient(answer: str) -> bool:
    return indicates_insufficient_grounding(answer) and len(answer.strip()) <= 100


def _scope_key(document_ids: list[uuid.UUID] | None) -> list[str]:
    return [str(document_id) for document_id in document_ids or []]


def validate_document_scope(
    session: Session,
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    document_ids: list[uuid.UUID] | None,
) -> list[uuid.UUID] | None:
    """显式资料范围只能包含当前团队、当前知识库内可用的文档。"""
    if document_ids is None:
        return None
    unique_ids = list(dict.fromkeys(document_ids))
    if len(unique_ids) != len(document_ids):
        raise InvalidDocumentScope("资料范围中存在重复文档")
    available = set(
        session.scalars(
            select(Document.id).where(
                Document.id.in_(unique_ids),
                Document.team_id == team_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "ready",
                Document.deleted_at.is_(None),
            )
        ).all()
    )
    if available != set(unique_ids):
        raise InvalidDocumentScope("所选资料不存在、尚未处理完成或不在当前知识库")
    return unique_ids


def _conversation_context(session: Session, turn: Turn) -> ConversationContext:
    """只取当前用户最近几轮已完成内容，并限制注入模型的长度。"""
    if turn.seq <= 1:
        return ConversationContext()
    rows = session.execute(
        select(Turn.question, Turn.answer)
        .where(
            Turn.conversation_id == turn.conversation_id,
            Turn.created_by_id == turn.created_by_id,
            Turn.seq < turn.seq,
            Turn.status == "completed",
            Turn.answer.is_not(None),
        )
        .order_by(Turn.seq.desc())
        .limit(4)
    ).all()
    history: list[tuple[str, str]] = []
    remaining = 8000
    for question, answer in reversed(rows):
        answer = answer or ""
        question = question.strip()[:1000]
        answer = answer.strip()[:2500]
        if remaining < len(question) + len(answer):
            break
        history.append((question, answer))
        remaining -= len(question) + len(answer)
    return ConversationContext(history=tuple(history))


def _retrieval_question(question: str, context: ConversationContext) -> str:
    """将最近问题附在检索文本后，帮助代词和省略主题获得上下文。"""
    if not context.history:
        return question
    previous_question = context.history[-1][0]
    return f"{question}\n此前问题：{previous_question}"[:5000]


def begin_turn(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    client_request_id: uuid.UUID,
    question: str,
    document_ids: list[uuid.UUID] | None = None,
) -> tuple[Turn, bool]:
    """占位并返回 (轮次, 是否为已存在的重复请求)。

    流式路由需要在返回响应体之前完成占位提交，因此该步骤必须可独立调用。
    """
    # 以会话行作为提交闸门，串行裁决重复请求、进行中状态与轮次序号。
    conversation = get_owned_conversation(
        session,
        context,
        knowledge_base_id,
        conversation_id,
        for_update=True,
    )
    existing = session.scalars(
        select(Turn).where(
            Turn.conversation_id == conversation.id,
            Turn.client_request_id == client_request_id,
        )
    ).one_or_none()
    if existing is not None:
        if (
            existing.question != question
            or existing.scope_document_ids != _scope_key(document_ids)
        ):
            session.rollback()
            raise ValueError("同一请求标识对应不同问题或资料范围")
        session.commit()
        _restore_rls_context(session, context)
        refreshed = session.get(Turn, existing.id)
        if refreshed is None:
            raise ConversationNotFound
        return refreshed, True

    active = session.scalars(
        select(Turn).where(Turn.conversation_id == conversation.id, Turn.status == "answering")
    ).first()
    if active is not None:
        session.rollback()
        raise ConversationBusy

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
        scope_document_ids=_scope_key(document_ids),
        status="answering",
    )
    session.add(new_turn)
    session.flush()

    # 先提交占位轮次，客户端可在断线后读取状态；崩溃遗留由超时收敛。
    session.commit()
    _restore_rls_context(session, context)
    turn = session.get(Turn, new_turn.id)
    if turn is None:
        raise RuntimeError("轮次创建后丢失")
    return turn, False


def _live_documents(session: Session, chunks: list[RetrievedChunk]) -> set[uuid.UUID]:
    """生成结束前复核来源仍然可用；已删除来源不得继续发布旧引用。"""
    ids = {chunk.document_id for chunk in chunks}
    if not ids:
        return set()
    rows = session.scalars(
        select(Document.id).where(
            Document.id.in_(ids),
            Document.deleted_at.is_(None),
            Document.status == "ready",
        )
    ).all()
    return set(rows)


def _ensure_publish_access(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    turn: Turn,
) -> Conversation:
    """最终发布前重新读取授权事实，防止长连接沿用生成开始时的身份。"""
    membership = session.execute(
        text(
            """
            select 1
            from team_memberships
            where team_id = :team_id and user_id = :user_id
            """
        ),
        {"team_id": context.team.id, "user_id": context.user.id},
    ).one_or_none()
    if membership is None:
        raise AccessRevoked
    try:
        return get_owned_conversation(session, context, knowledge_base_id, turn.conversation_id)
    except ConversationNotFound as exc:
        raise AccessRevoked from exc


def _finish_insufficient(session: Session, context: TeamContext, turn: Turn) -> TurnOutData:
    turn.status = "insufficient"
    turn.answer = "依据不足：没有检索到能够支持回答的团队资料。"
    turn.answer_draft = None
    turn.finished_at = datetime.now(UTC)
    session.commit()
    _restore_rls_context(session, context)
    refreshed = session.get(Turn, turn.id)
    if refreshed is None:
        raise ConversationNotFound
    return turn_data(session, refreshed)


def _mark_failed(
    session: Session,
    context: TeamContext,
    turn_id: uuid.UUID,
    error_code: str,
) -> None:
    session.rollback()
    _restore_rls_context(session, context)
    failed = session.get(Turn, turn_id)
    if failed is not None:
        failed.status = "failed"
        failed.error_code = error_code
        failed.answer_draft = None
        failed.finished_at = datetime.now(UTC)
        session.commit()


def _publish_answer(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    turn: Turn,
    answer_text: str,
    chunks: list[RetrievedChunk],
) -> TurnOutData:
    """校验引用与来源后一次性发布最终答案。"""
    if _answer_is_insufficient(answer_text):
        return _finish_insufficient(session, context, turn)
    used = _validate_answer(answer_text, chunks)
    live = _live_documents(session, used)
    if {chunk.document_id for chunk in used} - live:
        raise SourceDeleted
    conversation = _ensure_publish_access(session, context, knowledge_base_id, turn)

    turn.answer = answer_text
    turn.answer_draft = None
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


def answer_turn(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    client_request_id: uuid.UUID,
    question: str,
    document_ids: list[uuid.UUID] | None = None,
) -> TurnOutData:
    document_ids = validate_document_scope(
        session, context.team.id, knowledge_base_id, document_ids
    )
    turn, reused = begin_turn(
        session,
        context,
        knowledge_base_id,
        conversation_id,
        client_request_id,
        question,
        document_ids,
    )
    if reused:
        return turn_data(session, turn)

    try:
        conversation_context = _conversation_context(session, turn)
        chunks = retrieve(
            session,
            team_id=context.team.id,
            knowledge_base_id=knowledge_base_id,
            question=_retrieval_question(question, conversation_context),
            document_ids=document_ids,
        )
        if not chunks:
            return _finish_insufficient(session, context, turn)
        result = get_answer_provider().answer(question, chunks, conversation_context)
    except Exception as exc:  # noqa: BLE001
        _mark_failed(session, context, turn.id, "MODEL_UNAVAILABLE")
        raise RuntimeError("回答生成失败") from exc

    try:
        return _publish_answer(session, context, knowledge_base_id, turn, result.answer, chunks)
    except SourceDeleted:
        _mark_failed(session, context, turn.id, "SOURCE_DELETED")
        raise RuntimeError("回答来源已删除") from None
    except AccessRevoked:
        _mark_failed(session, context, turn.id, "ACCESS_REVOKED")
        raise RuntimeError("回答权限已失效") from None
    except Exception as exc:  # noqa: BLE001
        _mark_failed(session, context, turn.id, "ANSWER_INVALID")
        raise RuntimeError("回答持久化失败") from exc


def stream_answer(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    turn_id: uuid.UUID,
    question: str,
    *,
    reused: bool = False,
) -> Iterator[TurnStreamEvent]:
    """已有占位轮次后增量生成，并把校验通过的答案一次性发布。

    调用方负责创建轮次（含重复请求与进行中判定），因此这里可以安全地
    在长连接中重新取用会话，避免请求级依赖在响应体发送前被关闭。
    """
    turn = session.get(Turn, turn_id)
    if turn is None:
        raise ConversationNotFound
    yield TurnStreamEvent(type="turn", turn=turn_data(session, turn))
    if reused:
        return

    try:
        scoped_document_ids = (
            [uuid.UUID(value) for value in turn.scope_document_ids]
            if turn.scope_document_ids
            else None
        )
        conversation_context = _conversation_context(session, turn)
        chunks = retrieve(
            session,
            team_id=context.team.id,
            knowledge_base_id=knowledge_base_id,
            question=_retrieval_question(question, conversation_context),
            document_ids=scoped_document_ids,
        )
    except Exception:  # noqa: BLE001
        _mark_failed(session, context, turn.id, "MODEL_UNAVAILABLE")
        yield TurnStreamEvent(type="error", error_code="MODEL_UNAVAILABLE", message="检索失败")
        return

    if not chunks:
        result = _finish_insufficient(session, context, turn)
        yield TurnStreamEvent(type="turn", turn=result)
        return

    provider = get_answer_provider()
    buffer: list[str] = []
    try:
        for piece in provider.stream(question, chunks, conversation_context):
            buffer.append(piece)
            # 草稿按块落库：断流后重新读取可看到已生成内容。
            turn.answer_draft = "".join(buffer)
            session.commit()
            _restore_rls_context(session, context)
            current_turn = session.get(Turn, turn.id)
            if current_turn is None:
                raise ConversationNotFound
            turn = current_turn
            yield TurnStreamEvent(type="delta", text=piece)
    except ConversationNotFound:
        raise
    except Exception:  # noqa: BLE001
        _mark_failed(session, context, turn.id, "MODEL_UNAVAILABLE")
        yield TurnStreamEvent(type="error", error_code="MODEL_UNAVAILABLE", message="生成中断")
        return

    answer_text = "".join(buffer)
    try:
        finished = _publish_answer(session, context, knowledge_base_id, turn, answer_text, chunks)
    except SourceDeleted:
        _mark_failed(session, context, turn.id, "SOURCE_DELETED")
        yield TurnStreamEvent(type="error", error_code="SOURCE_DELETED", message="引用来源已被删除")
        return
    except AccessRevoked:
        _mark_failed(session, context, turn.id, "ACCESS_REVOKED")
        yield TurnStreamEvent(
            type="error", error_code="ACCESS_REVOKED", message="当前访问权限已失效"
        )
        return
    except Exception:  # noqa: BLE001
        _mark_failed(session, context, turn.id, "ANSWER_INVALID")
        yield TurnStreamEvent(type="error", error_code="ANSWER_INVALID", message="回答校验失败")
        return
    yield TurnStreamEvent(type="turn", turn=finished)


def turn_data(session: Session, turn: Turn) -> TurnOutData:
    return TurnOut(
        id=turn.id,
        seq=turn.seq,
        client_request_id=turn.client_request_id,
        question=turn.question,
        scope_document_ids=[uuid.UUID(value) for value in (turn.scope_document_ids or [])],
        status=turn.status,
        answer=turn.answer,
        answer_draft=turn.answer_draft,
        error_code=turn.error_code,
        started_at=turn.started_at,
        finished_at=turn.finished_at,
        citations=_citation_items(session, turn),
    )


TurnOutData = TurnOut
