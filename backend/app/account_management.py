from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.models import SessionToken, User
from app.security import hash_password

TEMPORARY_PASSWORD = "user1234"


def reset_user_password(
    session: Session,
    *,
    target_user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    team_id: uuid.UUID | None,
) -> User:
    """Apply the fixed temporary-password contract and revoke old sessions."""
    target = session.get(User, target_user_id)
    if target is None:
        raise HTTPException(404, "账号不存在")

    target.password_hash = hash_password(TEMPORARY_PASSWORD)
    target.must_change_password = True
    session.execute(delete(SessionToken).where(SessionToken.user_id == target.id))

    if team_id is None:
        session.execute(
            text(
                "select app_record_platform_account_event("
                ":action, :user_id, :target_id)"
            ),
            {
                "action": "account.password_reset",
                "user_id": actor_user_id,
                "target_id": target.id,
            },
        )
    else:
        session.execute(
            text(
                """
                insert into management_events
                  (id, actor_user_id, team_id, target_user_id, action, details)
                values
                  (gen_random_uuid(), :user_id, :team_id, :target_id,
                   'member.password_reset', '{}'::jsonb)
                """
            ),
            {
                "user_id": actor_user_id,
                "team_id": team_id,
                "target_id": target.id,
            },
        )
    return target
