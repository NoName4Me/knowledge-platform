from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context, require_admin
from app.db import get_session
from app.models import KnowledgeBase
from app.schemas import TeamSummary

router = APIRouter(prefix="/api/teams", tags=["teams"])


class KnowledgeBaseOut(BaseModel):
    id: uuid.UUID
    name: str


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.get("/{team_id}", response_model=TeamSummary)
def get_team(context: TeamContext = Depends(get_team_context)) -> TeamSummary:
    return TeamSummary(id=context.team.id, name=context.team.name, role=context.role)


@router.get("/{team_id}/knowledge-bases", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(
    team_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> list[KnowledgeBaseOut]:
    stmt = (
        select(KnowledgeBase)
        .where(KnowledgeBase.team_id == context.team.id)
        .order_by(KnowledgeBase.name)
    )
    return [KnowledgeBaseOut(id=kb.id, name=kb.name) for kb in session.scalars(stmt)]


@router.post("/{team_id}/knowledge-bases", response_model=KnowledgeBaseOut, status_code=201)
def create_knowledge_base(
    team_id: uuid.UUID,
    payload: KnowledgeBaseCreate,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> KnowledgeBaseOut:
    kb = KnowledgeBase(team_id=context.team.id, name=payload.name)
    session.add(kb)
    session.commit()
    return KnowledgeBaseOut(id=kb.id, name=kb.name)
