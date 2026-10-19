from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app import db
from app.db import apply_context
from app.embeddings import get_embedding_provider
from app.models import Document, DocumentChunk, ProcessingTask
from app.parsing import UnsupportedDocument, extract_document
from app.reconciliation import reconcile_uploads_once
from app.storage import get_object_store
from app.tasks import (
    LeaseLost,
    claim_next_task,
    lease_is_held,
    mark_exhausted_failed,
    mark_failed,
    mark_succeeded,
    reclaim_expired_tasks,
    renew_lease,
    replace_document_chunks,
)

logger = logging.getLogger("kp.worker")

POLL_INTERVAL_SECONDS = 2.0
RECONCILE_INTERVAL_SECONDS = 300.0


def process_task(
    session: Session,
    task: ProcessingTask,
    team_id: uuid.UUID,
    lease_token: uuid.UUID | None = None,
) -> None:
    apply_context(session, team_id=team_id)
    if lease_token is not None and task.lease_token != lease_token:
        raise LeaseLost
    document = session.get(Document, task.document_id)
    if document is None:
        task.status = "succeeded"
        session.flush()
        return
    if document.status in {"uploading", "deleted"}:
        task.status = "canceled"
        task.lease_token = None
        task.lease_expires_at = None
        session.flush()
        return

    document.status = "processing"
    session.flush()

    try:
        data = get_object_store().get(document.storage_key)
    except FileNotFoundError:
        mark_failed(session, task, document, "原始文件缺失", permanent=True)
        session.commit()
        return

    try:
        extracted = extract_document(document.filename, data)
    except UnsupportedDocument as exc:
        mark_failed(session, task, document, str(exc), permanent=True)
        session.commit()
        return
    except Exception as exc:  # noqa: BLE001
        mark_failed(session, task, document, f"解析失败：{exc}")
        session.commit()
        return

    renew_lease(session, task)
    session.flush()

    try:
        vectors = get_embedding_provider().embed([chunk.content for chunk in extracted.chunks])
    except Exception as exc:  # noqa: BLE001
        mark_failed(session, task, document, f"生成向量失败：{exc}")
        session.commit()
        return

    if not lease_is_held(session, task):
        raise LeaseLost

    chunks = [
        DocumentChunk(
            team_id=document.team_id,
            document_id=document.id,
            ordinal=index,
            content=extracted.content,
            locator_label=extracted.locator_label,
            locator_start=extracted.locator_start,
            locator_end=extracted.locator_end,
            embedding=vector,
        )
        for index, (extracted, vector) in enumerate(zip(extracted.chunks, vectors, strict=True))
    ]
    replace_document_chunks(session, document, chunks)
    mark_succeeded(session, task, document)
    session.commit()
    logger.info("文档 %s 处理完成，共 %d 个片段", document.id, len(chunks))


def run_once() -> bool:
    with db.SessionLocal() as session:
        reclaimed = reclaim_expired_tasks(session)
        session.commit()
        for task_id, team_id, exhausted in reclaimed:
            with db.SessionLocal() as recovery:
                if exhausted:
                    mark_exhausted_failed(recovery, team_id, task_id)
                recovery.commit()
        if reclaimed:
            logger.warning("回收了 %d 个过期任务", len(reclaimed))

        claimed = claim_next_task(session)
        session.commit()
        if claimed is None:
            session.rollback()
            return False
        task_id, team_id, lease_token = claimed

    with db.SessionLocal() as session:
        apply_context(session, team_id=team_id)
        task = session.get(ProcessingTask, task_id)
        if task is None:
            return True
        if task.lease_token != lease_token:
            logger.warning("任务 %s 的租约令牌与领取结果不一致，放弃执行", task_id)
            return True
        try:
            process_task(session, task, team_id, lease_token)
        except LeaseLost:
            session.rollback()
            logger.warning("任务 %s 租约已失效，放弃发布", task_id)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("任务 %s 处理异常", task_id, exc_info=exc)
            with db.SessionLocal() as recovery:
                apply_context(recovery, team_id=team_id)
                task = recovery.get(ProcessingTask, task_id)
                document = recovery.get(Document, task.document_id) if task else None
                if task and document:
                    mark_failed(recovery, task, document, f"未预期错误：{exc}")
                    recovery.commit()
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("文档处理 worker 启动")
    last_reconciliation = 0.0
    while True:
        if time.monotonic() - last_reconciliation >= RECONCILE_INTERVAL_SECONDS:
            try:
                with db.SessionLocal() as session:
                    result = reconcile_uploads_once(session)
                logger.info(
                    "上传对账完成：扫描 %d，补任务 %d，失败 %d，暂不可用 %d",
                    result.scanned,
                    result.queued,
                    result.failed,
                    result.unavailable,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("上传对账失败，等待下一周期", exc_info=exc)
            finally:
                last_reconciliation = time.monotonic()
        worked = run_once()
        if not worked:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
