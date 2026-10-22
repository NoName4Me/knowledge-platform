from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.account_management import reset_user_password as apply_password_reset
from app.auth import require_platform_admin
from app.config import get_settings
from app.db import apply_context, get_session
from app.models import User
from app.security import generate_session_token, hash_session_token

router = APIRouter(prefix="/api/platform", tags=["platform"])


class PlatformTeamOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    administrator_count: int
    pending_administrator_invitation_count: int = 0
    administrator_invitation_token: str | None = None


class PlatformUserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    is_active: bool
    must_change_password: bool
    created_at: datetime


class PlatformTeamCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    administrator_email: str = Field(min_length=3, max_length=320)


class TeamAdministratorAssign(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    administrator_email: str = Field(min_length=3, max_length=320)


class TeamStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


class AccountStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_active: bool


@router.get("/teams", response_model=list[PlatformTeamOut])
def list_teams(
    _actor: User = Depends(require_platform_admin),
    session: Session = Depends(get_session),
) -> list[PlatformTeamOut]:
    rows = session.execute(text("select * from app_platform_teams()")).all()
    return [PlatformTeamOut.model_validate(row._mapping) for row in rows]


@router.get("/users", response_model=list[PlatformUserOut])
def list_users(
    _actor: User = Depends(require_platform_admin),
    session: Session = Depends(get_session),
) -> list[PlatformUserOut]:
    rows = session.execute(text("select * from app_platform_users()")).all()
    return [PlatformUserOut.model_validate(row._mapping) for row in rows]


@router.post("/teams", response_model=PlatformTeamOut, status_code=201)
def create_team(
    payload: PlatformTeamCreate,
    _actor: User = Depends(require_platform_admin),
    session: Session = Depends(get_session),
) -> PlatformTeamOut:
    raw_token = generate_session_token()
    row = session.execute(
        text(
            """
            select team_id, administrator_status
            from app_create_platform_team(
              :name, :description, :email, :token_hash, :expires_at
            )
            """
        ),
        {
            "name": payload.name,
            "description": payload.description,
            "email": payload.administrator_email.lower(),
            "token_hash": hash_session_token(raw_token),
            "expires_at": datetime.now(UTC)
            + timedelta(hours=get_settings().invitation_ttl_hours),
        },
    ).one()
    team_row = session.execute(
        text(
            "select * from app_platform_teams() "
            "where app_platform_teams.id = :team_id"
        ),
        {"team_id": row.team_id},
    ).one()
    session.commit()
    invited = row.administrator_status == "invited"
    result = PlatformTeamOut.model_validate(team_row._mapping)
    if invited:
        result.administrator_invitation_token = raw_token
    return result


@router.post("/teams/{team_id}/administrator", response_model=PlatformTeamOut)
def assign_team_administrator(
    team_id: uuid.UUID,
    payload: TeamAdministratorAssign,
    actor: User = Depends(require_platform_admin),
    session: Session = Depends(get_session),
) -> PlatformTeamOut:
    raw_token = generate_session_token()
    row = session.execute(
        text(
            """
            select status
            from app_platform_assign_team_admin(
              :team_id, :email, :token_hash, :expires_at
            )
            """
        ),
        {
            "team_id": team_id,
            "email": payload.administrator_email.lower(),
            "token_hash": hash_session_token(raw_token),
            "expires_at": datetime.now(UTC)
            + timedelta(hours=get_settings().invitation_ttl_hours),
        },
    ).one()
    if row.status == "not_found":
        raise HTTPException(404, "团队不存在")
    if row.status == "account_disabled":
        raise HTTPException(409, "指定账号已停用")
    session.commit()
    apply_context(session, user_id=actor.id)
    team_row = session.execute(
        text(
            "select * from app_platform_teams() "
            "where app_platform_teams.id = :team_id"
        ),
        {"team_id": team_id},
    ).one()
    result = PlatformTeamOut.model_validate(team_row._mapping)
    if row.status == "invited":
        result.administrator_invitation_token = raw_token
    return result


@router.patch("/teams/{team_id}/status", response_model=PlatformTeamOut)
def update_team_status(
    team_id: uuid.UUID,
    payload: TeamStatusUpdate,
    actor: User = Depends(require_platform_admin),
    session: Session = Depends(get_session),
) -> PlatformTeamOut:
    row = session.execute(
        text(
            "select status, is_active from app_platform_set_team_active(:team_id, :active)"
        ),
        {"team_id": team_id, "active": payload.is_active},
    ).one()
    if row.status == "not_found":
        raise HTTPException(404, "团队不存在")
    session.commit()
    apply_context(session, user_id=actor.id)
    team_row = session.execute(
        text(
            "select * from app_platform_teams() "
            "where app_platform_teams.id = :team_id"
        ),
        {"team_id": team_id},
    ).one()
    return PlatformTeamOut.model_validate(team_row._mapping)


@router.patch("/users/{user_id}/status", response_model=PlatformUserOut)
def update_account_status(
    user_id: uuid.UUID,
    payload: AccountStatusUpdate,
    _actor: User = Depends(require_platform_admin),
    session: Session = Depends(get_session),
) -> PlatformUserOut:
    row = session.execute(
        text(
            "select status, blocking_teams "
            "from app_platform_set_account_active(:user_id, :active)"
        ),
        {"user_id": user_id, "active": payload.is_active},
    ).one()
    if row.status == "not_found":
        raise HTTPException(404, "账号不存在")
    if row.status == "blocked":
        session.rollback()
        teams = "、".join(f"「{name}」" for name in row.blocking_teams)
        raise HTTPException(409, f"{teams}仍需要该账号作为可登录管理员，请先交接")
    session.commit()
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(500, "账号状态更新结果不可用")
    return PlatformUserOut(
        id=target.id,
        email=target.email,
        display_name=target.display_name,
        is_active=target.is_active,
        must_change_password=target.must_change_password,
        created_at=target.created_at,
    )


@router.post("/users/{user_id}/reset-password", response_model=PlatformUserOut)
def reset_password(
    user_id: uuid.UUID,
    actor: User = Depends(require_platform_admin),
    session: Session = Depends(get_session),
) -> PlatformUserOut:
    if user_id == actor.id:
        raise HTTPException(409, "不能在平台管理中重置自己的密码")
    target = apply_password_reset(
        session,
        target_user_id=user_id,
        actor_user_id=actor.id,
        team_id=None,
    )
    session.commit()
    return PlatformUserOut(
        id=target.id,
        email=target.email,
        display_name=target.display_name,
        is_active=target.is_active,
        must_change_password=target.must_change_password,
        created_at=target.created_at,
    )
