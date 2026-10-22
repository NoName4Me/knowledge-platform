from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import apply_context, get_session
from app.models import SessionToken, Team, User
from app.security import hash_session_token

settings = get_settings()
FORCED_PASSWORD_PATHS = {
    "/api/auth/me",
    "/api/auth/profile",
    "/api/auth/password",
    "/api/auth/logout",
}


@dataclass
class TeamContext:
    user: User
    team: Team
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_add_documents(self) -> bool:
        return self.role in {"member", "admin"}


def _session_token_from_request(request: Request) -> str | None:
    return request.cookies.get(settings.session_cookie_name)


def get_current_user(request: Request, session: Session = Depends(get_session)) -> User:
    raw_token = _session_token_from_request(request)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")

    token_hash = hash_session_token(raw_token)
    stmt = select(SessionToken).where(SessionToken.token_hash == token_hash)
    token = session.scalars(stmt).one_or_none()
    if token is None or token.expires_at <= datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效")

    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    # 行级安全依赖事务级用户上下文；缺失时成员表策略会拒绝访问。
    apply_context(session, user_id=user.id)
    if user.must_change_password and request.url.path not in FORCED_PASSWORD_PATHS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="请先修改临时密码",
        )
    return user


def get_team_context(
    team_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TeamContext:
    row = session.execute(
        text(
            """
            select m.role, v.name, v.description
            from team_memberships m
            join my_teams v on v.id = m.team_id
            where m.team_id = :team_id and m.user_id = :user_id
              and v.is_active
            """
        ),
        {"team_id": team_id, "user_id": user.id},
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该团队")
    team = Team(id=team_id, name=row.name, description=row.description, is_active=True)
    apply_context(session, team_id=team.id)
    return TeamContext(user=user, team=team, role=row.role)


def require_admin(context: TeamContext = Depends(get_team_context)) -> TeamContext:
    if not context.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return context


def require_document_writer(context: TeamContext = Depends(get_team_context)) -> TeamContext:
    if not context.can_add_documents:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="查阅成员不能添加或修改团队公共资料",
        )
    return context


def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要平台管理员权限")
    return user
