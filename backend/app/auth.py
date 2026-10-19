from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import SessionToken, Team, TeamMembership, User
from app.security import hash_session_token

settings = get_settings()


@dataclass
class TeamContext:
    user: User
    team: Team
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


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
    return user


def get_team_context(
    team_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> TeamContext:
    stmt = (
        select(TeamMembership, Team)
        .join(Team, Team.id == TeamMembership.team_id)
        .where(TeamMembership.team_id == team_id, TeamMembership.user_id == user.id)
    )
    row = session.execute(stmt).one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该团队")
    membership, team = row
    return TeamContext(user=user, team=team, role=membership.role)


def require_admin(context: TeamContext = Depends(get_team_context)) -> TeamContext:
    if not context.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return context
