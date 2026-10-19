from __future__ import annotations

import uuid

from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from app.db import apply_context
from app.models import Document, DocumentChunk, ProcessingTask

LEASE_SECONDS = 120


class LeaseLost(Exception):
    """任务租约已失效：其他 worker 已接管，本次结果不得发布。"""


def enqueue_document_task(session: Session, document: Document) -> ProcessingTask:
    task = ProcessingTask(team_id=document.team_id, document_id=document.id)
    session.add(task)
    session.flush()
    return task


def claim_next_task(session: Session) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID] | None:
    """通过受限函数领取任务，返回 (task_id, team_id, lease_token)。

    领取本身由数据库函数在受限角色之外完成；调用方必须随后设置团队上下文，
    否则后续读写会被行级安全拒绝。
    """
    row = session.execute(
        text("select task_id, team_id, lease_token from app_claim_task(:lease_seconds)"),
        {"lease_seconds": LEASE_SECONDS},
    ).one_or_none()
    if row is None:
        return None
    return row.task_id, row.team_id, row.lease_token


def reclaim_expired_tasks(session: Session) -> list[tuple[uuid.UUID, uuid.UUID, bool]]:
    """回收过期租约，返回 (task_id, team_id, 是否已耗尽尝试次数)。"""
    rows = session.execute(
        text("select task_id, team_id, exhausted from app_reclaim_tasks()")
    ).all()
    return [(row.task_id, row.team_id, bool(row.exhausted)) for row in rows]


def mark_exhausted_failed(session: Session, team_id: uuid.UUID, task_id: uuid.UUID) -> None:
    """回收时发现尝试次数耗尽：把任务与文档一起置为失败。"""
    apply_context(session, team_id=team_id)
    task = session.get(ProcessingTask, task_id)
    if task is None:
        return
    task.status = "failed"
    task.lease_expires_at = None
    document = session.get(Document, task.document_id)
    if document is not None and document.status != "ready":
        document.status = "failed"
        document.failure_reason = "处理多次失败，已停止自动重试"
    session.flush()


def renew_lease(session: Session, task: ProcessingTask) -> None:
    if task.lease_token is None:
        raise LeaseLost
    result = session.execute(
        text(
            """
            update processing_tasks
            set lease_expires_at = now() + make_interval(secs => :lease_seconds),
                updated_at = now()
            where id = :task_id and status = 'running' and lease_token = :lease_token
              and lease_expires_at > now()
            """
        ),
        {
            "lease_seconds": LEASE_SECONDS,
            "task_id": task.id,
            "lease_token": task.lease_token,
        },
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        raise LeaseLost
    session.expire(task, ["lease_expires_at"])


def lease_is_held(session: Session, task: ProcessingTask) -> bool:
    if task.lease_token is None:
        return False
    token = session.scalar(
        text(
            """
            select lease_token from processing_tasks
            where id = :task_id and status = 'running' and lease_expires_at > now()
            """
        ),
        {"task_id": task.id},
    )
    return bool(token == task.lease_token)


def assert_lease_held(session: Session, task: ProcessingTask) -> None:
    """在发布结果前锁定并校验任务；已失去所有权时不得修改业务行。"""
    task_row = session.execute(
        text(
            """
            select id from processing_tasks
            where id = :task_id and status = 'running'
              and lease_token = :lease_token and lease_expires_at > now()
            for update
            """
        ),
        {"task_id": task.id, "lease_token": task.lease_token},
    ).one_or_none()
    if task_row is None:
        raise LeaseLost
    document_row = session.execute(
        text("select status from documents where id = :document_id for update"),
        {"document_id": task.document_id},
    ).one_or_none()
    if document_row is None or document_row.status not in {"pending", "processing", "failed"}:
        raise LeaseLost


def replace_document_chunks(
    session: Session, document: Document, chunks: list[DocumentChunk]
) -> None:
    """先清空该文档的片段再写入，保证重复执行不产生重复数据。"""
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    session.flush()
    session.add_all(chunks)
    session.flush()


def mark_succeeded(session: Session, task: ProcessingTask, document: Document) -> None:
    assert_lease_held(session, task)
    task.status = "succeeded"
    task.lease_token = None
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
    assert_lease_held(session, task)
    if permanent or task.attempts >= task.max_attempts:
        task.status = "failed"
        task.lease_token = None
        task.lease_expires_at = None
        document.status = "failed"
        document.failure_reason = reason[:500]
    else:
        task.status = "queued"
        task.lease_token = None
        task.lease_expires_at = None
    task.last_error = reason[:500]
    session.flush()
