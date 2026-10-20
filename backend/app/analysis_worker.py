"""V2 分析执行阶段：一次领取只处理一份文档，然后归还调度。

设计要点：

- 每次领取一个执行记录，跑完该文档的有界批次后立即发布并归还，不在一次领取里循环
  处理整批文档，避免长时间占用 worker 与饿死解析任务。
- 调用模型前与发布前都重新校验成员关系、来源快照与租约。
- 外部模型调用在无业务锁的事务外进行：读取与续租各自提交后，再调用模型；发布使用
  新的短事务。
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import analysis
from app.analysis_contract import (
    AnalysisBudgetExhausted,
    AnalysisError,
    ValidatedCandidate,
    parse_batch_output,
    validate_model_call_count,
)
from app.analysis_model import (
    OUTPUT_SCHEMA_VERSION,
    PROMPT_VERSION,
    AnalysisBatch,
    AnalysisFieldSpec,
    AnalysisProviderRejected,
    AnalysisProviderUnavailable,
    get_analysis_provider,
)
from app.analysis_tasks import renew_lease
from app.config import get_settings
from app.db import apply_context
from app.models import AnalysisDocument, AnalysisTask, DocumentChunk

logger = logging.getLogger("kp.analysis")
settings = get_settings()


def _chunks(session: Session, document_id: uuid.UUID) -> list[DocumentChunk]:
    return list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.ordinal)
        ).all()
    )


def _batches(chunks: list[DocumentChunk], batch_chars: int) -> list[list[DocumentChunk]]:
    """按字符预算切分批次；单个超长片段单独成批，不与其他片段混合。"""
    batches: list[list[DocumentChunk]] = []
    current: list[DocumentChunk] = []
    size = 0
    for chunk in chunks:
        length = len(chunk.content)
        if current and size + length > batch_chars:
            batches.append(current)
            current = []
            size = 0
        current.append(chunk)
        size += length
    if current:
        batches.append(current)
    return batches


def _split_oversized(chunk: DocumentChunk, batch_chars: int) -> list[DocumentChunk]:
    """超长片段切成带父定位的子段，保证任何已发布文本都有处理机会。"""
    if len(chunk.content) <= batch_chars:
        return [chunk]
    parts: list[DocumentChunk] = []
    start = 0
    ordinal = 0
    while start < len(chunk.content):
        end = min(start + batch_chars, len(chunk.content))
        piece = chunk.content[start:end]
        if piece.strip():
            ordinal += 1
            parts.append(
                DocumentChunk(
                    # 首个子段沿用父片段 ID，让依据仍能指回真实片段行。
                    id=chunk.id if ordinal == 1 else uuid.uuid4(),
                    team_id=chunk.team_id,
                    document_id=chunk.document_id,
                    ordinal=chunk.ordinal,
                    content=piece,
                    locator_label=f"{chunk.locator_label} · 子段 {ordinal}",
                    locator_start=(chunk.locator_start or 0) + start,
                    locator_end=(chunk.locator_start or 0) + end,
                )
            )
        if end >= len(chunk.content):
            break
        start = end
    return parts or [chunk]


def run_analysis_document(
    session: Session,
    *,
    execution_id: uuid.UUID,
    task_id: uuid.UUID,
    team_id: uuid.UUID,
    owner_id: uuid.UUID,
    document_id: uuid.UUID,
    lease_token: uuid.UUID,
) -> str:
    """处理一份文档并发布；返回终态字符串，供调用方记录。"""
    bounds = analysis.limits()
    apply_context(session, team_id=team_id, user_id=owner_id)

    execution = session.get(AnalysisDocument, execution_id)
    task = session.get(AnalysisTask, task_id)
    if execution is None or task is None:
        session.rollback()
        return "missing"

    # 调用模型前再次确认创建者仍是成员；被移除后立即停止，不继续产生结果。
    if not analysis.is_member(session, team_id, owner_id):
        analysis.mark_permission_revoked(session, task)
        execution.status = "failed"
        execution.error_code = "ANALYSIS_PERMISSION_REVOKED"
        execution.error_detail = "创建者已不是该团队成员"
        execution.lease_token = None
        execution.lease_expires_at = None
        session.commit()
        return "permission_revoked"

    specs = analysis.field_specs(session, task_id)
    # 在事务内把字段定义转成纯数据。提交后 SET LOCAL 失效，再读 ORM 属性会因
    # 缺少 RLS 上下文而报 ObjectDeletedError。
    field_specs = [
        AnalysisFieldSpec(
            id=field.id,
            name=field.name,
            description=field.description,
            value_type=field.value_type,
        )
        for field in analysis.ordered_fields(session, task_id)
    ]
    chunks = _chunks(session, document_id)
    attempts = execution.attempts
    max_attempts = execution.max_attempts
    task_kind = task.kind
    session.commit()

    if not chunks:
        apply_context(session, team_id=team_id, user_id=owner_id)
        return analysis.publish_document(
            session,
            task_id=task_id,
            execution_id=execution_id,
            lease_token=lease_token,
            candidates=[],
            model_calls=0,
            processed_chunk_count=0,
        )

    prepared: list[DocumentChunk] = []
    for chunk in chunks:
        prepared.extend(_split_oversized(chunk, bounds.batch_chars))

    provider = get_analysis_provider()
    candidates: list[ValidatedCandidate] = []
    model_calls = 0
    processed = 0
    provider_name = provider.name
    model_name = provider.model

    for batch_chunks in _batches(prepared, bounds.batch_chars):
        # 每次模型调用前续租；SET LOCAL 随上一个事务结束而失效，必须重新绑定
        # 用户与团队上下文，否则 RLS 会让续租更新命中 0 行。
        apply_context(session, team_id=team_id, user_id=owner_id)
        renew_lease(session, execution_id, lease_token, bounds.lease_seconds)
        session.commit()
        try:
            validate_model_call_count(model_calls, bounds)
        except AnalysisBudgetExhausted as exc:
            # 预算耗尽不再重试：重试只会重复消耗同一上限。
            return _fail(
                session,
                execution_id,
                exc.code,
                exc.detail,
                attempts,
                max_attempts,
                team_id,
                owner_id,
                retryable=False,
            )

        batch = AnalysisBatch(task_kind=task_kind, field_specs=field_specs, chunks=batch_chunks)
        try:
            result = provider.extract(batch)
        except AnalysisProviderRejected as exc:
            # 鉴权/参数问题重试也不会变好，直接终态失败。
            return _fail(
                session,
                execution_id,
                "ANALYSIS_MODEL_UNAVAILABLE",
                f"模型请求被拒绝：{exc}",
                attempts,
                max_attempts,
                team_id,
                owner_id,
                retryable=False,
            )
        except AnalysisProviderUnavailable as exc:
            # 暂时故障按剩余尝试次数重试，耗尽后明确失败，不降级替身。
            return _fail(
                session,
                execution_id,
                "ANALYSIS_MODEL_UNAVAILABLE",
                str(exc),
                attempts,
                max_attempts,
                team_id,
                owner_id,
            )
        model_calls += result.calls
        provider_name = result.provider
        model_name = result.model

        try:
            parsed = parse_batch_output(
                result.raw,
                specs=specs,
                chunk_contents={chunk.id: chunk.content for chunk in batch_chunks},
                limits=bounds,
            )
        except AnalysisError as exc:
            # 输出非法可以有限重试；耗尽后按模型输出错误收敛。
            return _fail(
                session,
                execution_id,
                exc.code,
                exc.detail,
                attempts,
                max_attempts,
                team_id,
                owner_id,
            )
        candidates.extend(parsed)
        processed += len(batch_chunks)

    # 最后一个续租事务已提交，发布前重新绑定上下文。
    apply_context(session, team_id=team_id, user_id=owner_id)
    return analysis.publish_document(
        session,
        task_id=task_id,
        execution_id=execution_id,
        lease_token=lease_token,
        candidates=candidates,
        model_calls=model_calls,
        processed_chunk_count=processed,
        provider=provider_name,
        model=model_name,
        prompt_version=PROMPT_VERSION,
        schema_version=OUTPUT_SCHEMA_VERSION,
    )


def _fail(
    session: Session,
    execution_id: uuid.UUID,
    error_code: str,
    detail: str,
    attempts: int,
    max_attempts: int,
    team_id: uuid.UUID,
    owner_id: uuid.UUID,
    *,
    retryable: bool | None = None,
) -> str:
    """按剩余尝试次数决定重试或终态失败，并收敛父任务。

    `_fail` 常在续租提交之后调用，此时事务级上下文已失效；必须重新绑定用户与团队，
    否则 RLS 会让更新命中 0 行。
    """
    apply_context(session, team_id=team_id, user_id=owner_id)
    analysis.fail_execution(
        session,
        execution_id=execution_id,
        error_code=error_code,
        detail=detail,
        retryable=(attempts < max_attempts) if retryable is None else retryable,
    )
    session.commit()
    return error_code
