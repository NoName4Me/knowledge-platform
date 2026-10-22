from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.account_management import reset_user_password
from app.auth import TeamContext, get_team_context, require_admin
from app.db import apply_context, get_session
from app.knowledge_base_access import knowledge_base_permission, require_knowledge_base
from app.models import (
    Document,
    KnowledgeBase,
    KnowledgeBaseAccess,
    ManagementEvent,
    Team,
    TeamMembership,
    User,
)
from app.schemas import MemberOut, MemberRoleUpdate, TeamSummary, TeamUpdate
from app.storage import delete_objects_best_effort
from app.upload_service import release_quota_once

router = APIRouter(prefix="/api/teams", tags=["teams"])


class KnowledgeBaseOut(BaseModel):
    id: uuid.UUID
    name: str
    visibility: str = "team"
    team_permission: str = "inherit"
    permission: str = "view"
    owner_id: uuid.UUID | None = None


class KnowledgeBaseCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    visibility: str = Field(default="team", pattern="^(team|private)$")


class KnowledgeBaseSettingsUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    visibility: str | None = Field(default=None, pattern="^(team|private)$")
    team_permission: str | None = Field(
        default=None, pattern="^(inherit|view|edit|manage)$"
    )


class KnowledgeBaseMemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    team_role: str
    permission: str | None
    explicit_permission: str | None
    inherited: bool
    source: str


class KnowledgeBaseMemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permission: str | None = Field(default=None, pattern="^(view|edit|manage)$")


class KnowledgeBaseMemberPermission(BaseModel):
    user_id: uuid.UUID
    permission: str = Field(pattern="^(view|edit|manage)$")


def _loginable_admin_count(session: Session, team_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(TeamMembership)
            .join(User, User.id == TeamMembership.user_id)
            .where(
                TeamMembership.team_id == team_id,
                TeamMembership.role == "admin",
                User.is_active.is_(True),
            )
        )
        or 0
    )


@router.get("/{team_id}", response_model=TeamSummary)
def get_team(context: TeamContext = Depends(get_team_context)) -> TeamSummary:
    return TeamSummary(
        id=context.team.id,
        name=context.team.name,
        role=context.role,
        description=context.team.description,
    )


