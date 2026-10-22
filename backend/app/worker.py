from __future__ import annotations

import logging
import time
import uuid

from sqlalchemy.orm import Session

from app import analysis, automation, db
from app.analysis_tasks import (
    AnalysisLeaseLost,
    claim_next_document,
    claim_next_summary,
    reclaim_expired_documents,
    reclaim_expired_summaries,
)
from app.config import get_settings
from app.db import apply_context
from app.embeddings import get_embedding_provider
from app.models import Document, DocumentChunk, KnowledgeAutomation, ProcessingTask
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
settings = get_settings()
# 两条执行路径交替轮询，避免批量分析长时间占满 worker 而饿死上传解析。
_LAST_SCHEDULED = "analysis"


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
        extracted = extract_document(
            document.filename,
            data,
            legacy_doc_converter=settings.legacy_doc_converter,
            legacy_doc_timeout=settings.legacy_doc_timeout_seconds,
        )
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
            table_number=extracted.table_number,
            table_row=extracted.table_row,
            table_headers=extracted.table_headers,
            table_cells=extracted.table_cells,
            embedding=vector,
        )
        for index, (extracted, vector) in enumerate(zip(extracted.chunks, vectors, strict=True))
    ]
    replace_document_chunks(session, document, chunks)
    mark_succeeded(session, task, document)
    session.commit()
    logger.info("文档 %s 处理完成，共 %d 个片段", document.id, len(chunks))


def _reclaim_analysis() -> None:
    with db.SessionLocal() as session:
        reclaimed = reclaim_expired_documents(session)
        for execution_id, task_id, team_id, owner_id, exhausted in reclaimed:
            logger.warning("回收了过期的分析执行 execution=%s task=%s", execution_id, task_id)
            if not exhausted:
                with db.SessionLocal() as recovery:
                    apply_context(recovery, team_id=team_id, user_id=owner_id)
                    analysis.reconcile_task(recovery, task_id)
                    recovery.commit()
                continue
            with db.SessionLocal() as recovery:
                apply_context(recovery, team_id=team_id, user_id=owner_id)
                analysis.mark_exhausted(recovery, execution_id)
                recovery.commit()
        summaries = reclaim_expired_summaries(session)
        session.commit()
        for summary_id, task_id, team_id, owner_id, exhausted in summaries:
            with db.SessionLocal() as recovery:
                apply_context(recovery, team_id=team_id, user_id=owner_id)
                if exhausted:
                    analysis.mark_summary_exhausted(recovery, summary_id)
                else:
                    analysis.reconcile_task(recovery, task_id)
                recovery.commit()


def _run_analysis_once() -> bool:
    """领取并执行一份文档的分析；一次只跑一个文档阶段。"""
    with db.SessionLocal() as session:
        claimed = claim_next_document(session, settings.analysis_lease_seconds)
        session.commit()
        if claimed is None:
            session.rollback()
            return False

    from app.analysis_worker import run_analysis_document

    with db.SessionLocal() as session:
        try:
            outcome = run_analysis_document(
                session,
                execution_id=claimed.document_execution_id,
                task_id=claimed.task_id,
                team_id=claimed.team_id,
                owner_id=claimed.owner_id,
                document_id=claimed.document_id,
                lease_token=claimed.lease_token,
            )
        except AnalysisLeaseLost:
            session.rollback()
            logger.warning("分析执行 %s 租约已失效，放弃发布", claimed.document_execution_id)
            return True
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("分析执行 %s 异常", claimed.document_execution_id, exc_info=exc)
            with db.SessionLocal() as recovery:
                # 分析表按“团队 + 创建者”双重 RLS；只设团队上下文会让更新命中 0 行。
                apply_context(recovery, team_id=claimed.team_id, user_id=claimed.owner_id)
                analysis.fail_execution(
                    recovery,
                    execution_id=claimed.document_execution_id,
                    error_code="ANALYSIS_MODEL_UNAVAILABLE",
                    detail=f"未预期错误：{exc}",
                    retryable=True,
                )
                recovery.commit()
            return True
        else:
            session.commit()
            logger.info("分析执行 %s 结束：%s", claimed.document_execution_id, outcome)
    return True


