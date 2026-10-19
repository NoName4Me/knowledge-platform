from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context, require_admin
from app.db import get_session
from app.models import KnowledgeBase, TeamMembership, User
from app.schemas import MemberOut, TeamSummary

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


def _member_rows(session: Session, team_id: uuid.UUID) -> list[MemberOut]:
    stmt = (
        select(TeamMembership, User)
        .join(User, User.id == TeamMembership.user_id)
        .where(TeamMembership.team_id == team_id)
        .order_by(User.email)
    )
    return [
        MemberOut(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=membership.role,
        )
        for membership, user in session.execute(stmt).all()
    ]


@router.get("/{team_id}/members", response_model=list[MemberOut])
def list_members(
    team_id: uuid.UUID,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[MemberOut]:
    return _member_rows(session, context.team.id)


@router.delete("/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    # teams 不开放给运行角色；用行锁函数串行化团队内的管理员删除。
    session.execute(text("select app_lock_team(:team_id)"), {"team_id": context.team.id})
    membership = session.scalars(
        select(TeamMembership).where(
            TeamMembership.team_id == context.team.id,
            TeamMembership.user_id == user_id,
        )
    ).one_or_none()
    if membership is None:
        # 成员已不存在视为成功，避免重试报错。
        session.rollback()
        return

    if membership.role == "admin":
        admin_count = session.scalar(
            select(func.count())
            .select_from(TeamMembership)
            .where(
                TeamMembership.team_id == context.team.id,
                TeamMembership.role == "admin",
            )
        )
        if admin_count is not None and admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="团队必须保留至少一名管理员",
            )

    session.delete(membership)
    session.commit()
