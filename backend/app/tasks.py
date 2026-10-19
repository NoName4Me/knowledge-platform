from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models import Document, DocumentChunk, ProcessingTask

LEASE_SECONDS = 120


class LeaseLost(Exception):
    """任务租约已失效：其他 worker 已接管，本次结果不得发布。"""


def enqueue_document_task(session: Session, document: Document) -> ProcessingTask:
    task = ProcessingTask(team_id=document.team_id, document_id=document.id)
    session.add(task)
    session.flush()
    return task


def claim_next_task(session: Session) -> ProcessingTask | None:
    now = datetime.now(UTC)
    stmt = (
        select(ProcessingTask)
        .where(
            ProcessingTask.status == "queued",
            ProcessingTask.attempts < ProcessingTask.max_attempts,
        )
        .order_by(ProcessingTask.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    task = session.scalars(stmt).one_or_none()
    if task is None:
        return None

    task.status = "running"
    task.attempts += 1
    task.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    session.flush()
    return task


def reclaim_expired_tasks(session: Session) -> int:
    now = datetime.now(UTC)
    result = session.execute(
        update(ProcessingTask)
        .where(
            ProcessingTask.status == "running",
            ProcessingTask.lease_expires_at < now,
            ProcessingTask.attempts < ProcessingTask.max_attempts,
        )
        .values(status="queued", lease_expires_at=None)
    )
    session.flush()
    return int(result.rowcount)  # type: ignore[attr-defined]


def renew_lease(session: Session, task: ProcessingTask) -> None:
    task.lease_expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
    session.flush()


def lease_is_held(session: Session, task: ProcessingTask) -> bool:
    session.refresh(task)
    return task.status == "running"


def replace_document_chunks(
    session: Session, document: Document, chunks: list[DocumentChunk]
) -> None:
    """先清空该文档的片段再写入，保证重复执行不产生重复数据。"""
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    session.flush()
    session.add_all(chunks)
    session.flush()


def mark_succeeded(session: Session, task: ProcessingTask, document: Document) -> None:
    task.status = "succeeded"
    task.lease_expires_at = None
    task.last_error = None
    document.status = "ready"
    document.failure_reason = None
    session.flush()


def mark_failed(
    session: Session,
    task: ProcessingTask,
    document: Document,
    reason: str,
    *,
    permanent: bool = False,
) -> None:
    """permanent 用于格式错误等重试也无法解决的失败，直接终止不再重试。"""
    task.last_error = reason[:500]
    if permanent or task.attempts >= task.max_attempts:
        task.status = "failed"
        task.lease_expires_at = None
        document.status = "failed"
        document.failure_reason = reason[:500]
    else:
        task.status = "queued"
        task.lease_expires_at = None
    session.flush()
