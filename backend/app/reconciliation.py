from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import apply_context
from app.models import Document, ProcessingTask
from app.storage import get_object_store
from app.upload_service import mark_uploaded

logger = logging.getLogger("kp.reconciliation")

STALE_UPLOAD_AGE = timedelta(minutes=30)


@dataclass
class ReconciliationResult:
    scanned: int = 0
    queued: int = 0
    failed: int = 0
    unavailable: int = 0


def reconcile_uploads_once(session: Session, *, limit: int = 100) -> ReconciliationResult:
    """恢复“对象已写入但数据库完成事务未提交”的上传。

    只接受超过上传窗口的记录；存储不可用时不改状态，避免把临时故障误判为数据丢失。
    """
    cutoff = datetime.now(UTC) - STALE_UPLOAD_AGE
    candidates = session.scalars(
        select(Document)
        .where(Document.status == "uploading", Document.updated_at < cutoff)
        .order_by(Document.created_at)
        .limit(limit)
    ).all()
    result = ReconciliationResult(scanned=len(candidates))
    store = get_object_store()

    for candidate in candidates:
        apply_context(session, team_id=candidate.team_id)
        unique = session.scalars(
            select(ProcessingTask).where(
                ProcessingTask.document_id == candidate.id,
            )
        ).one_or_none()
        try:
            data = store.get(candidate.storage_key)
        except FileNotFoundError:
            candidate.status = "failed"
            candidate.failure_reason = "上传未完成，原始文件不存在"
            session.commit()
            result.failed += 1
            continue
        except OSError:
            result.unavailable += 1
            session.rollback()
            continue
        except Exception:  # noqa: BLE001
            result.unavailable += 1
            session.rollback()
            continue

        if len(data) != candidate.size_bytes:
            result.unavailable += 1
            session.rollback()
            continue

        if unique is None:
            mark_uploaded(session, candidate)
            session.commit()
            result.queued += 1
        else:
            session.rollback()
    return result


def retry_failed_upload(session: Session, document: Document) -> None:
    """仅当原件完整时才允许用户重新处理失败上传。"""
    if document.status != "failed":
        return
    task = session.scalars(
        select(ProcessingTask)
        .where(ProcessingTask.document_id == document.id)
        .order_by(ProcessingTask.created_at.desc())
        .limit(1)
    ).one_or_none()
    if task is None:
        return
    task.status = "queued"
    task.attempts = 0
    task.lease_token = None
    task.lease_expires_at = None
    document.status = "pending"
    document.failure_reason = None
    session.flush()
