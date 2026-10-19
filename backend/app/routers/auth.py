from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.db import get_session
from app.models import SessionToken, Team, TeamMembership, User
from app.schemas import LoginRequest, MeOut, TeamSummary, UserOut
from app.security import generate_session_token, hash_session_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


def _to_user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, display_name=user.display_name)


def _teams_for_user(session: Session, user: User) -> list[TeamSummary]:
    stmt = (
        select(TeamMembership, Team)
        .join(Team, Team.id == TeamMembership.team_id)
        .where(TeamMembership.user_id == user.id)
        .order_by(Team.name)
    )
    return [
        TeamSummary(id=team.id, name=team.name, role=membership.role)
        for membership, team in session.execute(stmt).all()
    ]


@router.post("/login", response_model=MeOut)
def login(
    payload: LoginRequest, response: Response, session: Session = Depends(get_session)
) -> MeOut:
    user = session.scalars(select(User).where(User.email == payload.email.lower())).one_or_none()
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码不正确")

    raw_token = generate_session_token()
    session.add(
        SessionToken(
            user_id=user.id,
            token_hash=hash_session_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_max_age_seconds),
        )
    )
    session.commit()

    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return MeOut(user=_to_user_out(user), teams=_teams_for_user(session, user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    session.execute(delete(SessionToken).where(SessionToken.user_id == user.id))
    session.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), session: Session = Depends(get_session)) -> MeOut:
    return MeOut(user=_to_user_out(user), teams=_teams_for_user(session, user))
