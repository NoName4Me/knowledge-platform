from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_current_user, require_admin
from app.config import get_settings
from app.db import apply_context, get_session
from app.models import SessionToken, TeamInvitation, TeamMembership, User
from app.ratelimit import FixedWindowLimiter, enforce_rate_limit
from app.schemas import (
    InvitationAcceptRequest,
    InvitationCreate,
    InvitationCreatedOut,
    InvitationOut,
    InvitationPreviewOut,
    InvitationPreviewRequest,
    MeOut,
    RegisterWithInvitationRequest,
    TeamSummary,
    UserOut,
)
from app.security import (
    derive_csrf_token,
    generate_session_token,
    hash_password,
    hash_session_token,
)

router = APIRouter(prefix="/api", tags=["invitations"])
settings = get_settings()
# 兑换接口不要求登录，按来源限制尝试次数，避免暴力猜测令牌。
redeem_limiter = FixedWindowLimiter(30, 300)


def _client_ip(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    if client_ip.startswith(("172.", "10.", "192.168.")):
        client_ip = request.headers.get("x-real-ip") or client_ip
    return client_ip


def _invitation_out(invitation: TeamInvitation) -> InvitationOut:
    return InvitationOut(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        expires_at=invitation.expires_at,
        accepted_at=invitation.accepted_at,
        created_at=invitation.created_at,
    )


def _teams_for_user(session: Session, user: User) -> list[TeamSummary]:
    rows = session.execute(
        text(
            """
            select m.team_id, v.name, m.role
            from team_memberships m
            join my_teams v on v.id = m.team_id
            where m.user_id = :user_id
            order by v.name
            """
        ),
        {"user_id": user.id},
    ).all()
    return [TeamSummary(id=row.team_id, name=row.name, role=row.role) for row in rows]


@router.get("/teams/{team_id}/invitations", response_model=list[InvitationOut])
def list_invitations(
    team_id: uuid.UUID,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[InvitationOut]:
    stmt = (
        select(TeamInvitation)
        .where(TeamInvitation.team_id == context.team.id)
        .order_by(TeamInvitation.created_at.desc())
    )
    return [_invitation_out(item) for item in session.scalars(stmt)]


@router.post(
    "/teams/{team_id}/invitations",
    response_model=InvitationCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    team_id: uuid.UUID,
    payload: InvitationCreate,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> InvitationCreatedOut:
    email = payload.email.lower()
    existing = session.scalars(
        select(TeamMembership)
        .join(User, User.id == TeamMembership.user_id)
        .where(TeamMembership.team_id == context.team.id, User.email == email)
    ).one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该邮箱已在团队中")

    # 同一邮箱的旧邀请先失效，避免多条链接同时可用。
    session.execute(
        text(
            """
            update team_invitations
               set expires_at = now()
             where team_id = :team_id
               and lower(email) = :email
               and accepted_at is null
               and expires_at > now()
            """
        ),
        {"team_id": context.team.id, "email": email},
    )

    raw_token = generate_session_token()
    invitation = TeamInvitation(
        team_id=context.team.id,
        email=email,
        role=payload.role,
        token_hash=hash_session_token(raw_token),
        invited_by_id=context.user.id,
        expires_at=datetime.now(UTC) + timedelta(hours=settings.invitation_ttl_hours),
    )
    session.add(invitation)
    session.commit()
    return InvitationCreatedOut(invitation=_invitation_out(invitation), token=raw_token)


@router.delete(
    "/teams/{team_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_invitation(
    team_id: uuid.UUID,
    invitation_id: uuid.UUID,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    invitation = session.scalars(
        select(TeamInvitation).where(
            TeamInvitation.id == invitation_id,
            TeamInvitation.team_id == context.team.id,
        )
    ).one_or_none()
    if invitation is None:
        # 已失效的邀请视为撤销成功，避免重复点击报错。
        session.rollback()
        return
    session.delete(invitation)
    session.commit()


@router.post("/invitations/preview", response_model=InvitationPreviewOut)
def preview_invitation(
    payload: InvitationPreviewRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> InvitationPreviewOut:
    enforce_rate_limit(redeem_limiter, f"preview:{_client_ip(request)}")
    row = session.execute(
        text("select status, team_name, email from app_preview_invitation(:token_hash)"),
        {"token_hash": hash_session_token(payload.token)},
    ).one_or_none()
    if row is None:
        return InvitationPreviewOut(
            status="not_found", team_name=None, email_masked=None, email=None
        )
    return InvitationPreviewOut(
        status=row.status,
        team_name=row.team_name,
        email_masked=_mask_email(row.email),
        email=row.email if row.status == "ok" else None,
    )


def _mask_email(email: str | None) -> str | None:
    if not email:
        return None
    local, _, domain = email.partition("@")
    if not domain or len(local) <= 1:
        return email
    return f"{local[0]}***@{domain}"


def _accept(
    session: Session,
    token: str,
    user_id: uuid.UUID,
    email: str,
) -> tuple[str, uuid.UUID | None, str | None, str | None]:
    row = session.execute(
        text(
            """
            select status, team_id, team_name, role
            from app_accept_invitation(:token_hash, :user_id, :email)
            """
        ),
        {
            "token_hash": hash_session_token(token),
            "user_id": user_id,
            "email": email,
        },
    ).one()
    return row.status, row.team_id, row.team_name, row.role


def _reject_invitation(outcome: str) -> HTTPException:
    if outcome == "email_mismatch":
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该邀请仅限指定邮箱使用",
        )
    if outcome == "used":
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="邀请已被使用")
    if outcome == "expired":
        return HTTPException(status_code=status.HTTP_410_GONE, detail="邀请已过期")
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="邀请无效")


def _login_response(session: Session, response: Response, user: User, raw_token: str) -> MeOut:
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return MeOut(
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name),
        teams=_teams_for_user(session, user),
        csrf_token=derive_csrf_token(raw_token, settings.secret_key),
    )


@router.post(
    "/invitations/register",
    response_model=MeOut,
    status_code=status.HTTP_201_CREATED,
)
def register_with_invitation(
    payload: RegisterWithInvitationRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> MeOut:
    """被邀请的新用户设置密码并加入团队；已有账号应改用登录后接受。"""
    enforce_rate_limit(redeem_limiter, f"register:{_client_ip(request)}")
    email = payload.email.lower()
    existing = session.scalars(select(User).where(User.email == email)).one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该邮箱已有账号，请先登录后再接受邀请",
        )

    user = User(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    session.flush()

    outcome, _team_id, _team_name, _role = _accept(session, payload.token, user.id, email)
    if outcome != "ok":
        session.rollback()
        raise _reject_invitation(outcome)

    raw_token = generate_session_token()
    session.add(
        SessionToken(
            user_id=user.id,
            token_hash=hash_session_token(raw_token),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.session_max_age_seconds),
        )
    )
    session.commit()
    apply_context(session, user_id=user.id)
    return _login_response(session, response, user, raw_token)


@router.post("/invitations/accept", response_model=MeOut)
def accept_invitation(
    payload: InvitationAcceptRequest,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MeOut:
    outcome, _team_id, _team_name, _role = _accept(session, payload.token, user.id, user.email)
    if outcome != "ok":
        session.rollback()
        raise _reject_invitation(outcome)
    session.commit()
    apply_context(session, user_id=user.id)
    raw_token = request.cookies.get(settings.session_cookie_name, "")
    return MeOut(
        user=UserOut(id=user.id, email=user.email, display_name=user.display_name),
        teams=_teams_for_user(session, user),
        csrf_token=derive_csrf_token(raw_token, settings.secret_key) if raw_token else "",
    )
