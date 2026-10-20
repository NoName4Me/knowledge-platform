"""V2 分析执行的领取、续租与回收。

与 `app/tasks.py` 同构，但走的是分析专用受限函数：跨团队调度只暴露执行 ID、任务 ID、
团队、创建者、来源文档与租约令牌，不返回片段正文或结果内容。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


class AnalysisLeaseLost(Exception):
    """分析执行租约已失效：其他执行者已接管，本次结果不得发布。"""


@dataclass(frozen=True)
class ClaimedDocument:
    document_execution_id: uuid.UUID
    task_id: uuid.UUID
    team_id: uuid.UUID
    owner_id: uuid.UUID
    document_id: uuid.UUID
    lease_token: uuid.UUID


@dataclass(frozen=True)
class ClaimedSummary:
    summary_id: uuid.UUID
    task_id: uuid.UUID
    team_id: uuid.UUID
    owner_id: uuid.UUID
    lease_token: uuid.UUID


def claim_next_document(session: Session, lease_seconds: int) -> ClaimedDocument | None:
    row = session.execute(
        text(
            "select document_execution_id, task_id, team_id, owner_id, document_id, "
            "lease_token from app_claim_analysis_document(:lease_seconds)"
        ),
        {"lease_seconds": lease_seconds},
    ).one_or_none()
    if row is None:
        return None
    return ClaimedDocument(
        document_execution_id=row.document_execution_id,
        task_id=row.task_id,
        team_id=row.team_id,
        owner_id=row.owner_id,
        document_id=row.document_id,
        lease_token=row.lease_token,
    )


def reclaim_expired_documents(
    session: Session,
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, bool]]:
    """回收过期租约，返回 (执行 ID, 任务 ID, 团队 ID, 创建者 ID, 是否耗尽)。

    创建者是分析表的 RLS 身份，回收事务必须同时绑定团队和创建者；仅凭团队上下文
    无法更新本人分析。
    """
    rows = session.execute(
        text(
            "select document_execution_id, task_id, team_id, owner_id, exhausted "
            "from app_reclaim_analysis_documents()"
        )
    ).all()
    return [
        (
            row.document_execution_id,
            row.task_id,
            row.team_id,
            row.owner_id,
            bool(row.exhausted),
        )
        for row in rows
    ]


def renew_lease(
    session: Session, execution_id: uuid.UUID, lease_token: uuid.UUID, lease_seconds: int
) -> None:
    """独立短事务内续租；失败即视为失去所有权。"""
    result = session.execute(
        text(
            """
            update analysis_documents
            set lease_expires_at = now() + make_interval(secs => :lease_seconds),
                updated_at = now()
            where id = :execution_id and status = 'running'
              and lease_token = :lease_token and lease_expires_at > now()
            """
        ),
        {
            "lease_seconds": lease_seconds,
            "execution_id": execution_id,
            "lease_token": lease_token,
        },
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise AnalysisLeaseLost


def claim_next_summary(session: Session, lease_seconds: int) -> ClaimedSummary | None:
    row = session.execute(
        text(
            "select summary_id, task_id, team_id, owner_id, lease_token "
            "from app_claim_analysis_summary(:lease_seconds)"
        ),
        {"lease_seconds": lease_seconds},
    ).one_or_none()
    if row is None:
        return None
    return ClaimedSummary(
        summary_id=row.summary_id,
        task_id=row.task_id,
        team_id=row.team_id,
        owner_id=row.owner_id,
        lease_token=row.lease_token,
    )


def reclaim_expired_summaries(
    session: Session,
) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID, bool]]:
    rows = session.execute(
        text(
            "select summary_id, task_id, team_id, owner_id, exhausted "
            "from app_reclaim_analysis_summaries()"
        )
    ).all()
    return [
        (row.summary_id, row.task_id, row.team_id, row.owner_id, bool(row.exhausted))
        for row in rows
    ]


def renew_summary_lease(
    session: Session, summary_id: uuid.UUID, lease_token: uuid.UUID, lease_seconds: int
) -> None:
    result = session.execute(
        text(
            """
            update analysis_summaries
            set lease_expires_at = now() + make_interval(secs => :lease_seconds),
                updated_at = now()
            where id = :summary_id and status = 'running'
              and lease_token = :lease_token and lease_expires_at > now()
            """
        ),
        {
            "lease_seconds": lease_seconds,
            "summary_id": summary_id,
            "lease_token": lease_token,
        },
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise AnalysisLeaseLost
