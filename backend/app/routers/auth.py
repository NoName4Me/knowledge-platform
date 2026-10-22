from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.csrf import issue_prelogin_token
from app.db import apply_context, get_session
from app.models import SessionToken, User
from app.ratelimit import FixedWindowLimiter, enforce_rate_limit
from app.schemas import (
    CsrfOut,
    LoginRequest,
    MeOut,
    PasswordChange,
    ProfileUpdate,
    TeamSummary,
    UserOut,
)
from app.security import (
    derive_csrf_token,
    generate_session_token,
    hash_password,
    hash_session_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()
email_login_limiter = FixedWindowLimiter(
    settings.login_email_rate_limit, settings.login_rate_window_seconds
)
ip_login_limiter = FixedWindowLimiter(
    settings.login_ip_rate_limit, settings.login_rate_window_seconds
)


def _to_user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_platform_admin=user.is_platform_admin,
        must_change_password=user.must_change_password,
    )


def _teams_for_user(session: Session, user: User) -> list[TeamSummary]:
    # teams 对运行角色不直接开放；只能通过只读视图读取已加入团队。
    rows = session.execute(
        text(
            """
            select m.team_id, v.name, m.role
            from team_memberships m
            join my_teams v on v.id = m.team_id
            where m.user_id = :user_id
              and v.is_active
            order by v.name
            """
        ),
        {"user_id": user.id},
    ).all()
    return [TeamSummary(id=row.team_id, name=row.name, role=row.role) for row in rows]


@router.get("/csrf", response_model=CsrfOut)
def csrf(response: Response) -> CsrfOut:
    """未登录时签发预登录令牌；已登录会话的令牌由 /login 与 /me 返回。"""
    token = issue_prelogin_token()
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=settings.csrf_prelogin_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
    )
    return CsrfOut(
        csrf_token=token,
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.csrf_prelogin_seconds),
    )


@router.post("/login", response_model=MeOut)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
) -> MeOut:
    # docker-compose 只把 API 暴露在 Compose 内网，外部请求经 Nginx 后
    # 才可信转发 X-Real-IP；直接访问时使用连接来源。
    client_ip = request.client.host if request.client else "unknown"
    if client_ip.startswith(("172.", "10.", "192.168.")):
        client_ip = request.headers.get("x-real-ip") or client_ip
    normalized_email = payload.email.lower()
    enforce_rate_limit(email_login_limiter, f"{client_ip}:{normalized_email}")
    enforce_rate_limit(ip_login_limiter, client_ip)

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
    # 提交会清除事务级上下文，列出团队前需要重新设置。
    apply_context(session, user_id=user.id)

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
        user=_to_user_out(user),
        teams=_teams_for_user(session, user),
        csrf_token=derive_csrf_token(raw_token, settings.secret_key),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    session.execute(delete(SessionToken).where(SessionToken.user_id == user.id))
    session.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


@router.get("/me", response_model=MeOut)
def me(
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MeOut:
    raw_token = request.cookies.get(settings.session_cookie_name, "")
    return MeOut(
        user=_to_user_out(user),
        teams=_teams_for_user(session, user),
        csrf_token=derive_csrf_token(raw_token, settings.secret_key),
    )


@router.patch("/profile", response_model=UserOut)
def update_profile(
    payload: ProfileUpdate,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UserOut:
    user.display_name = payload.display_name
    session.commit()
    return _to_user_out(user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChange,
    response: Response,
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.new_password == "user1234":
        raise HTTPException(status_code=422, detail="新密码不能使用临时密码")
    if payload.new_password == payload.current_password:
        raise HTTPException(status_code=422, detail="新密码不能与当前密码相同")
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    session.execute(delete(SessionToken).where(SessionToken.user_id == user.id))
    session.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