def _run_summary_once() -> bool:
    """领取并执行一次对比汇总；有界模型调用后归还 worker。"""
    with db.SessionLocal() as session:
        claimed = claim_next_summary(session, settings.analysis_lease_seconds)
        session.commit()
        if claimed is None:
            session.rollback()
            return False

    from app.analysis_summary import run_analysis_summary

    with db.SessionLocal() as session:
        try:
            outcome = run_analysis_summary(
                session,
                summary_id=claimed.summary_id,
                task_id=claimed.task_id,
                team_id=claimed.team_id,
                owner_id=claimed.owner_id,
                lease_token=claimed.lease_token,
            )
        except AnalysisLeaseLost:
            session.rollback()
            logger.warning("分析汇总 %s 租约已失效，放弃发布", claimed.summary_id)
            return True
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("分析汇总 %s 异常", claimed.summary_id, exc_info=exc)
            with db.SessionLocal() as recovery:
                apply_context(recovery, team_id=claimed.team_id, user_id=claimed.owner_id)
                analysis.fail_summary(
                    recovery,
                    task_id=claimed.task_id,
                    error_code="ANALYSIS_MODEL_UNAVAILABLE",
                    detail=f"汇总未预期错误：{exc}",
                )
                recovery.commit()
            return True
        else:
            session.commit()
            logger.info("分析汇总 %s 结束：%s", claimed.summary_id, outcome)
    return True


def _run_automation_once() -> bool:
    """协调一个有界步骤；每次只领取、保存状态并归还调度。"""
    with db.SessionLocal() as session:
        claimed = automation.claim_next(session)
        session.commit()
        if claimed is None:
            session.rollback()
            return False

    with db.SessionLocal() as session:
        try:
            outcome = automation.run_once(
                session,
                automation_id=claimed.automation_id,
                team_id=claimed.team_id,
                owner_id=claimed.owner_id,
                lease_token=claimed.lease_token,
            )
        except automation.AutomationLeaseLost:
            session.rollback()
            logger.warning("自动执行 %s 租约已失效，放弃发布", claimed.automation_id)
            return True
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.exception("自动执行 %s 异常", claimed.automation_id, exc_info=exc)
            with db.SessionLocal() as recovery:
                apply_context(
                    recovery, team_id=claimed.team_id, user_id=claimed.owner_id
                )
                item = recovery.get(KnowledgeAutomation, claimed.automation_id)
                if item is not None and item.lease_token == claimed.lease_token:
                    if item.attempts < item.max_attempts:
                        item.error_code = "AUTOMATION_EXHAUSTED"
                        item.error_detail = "协调步骤发生未预期错误，正在自动重试"
                        automation.release_lease(recovery, item, reset_attempts=False)
                    else:
                        automation.finish(
                            recovery,
                            item,
                            status="failed",
                            error_code="AUTOMATION_EXHAUSTED",
                            detail="协调步骤发生未预期错误，重试次数已用完",
                        )
                    recovery.commit()
            return True
        else:
            logger.info("自动执行 %s 结束：%s", claimed.automation_id, outcome)
    return True


def _run_analysis_path_once() -> bool:
    """按固定顺序推进自动执行、对比汇总和逐文档分析。"""
    return _run_automation_once() or _run_summary_once() or _run_analysis_once()


def _run_parsing_once() -> bool:
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


def run_once() -> bool:
    """公平轮询两条执行路径；先到者优先，另一条路径下一次获得机会。"""
    global _LAST_SCHEDULED
    _reclaim_analysis()
    order = ("parsing", "analysis") if _LAST_SCHEDULED == "analysis" else ("analysis", "parsing")
    worked = False
    for kind in order:
        if kind == "parsing":
            if _run_parsing_once():
                worked = True
                _LAST_SCHEDULED = "parsing"
                break
        elif _run_analysis_path_once():
            worked = True
            _LAST_SCHEDULED = "analysis"
            break
    return worked


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