@router.patch("/{team_id}", response_model=TeamSummary)
def update_team(
    payload: TeamUpdate,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> TeamSummary:
    team = session.get(Team, context.team.id)
    if team is None:
        raise HTTPException(404, "团队不存在")
    if payload.name is not None:
        team.name = payload.name
    if "description" in payload.model_fields_set:
        team.description = payload.description
    session.add(
        ManagementEvent(
            actor_user_id=context.user.id,
            team_id=team.id,
            action="team.updated",
            details={},
        )
    )
    session.commit()
    return TeamSummary(
        id=team.id,
        name=team.name,
        role=context.role,
        description=team.description,
    )


@router.get("/{team_id}/knowledge-bases", response_model=list[KnowledgeBaseOut])
def list_knowledge_bases(
    team_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> list[KnowledgeBaseOut]:
    stmt = select(KnowledgeBase).where(
        KnowledgeBase.team_id == context.team.id
    ).order_by(KnowledgeBase.name)
    rows: list[KnowledgeBaseOut] = []
    for knowledge_base in session.scalars(stmt):
        permission = knowledge_base_permission(session, context, knowledge_base.id)
        if permission is None:
            continue
        rows.append(
            KnowledgeBaseOut(
                id=knowledge_base.id,
                name=knowledge_base.name,
                visibility=knowledge_base.visibility,
                team_permission=knowledge_base.team_permission,
                permission=permission,
                owner_id=knowledge_base.owner_id,
            )
        )
    return rows


@router.post("/{team_id}/knowledge-bases", response_model=KnowledgeBaseOut, status_code=201)
def create_knowledge_base(
    team_id: uuid.UUID,
    payload: KnowledgeBaseCreate,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> KnowledgeBaseOut:
    try:
        knowledge_base_id = session.execute(
            text(
                "select app_create_knowledge_base("
                ":team_id, :user_id, :name, :visibility)"
            ),
            {
                "team_id": context.team.id,
                "user_id": context.user.id,
                "name": payload.name,
                "visibility": payload.visibility,
            },
        ).scalar_one()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "同一团队内已存在该名称的知识库") from exc
    _commit_kb(session)
    apply_context(
        session,
        user_id=context.user.id,
        team_id=context.team.id,
    )
    kb = session.get(KnowledgeBase, knowledge_base_id)
    if kb is None:
        raise HTTPException(404, "知识库不存在或无权访问")
    return KnowledgeBaseOut(
        id=kb.id,
        name=kb.name,
        visibility=kb.visibility,
        team_permission=kb.team_permission,
        permission="manage",
        owner_id=context.user.id,
    )


def _commit_kb(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "同一团队内已存在该名称的知识库") from exc


@router.patch("/{team_id}/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseOut)
def update_knowledge_base(
    knowledge_base_id: uuid.UUID,
    payload: KnowledgeBaseSettingsUpdate,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> KnowledgeBaseOut:
    kb, permission = require_knowledge_base(
        session,
        context,
        knowledge_base_id,
        minimum="manage",
    )
    if payload.name is not None:
        kb.name = payload.name
    if payload.visibility is not None:
        kb.visibility = payload.visibility
    if payload.team_permission is not None:
        kb.team_permission = payload.team_permission
    if kb.visibility == "private":
        # Team defaults have no effect on private libraries.
        kb.team_permission = "inherit"
    session.add(
        ManagementEvent(
            actor_user_id=context.user.id,
            team_id=context.team.id,
            action="knowledge_base.updated",
            details={
                "knowledge_base_id": str(kb.id),
                "visibility": kb.visibility,
                "team_permission": kb.team_permission,
            },
        )
    )
    _commit_kb(session)
    return KnowledgeBaseOut(
        id=kb.id,
        name=kb.name,
        visibility=kb.visibility,
        team_permission=kb.team_permission,
        permission=permission,
        owner_id=kb.owner_id,
    )


@router.delete("/{team_id}/knowledge-bases/{knowledge_base_id}", status_code=204)
def delete_knowledge_base(
    knowledge_base_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    kb, _permission = require_knowledge_base(
        session,
        context,
        knowledge_base_id,
        minimum="manage",
    )
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


@router.get(
    "/{team_id}/knowledge-bases/{knowledge_base_id}/access",
    response_model=list[KnowledgeBaseMemberOut],
)
def list_knowledge_base_access(
    knowledge_base_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> list[KnowledgeBaseMemberOut]:
    kb, _permission = require_knowledge_base(
        session, context, knowledge_base_id, minimum="manage"
    )
    members = session.execute(
        select(TeamMembership, User)
        .join(User, User.id == TeamMembership.user_id)
        .where(TeamMembership.team_id == context.team.id)
        .order_by(User.display_name, User.email)
    ).all()
    access_rows: dict[uuid.UUID, KnowledgeBaseAccess] = {
        row.user_id: row
        for row in session.scalars(
            select(KnowledgeBaseAccess).where(
                KnowledgeBaseAccess.knowledge_base_id == knowledge_base_id
            )
        )
    }
    result: list[KnowledgeBaseMemberOut] = []
    for membership, user in members:
        explicit = access_rows.get(user.id)
        base_permission: str | None
        if user.id == kb.owner_id:
            base_permission = "manage"
            source = "owner"
        elif kb.visibility == "private":
            base_permission = None
            source = "explicit" if explicit else "none"
        elif membership.role == "admin":
            base_permission = "manage"
            source = "team_role"
        elif kb.team_permission == "inherit":
            base_permission = "edit" if membership.role == "member" else "view"
            source = "team_role"
        else:
            base_permission = kb.team_permission
            source = "team_default"
        if explicit is not None and (
            base_permission is None
            or {"view": 1, "edit": 2, "manage": 3}[explicit.permission]
            > {"view": 1, "edit": 2, "manage": 3}[base_permission]
        ):
            source = "explicit"
        # ``context`` belongs to the caller, so query this member's effective
        # permission with the same database-authoritative function.
        permission = session.execute(
            text(
                "select app_kb_permission(:knowledge_base_id, :user_id)"
            ),
            {"knowledge_base_id": knowledge_base_id, "user_id": user.id},
        ).scalar_one_or_none()
        if permission not in {"view", "edit", "manage"}:
            permission = None
        explicit_permission = explicit.permission if explicit else None
        result.append(
            KnowledgeBaseMemberOut(
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                team_role=membership.role,
                permission=permission,
                explicit_permission=explicit_permission,
                inherited=source in {"owner", "team_role", "team_default"},
                source=source,
            )
        )
    return result


@router.put(
    "/{team_id}/knowledge-bases/{knowledge_base_id}/access",
    status_code=204,
)
def replace_knowledge_base_access(
    knowledge_base_id: uuid.UUID,
    payload: list[KnowledgeBaseMemberPermission],
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    kb, _permission = require_knowledge_base(
        session, context, knowledge_base_id, minimum="manage"
    )
    ids = [item.user_id for item in payload]
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "成员权限列表中存在重复用户")
    if ids:
        member_ids = set(
            session.scalars(
                select(TeamMembership.user_id).where(
                    TeamMembership.team_id == context.team.id,
                    TeamMembership.user_id.in_(ids),
                )
            )
        )
        if member_ids != set(ids):
            raise HTTPException(422, "只能给当前团队成员设置知识库权限")
    if kb.visibility == "team":
        memberships = {
            membership.user_id: membership
            for membership in session.scalars(
                select(TeamMembership).where(
                    TeamMembership.team_id == context.team.id,
                )
            )
        }
        roles = {
            "view": 1,
            "edit": 2,
            "manage": 3,
        }
        for item in payload:
            membership = memberships[item.user_id]
            if item.user_id == kb.owner_id or membership.role == "admin":
                raise HTTPException(
                    422,
                    "知识库创建者和团队管理员始终拥有管理权限，无需单独设置",
                )
            inherited = (
                roles[
                    "edit" if membership.role == "member" else "view"
                ]
                if kb.team_permission == "inherit"
                else roles[kb.team_permission]
            )
            if roles[item.permission] <= inherited:
                raise HTTPException(
                    422,
                    "团队可见知识库的个人权限只能高于团队默认权限",
                )
    for item in payload:
        if item.user_id == kb.owner_id:
            raise HTTPException(
                422,
                "知识库创建者始终拥有管理权限，无需单独设置",
            )
    session.execute(
        text(
            "delete from knowledge_base_access where knowledge_base_id = :kb_id"
        ),
        {"kb_id": knowledge_base_id},
    )
    for item in payload:
        session.add(
            KnowledgeBaseAccess(
                team_id=context.team.id,
                knowledge_base_id=knowledge_base_id,
                user_id=item.user_id,
                permission=item.permission,
            )
        )
    session.add(
        ManagementEvent(
            actor_user_id=context.user.id,
            team_id=context.team.id,
            action="knowledge_base.access_updated",
            details={
                "knowledge_base_id": str(kb.id),
                "member_count": len(payload),
            },
        )
    )
    session.commit()


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

    if membership.role == "admin" and _loginable_admin_count(session, context.team.id) <= 1:
        target = session.get(User, user_id)
        if target is not None and target.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="团队必须保留至少一名管理员",
            )

    session.delete(membership)
    session.add(
        ManagementEvent(
            actor_user_id=context.user.id,
            team_id=context.team.id,
            target_user_id=user_id,
            action="member.removed",
            details={},
        )
    )
    session.commit()


@router.patch("/{team_id}/members/{user_id}/role", response_model=MemberOut)
def update_member_role(
    user_id: uuid.UUID,
    payload: MemberRoleUpdate,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> MemberOut:
    session.execute(text("select app_lock_team(:team_id)"), {"team_id": context.team.id})
    membership = session.scalars(
        select(TeamMembership).where(
            TeamMembership.team_id == context.team.id,
            TeamMembership.user_id == user_id,
        )
    ).one_or_none()
    if membership is None:
        raise HTTPException(404, "成员不存在")
    if (
        membership.role == "admin"
        and payload.role != "admin"
        and _loginable_admin_count(session, context.team.id) <= 1
    ):
        target = session.get(User, user_id)
        if target is not None and target.is_active:
            raise HTTPException(409, "团队必须保留至少一名管理员")
    previous = membership.role
    membership.role = payload.role
    session.add(
        ManagementEvent(
            actor_user_id=context.user.id,
            team_id=context.team.id,
            target_user_id=user_id,
            action="member.role_changed",
            details={"from": previous, "to": payload.role},
        )
    )
    session.commit()
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "成员账号不存在")
    return MemberOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
    )


@router.post("/{team_id}/members/{user_id}/reset-password", status_code=204)
def reset_member_password(
    user_id: uuid.UUID,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    membership = session.scalars(
        select(TeamMembership).where(
            TeamMembership.team_id == context.team.id,
            TeamMembership.user_id == user_id,
        )
    ).one_or_none()
    if membership is None:
        raise HTTPException(404, "成员不存在")
    reset_user_password(
        session,
        target_user_id=user_id,
        actor_user_id=context.user.id,
        team_id=context.team.id,
    )
    session.commit()


@router.delete("/{team_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_team(
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    session.execute(text("select app_lock_team(:team_id)"), {"team_id": context.team.id})
    membership = session.scalars(
        select(TeamMembership).where(
            TeamMembership.team_id == context.team.id,
            TeamMembership.user_id == context.user.id,
        )
    ).one_or_none()
    if membership is None:
        session.rollback()
        return
    if membership.role == "admin" and _loginable_admin_count(session, context.team.id) <= 1:
        raise HTTPException(409, "最后一名管理员不能退出团队")
    session.delete(membership)
    session.add(
        ManagementEvent(
            actor_user_id=context.user.id,
            team_id=context.team.id,
            target_user_id=context.user.id,
            action="member.left",
            details={},
        )
    )
    session.commit()
