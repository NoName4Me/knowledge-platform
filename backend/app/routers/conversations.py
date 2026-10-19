from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context
from app.config import get_settings
from app.conversations import (
    ConversationBusy,
    ConversationNotFound,
    answer_turn,
    begin_turn,
    create_conversation,
    get_owned_conversation,
    stream_answer,
    turn_data,
)
from app.db import apply_context, get_session
from app.models import Conversation, KnowledgeBase, Turn
from app.schemas import (
    ConversationCreate,
    ConversationOut,
    TurnAskRequest,
    TurnOut,
    TurnStreamEventOut,
)

router = APIRouter(
    prefix="/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}",
    tags=["conversations"],
)
settings = get_settings()


def _close_stale_turns(session: Session, conversation_id: uuid.UUID) -> None:
    """同步请求崩溃后不能永久占用会话；超时轮次需成为可重试终态。"""
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.turn_timeout_seconds)
    session.execute(
        text(
            """
            update turns
            set status = 'interrupted',
                error_code = 'TURN_TIMEOUT',
                finished_at = now()
            where conversation_id = :conversation_id
              and created_by_id = app_current_user()
              and status = 'answering'
              and started_at < :cutoff
            """
        ),
        {"conversation_id": conversation_id, "cutoff": cutoff},
    )


def _knowledge_base(
    session: Session, context: TeamContext, knowledge_base_id: uuid.UUID
) -> KnowledgeBase:
    kb = session.get(KnowledgeBase, knowledge_base_id)
    if kb is None or kb.team_id != context.team.id:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb


@router.post("/conversations", response_model=ConversationOut, status_code=201)
def new_conversation(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    payload: ConversationCreate,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> ConversationOut:
    kb = _knowledge_base(session, context, knowledge_base_id)
    conversation = create_conversation(session, context, kb, payload.title)
    session.commit()
    return ConversationOut.model_validate(conversation)


@router.get("/conversations", response_model=list[ConversationOut])
def list_conversations(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> list[ConversationOut]:
    _knowledge_base(session, context, knowledge_base_id)
    rows = session.scalars(
        select(Conversation)
        .where(
            Conversation.team_id == context.team.id,
            Conversation.knowledge_base_id == knowledge_base_id,
            Conversation.created_by_id == context.user.id,
        )
        .order_by(Conversation.updated_at.desc())
    ).all()
    return [ConversationOut.model_validate(row) for row in rows]


@router.get("/conversations/{conversation_id}/turns", response_model=list[TurnOut])
def list_turns(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> list[TurnOut]:
    try:
        get_owned_conversation(session, context, knowledge_base_id, conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    _close_stale_turns(session, conversation_id)
    session.commit()
    apply_context(session, user_id=context.user.id, team_id=context.team.id)
    turns = session.scalars(
        select(Turn).where(Turn.conversation_id == conversation_id).order_by(Turn.seq)
    ).all()
    return [turn_data(session, turn) for turn in turns]


@router.post("/conversations/{conversation_id}/turns", response_model=TurnOut)
def ask_in_conversation(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    payload: TurnAskRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> TurnOut:
    try:
        get_owned_conversation(session, context, knowledge_base_id, conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    _close_stale_turns(session, conversation_id)
    session.commit()
    apply_context(session, user_id=context.user.id, team_id=context.team.id)
    try:
        return answer_turn(
            session,
            context,
            knowledge_base_id,
            conversation_id,
            payload.client_request_id,
            payload.question.strip(),
        )
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except ConversationBusy as exc:
        raise HTTPException(status_code=409, detail="当前会话已有进行中的提问") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="回答服务暂不可用") from exc


@router.post("/conversations/{conversation_id}/turns/stream")
def ask_in_conversation_stream(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    conversation_id: uuid.UUID,
    payload: TurnAskRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> StreamingResponse:
    """SSE 流式回答；先持久化占位轮次，断流后可用轮次读取接口恢复。"""
    try:
        get_owned_conversation(session, context, knowledge_base_id, conversation_id)
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    _close_stale_turns(session, conversation_id)
    session.commit()
    apply_context(session, user_id=context.user.id, team_id=context.team.id)
    try:
        turn, reused = begin_turn(
            session,
            context,
            knowledge_base_id,
            conversation_id,
            payload.client_request_id,
            payload.question.strip(),
        )
    except ConversationNotFound as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    except ConversationBusy as exc:
        raise HTTPException(status_code=409, detail="当前会话已有进行中的提问") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def events() -> Iterator[str]:
        # 长连接使用独立会话；请求级会话会在返回响应体后提前关闭。
        from app.db import SessionLocal

        with SessionLocal() as stream_session:
            apply_context(
                stream_session, user_id=context.user.id, team_id=context.team.id
            )
            try:
                for event in stream_answer(
                    stream_session,
                    context,
                    knowledge_base_id,
                    turn.id,
                    payload.question.strip(),
                    reused=reused,
                ):
                    data = TurnStreamEventOut(
                        type=event.type,
                        turn=event.turn,
                        text=event.text,
                        error_code=event.error_code,
                        message=event.message,
                    )
                    yield f"data: {data.model_dump_json()}\n\n"
                yield "event: done\ndata: {}\n\n"
            finally:
                stream_session.rollback()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Request-ID": str(payload.client_request_id),
        },
    )
