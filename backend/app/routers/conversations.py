from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context
from app.conversations import (
    ConversationBusy,
    ConversationNotFound,
    answer_turn,
    create_conversation,
    get_owned_conversation,
    turn_data,
)
from app.db import get_session
from app.models import Conversation, KnowledgeBase, Turn
from app.schemas import (
    ConversationCreate,
    ConversationOut,
    TurnAskRequest,
    TurnOut,
)

router = APIRouter(
    prefix="/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}",
    tags=["conversations"],
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
    turns = session.scalars(
        select(Turn)
        .where(
            Turn.conversation_id == conversation_id,
            Turn.created_by_id == context.user.id,
        )
        .order_by(Turn.seq)
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
