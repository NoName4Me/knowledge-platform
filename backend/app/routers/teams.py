from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context, require_admin
from app.db import get_session
from app.models import Document, KnowledgeBase, TeamMembership, User
from app.schemas import MemberOut, TeamSummary
from app.storage import delete_objects_best_effort
from app.upload_service import release_quota_once

router = APIRouter(prefix="/api/teams", tags=["teams"])


class KnowledgeBaseOut(BaseModel):
    id: uuid.UUID
    name: str


class KnowledgeBaseCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
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
    _commit_kb(session)
    return KnowledgeBaseOut(id=kb.id, name=kb.name)


def _commit_kb(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "同一团队内已存在该名称的知识库") from exc


@router.patch("/{team_id}/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseOut)
def rename_knowledge_base(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseCreate,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> KnowledgeBaseOut:
    kb = session.get(KnowledgeBase, knowledge_base_id)
    if kb is None or kb.team_id != context.team.id:
        raise HTTPException(404, "知识库不存在")
    kb.name = payload.name
    _commit_kb(session)
    return KnowledgeBaseOut(id=kb.id, name=kb.name)


@router.delete("/{team_id}/knowledge-bases/{knowledge_base_id}", status_code=204)
def delete_knowledge_base(
    knowledge_base_id: uuid.UUID,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    kb = session.get(KnowledgeBase, knowledge_base_id)
    if kb is None or kb.team_id != context.team.id:
        raise HTTPException(404, "知识库不存在")
    documents = list(
        session.scalars(
            select(Document).where(Document.knowledge_base_id == kb.id).with_for_update()
        )
    )
    storage_keys = [document.storage_key for document in documents]
    for document in documents:
        release_quota_once(session, context.team.id, document)
    session.delete(kb)
    try:
        session.commit()
    except IntegrityError as exc:
        # 数据库触发器或外键仍可能拒绝删除；回滚后给出可操作的错误，而不是裸露的 500。
        session.rollback()
        raise HTTPException(
            status_code=409,
            detail="知识库删除失败，可能仍有关联数据未清理，请刷新后重试",
        ) from exc
    delete_objects_best_effort(storage_keys)


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
