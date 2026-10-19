from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.embeddings import get_embedding_provider
from app.models import Document, DocumentChunk, ProcessingTask
from app.parsing import UnsupportedDocument, extract_document
from app.storage import get_object_store
from app.tasks import (
    LeaseLost,
    claim_next_task,
    lease_is_held,
    mark_failed,
    mark_succeeded,
    reclaim_expired_tasks,
    renew_lease,
    replace_document_chunks,
)

logger = logging.getLogger("kp.worker")

POLL_INTERVAL_SECONDS = 2.0


def process_task(session: Session, task: ProcessingTask) -> None:
    document = session.get(Document, task.document_id)
    if document is None:
        task.status = "succeeded"
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
    with SessionLocal() as session:
        reclaimed = reclaim_expired_tasks(session)
        if reclaimed:
            session.commit()
            logger.warning("回收了 %d 个过期任务", reclaimed)
        else:
            session.rollback()

        task = claim_next_task(session)
        if task is None:
            session.rollback()
            return False
        session.commit()
        task_id = task.id

    with SessionLocal() as session:
        task = session.get(ProcessingTask, task_id)
        if task is None:
            return True
        try:
            process_task(session, task)
        except LeaseLost:
            session.rollback()
            logger.warning("任务 %s 租约已失效，放弃发布", task_id)
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("任务 %s 处理异常", task_id, exc_info=exc)
            with SessionLocal() as recovery:
                task = recovery.get(ProcessingTask, task_id)
                document = recovery.get(Document, task.document_id) if task else None
                if task and document:
                    mark_failed(recovery, task, document, f"未预期错误：{exc}")
                    recovery.commit()
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("文档处理 worker 启动")
    while True:
        worked = run_once()
        if not worked:
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
