from __future__ import annotations

import uuid
from typing import Literal, cast

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import TeamContext
from app.models import KnowledgeBase

KnowledgeBasePermission = Literal["view", "edit", "manage"]
_PERMISSION_RANK = {"view": 1, "edit": 2, "manage": 3}


def knowledge_base_permission(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
) -> KnowledgeBasePermission | None:
    """Return the effective permission for the current team member."""
    value = session.execute(
        text("select app_kb_permission(:knowledge_base_id, :user_id)"),
        {
            "knowledge_base_id": knowledge_base_id,
            "user_id": context.user.id,
        },
    ).scalar_one_or_none()
    if value not in _PERMISSION_RANK:
        return None
    return cast(KnowledgeBasePermission, value)


def require_knowledge_base(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    *,
    minimum: KnowledgeBasePermission = "view",
) -> tuple[KnowledgeBase, KnowledgeBasePermission]:
    """Load a base and reject hidden bases without revealing whether they exist."""
    permission = knowledge_base_permission(session, context, knowledge_base_id)
    if permission is None:
        raise HTTPException(404, "知识库不存在或无权访问")
    if _PERMISSION_RANK[permission] < _PERMISSION_RANK[minimum]:
        raise HTTPException(403, "当前知识库权限不足")
    knowledge_base = session.get(KnowledgeBase, knowledge_base_id)
    if knowledge_base is None or knowledge_base.team_id != context.team.id:
        raise HTTPException(404, "知识库不存在或无权访问")
    return knowledge_base, permission


def require_knowledge_base_permission(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    minimum: KnowledgeBasePermission,
) -> KnowledgeBasePermission:
    return require_knowledge_base(
        session,
        context,
        knowledge_base_id,
        minimum=minimum,
    )[1]
