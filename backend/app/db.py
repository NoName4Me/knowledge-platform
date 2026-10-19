import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

settings = get_settings()


def _set_runtime_role(dbapi_connection: Any, _record: Any) -> None:
    """连接建立后切换到受限角色，使行级安全对应用生效。"""
    role = settings.rls_role
    if not role:
        return
    with dbapi_connection.cursor() as cursor:
        cursor.execute(f"SET ROLE {role}")


def configure_engine(target_engine: Engine) -> None:
    """为引擎挂上受限角色切换；应用与测试共用同一套连接准备逻辑。"""
    event.listen(target_engine, "connect", _set_runtime_role)


engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
configure_engine(engine)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def apply_context(
    session: Session,
    *,
    user_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
) -> None:
    """设置事务级上下文；只在当前事务有效，连接回池后不会残留。"""
    if user_id is not None:
        session.execute(
            text("select set_config('kp.user_id', :value, true)"), {"value": str(user_id)}
        )
    if team_id is not None:
        session.execute(
            text("select set_config('kp.team_id', :value, true)"), {"value": str(team_id)}
        )


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
