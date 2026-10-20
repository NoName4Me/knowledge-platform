"""V2 对比汇总执行：只消费已校验的逐文档提取结果与依据。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app import analysis
from app.analysis_contract import AnalysisError, ValidatedConclusion, parse_comparison_output
from app.analysis_model import (
    AnalysisComparisonBatch,
    AnalysisFieldSpec,
    AnalysisProviderRejected,
    AnalysisProviderUnavailable,
    ComparisonValue,
    get_analysis_provider,
)
from app.analysis_tasks import AnalysisLeaseLost, renew_summary_lease
from app.db import apply_context
from app.models import (
    AnalysisCitation,
    AnalysisConclusion,
    AnalysisDocument,
    AnalysisField,
    AnalysisFieldResult,
    AnalysisSummary,
    AnalysisTask,
    Document,
)


def _render_value(result: AnalysisFieldResult) -> str:
    if result.status == "not_mentioned":
        return "全文已处理，未找到该项"
    if result.value_list:
        return "、".join(result.value_list)
    if result.value_number is not None:
        return f"{result.value_number}{result.unit or ''}"
    if result.value_date is not None:
        return result.raw_value or result.value_date.isoformat()
    return result.value_text or result.raw_value or result.note or ""


def run_analysis_summary(
    session: Session,
    *,
    summary_id: uuid.UUID,
    task_id: uuid.UUID,
    team_id: uuid.UUID,
    owner_id: uuid.UUID,
    lease_token: uuid.UUID,
) -> str:
    """生成并发布对比结论；失败时保留逐文档提取表并收敛为 partial。"""
    bounds = analysis.limits()
    apply_context(session, team_id=team_id, user_id=owner_id)
    summary = session.get(AnalysisSummary, summary_id)
    task = session.get(AnalysisTask, task_id)
    if summary is None or task is None or task.kind != "comparison":
        session.rollback()
        return "missing"
    if not analysis.is_member(session, team_id, owner_id):
        analysis.mark_permission_revoked(session, task)
        summary.status = "failed"
        summary.error_code = "ANALYSIS_PERMISSION_REVOKED"
        summary.error_detail = "创建者已不是该团队成员"
        summary.lease_token = None
        summary.lease_expires_at = None
        session.commit()
        return "permission_revoked"

    executions = list(
        session.scalars(
            select(AnalysisDocument)
            .where(AnalysisDocument.task_id == task_id)
            .order_by(AnalysisDocument.ordinal)
        ).all()
    )
    reason = analysis.source_invalid_reason(session, executions)
    if reason is not None:
        analysis.invalidate_task(session, task, reason)
        summary.status = "failed"
        summary.error_code = "ANALYSIS_SOURCE_INVALIDATED"
        summary.error_detail = reason
        summary.lease_token = None
        summary.lease_expires_at = None
        session.commit()
        return "invalidated"
    if any(execution.status != "succeeded" for execution in executions):
        analysis.fail_summary(
            session,
            task_id=task_id,
            error_code="ANALYSIS_INVALID_INPUT",
            detail="仅有部分文档提取成功，未生成跨文档结论",
        )
        session.commit()
        return "partial"

    fields = list(
        session.scalars(
            select(AnalysisField)
            .where(AnalysisField.task_id == task_id)
            .order_by(AnalysisField.ordinal)
        ).all()
    )
    field_specs = [
        AnalysisFieldSpec(
            id=field.id,
            name=field.name,
            description=field.description,
            value_type=field.value_type,
        )
        for field in fields
    ]
    titles = {
        execution.document_id: (
            source.title
            if (source := session.get(Document, execution.document_id)) is not None
            else "文档"
        )
        for execution in executions
    }
    rows = session.execute(
        select(AnalysisFieldResult, AnalysisField, AnalysisDocument)
        .join(AnalysisField, AnalysisField.id == AnalysisFieldResult.field_id)
        .join(AnalysisDocument, AnalysisDocument.id == AnalysisFieldResult.document_execution_id)
        .where(
            AnalysisFieldResult.task_id == task_id,
            AnalysisDocument.status == "succeeded",
        )
        .order_by(AnalysisDocument.ordinal, AnalysisField.ordinal)
    ).all()
    citations = list(
        session.scalars(
            select(AnalysisCitation).where(
                AnalysisCitation.task_id == task_id,
                AnalysisCitation.field_result_id.is_not(None),
            )
        ).all()
    )
    by_result: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    for citation in citations:
        assert citation.field_result_id is not None
        by_result.setdefault(citation.field_result_id, []).append(
            (citation.id, citation.excerpt)
        )
    comparison_values = [
        ComparisonValue(
            document_id=execution.document_id,
            field_id=result.field_id,
            status=result.status,
            rendered_value=_render_value(result),
            citations=tuple(by_result.get(result.id, [])),
        )
        for result, _field, execution in rows
    ]
    batch = AnalysisComparisonBatch(
        field_specs=field_specs,
        values=comparison_values,
        documents=[
            (execution.document_id, titles[execution.document_id]) for execution in executions
        ],
    )
    attempts = summary.attempts
    max_attempts = summary.max_attempts
    session.commit()

    provider = get_analysis_provider()
    try:
        apply_context(session, team_id=team_id, user_id=owner_id)
        renew_summary_lease(session, summary_id, lease_token, bounds.lease_seconds)
        session.commit()
        model_result = provider.compare(batch)
    except AnalysisLeaseLost:
        session.rollback()
        raise
    except AnalysisProviderRejected as exc:
        return _fail(
            session,
            task_id,
            summary_id,
            str(exc),
            retryable=False,
            team_id=team_id,
            owner_id=owner_id,
        )
    except AnalysisProviderUnavailable as exc:
        return _fail(
            session,
            task_id,
            summary_id,
            str(exc),
            retryable=attempts < max_attempts,
            team_id=team_id,
            owner_id=owner_id,
        )

    apply_context(session, team_id=team_id, user_id=owner_id)
    try:
        conclusions = parse_comparison_output(
            model_result.raw,
            field_ids={field.id for field in fields},
            document_ids={execution.document_id for execution in executions},
            citation_documents={citation.id: citation.document_id for citation in citations},
            limits=bounds,
        )
    except AnalysisError as exc:
        return _fail(
            session,
            task_id,
            summary_id,
            exc.detail,
            retryable=attempts < max_attempts,
            team_id=team_id,
            owner_id=owner_id,
            code=exc.code,
        )
    return _publish(
        session,
        summary_id=summary_id,
        task_id=task_id,
        lease_token=lease_token,
        conclusions=conclusions,
        citations={citation.id: citation for citation in citations},
        provider=model_result.provider,
        model=model_result.model,
        prompt_version=model_result.prompt_version,
        schema_version=model_result.schema_version,
    )


def _fail(
    session: Session,
    task_id: uuid.UUID,
    summary_id: uuid.UUID,
    detail: str,
    *,
    retryable: bool,
    team_id: uuid.UUID,
    owner_id: uuid.UUID,
    code: str = "ANALYSIS_MODEL_UNAVAILABLE",
) -> str:
    apply_context(session, team_id=team_id, user_id=owner_id)
    summary = session.get(AnalysisSummary, summary_id)
    if retryable:
        if summary is not None:
            summary.status = "queued"
            summary.lease_token = None
            summary.lease_expires_at = None
            summary.error_code = code
            summary.error_detail = detail
        session.commit()
        return "retry"
    analysis.fail_summary(session, task_id=task_id, error_code=code, detail=detail)
    session.commit()
    return "failed"


def _publish(
    session: Session,
    *,
    summary_id: uuid.UUID,
    task_id: uuid.UUID,
    lease_token: uuid.UUID,
    conclusions: list[ValidatedConclusion],
    citations: dict[uuid.UUID, AnalysisCitation],
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
) -> str:
    task = session.get(AnalysisTask, task_id)
    summary = session.get(AnalysisSummary, summary_id)
    if task is None or summary is None:
        raise AnalysisLeaseLost
    locked_task = session.execute(
        text("select id from analysis_tasks where id = :task_id for update"),
        {"task_id": task_id},
    ).one_or_none()
    locked_summary = session.execute(
        text(
            "select id from analysis_summaries where id = :id and status = 'running' "
            "and lease_token = :token and lease_expires_at > now() for update"
        ),
        {"id": summary_id, "token": lease_token},
    ).one_or_none()
    if locked_task is None or locked_summary is None:
        raise AnalysisLeaseLost
    session.refresh(task)
    session.refresh(summary)
    if task.status not in analysis.ACTIVE_TASK_STATUSES:
        raise AnalysisLeaseLost
    executions = list(
        session.scalars(
            select(AnalysisDocument).where(AnalysisDocument.task_id == task_id)
        ).all()
    )
    reason = analysis.source_invalid_reason(session, executions)
    if reason is not None:
        analysis.invalidate_task(session, task, reason)
        summary.status = "failed"
        summary.error_code = "ANALYSIS_SOURCE_INVALIDATED"
        summary.error_detail = reason
        summary.lease_token = None
        summary.finished_at = datetime.now(UTC)
        session.flush()
        return "invalidated"

    existing = list(
        session.scalars(
            select(AnalysisConclusion).where(AnalysisConclusion.task_id == task_id)
        ).all()
    )
    existing_ids = [row.id for row in existing]
    if existing_ids:
        session.execute(
            delete(AnalysisCitation).where(AnalysisCitation.conclusion_id.in_(existing_ids))
        )
        session.execute(
            delete(AnalysisConclusion).where(AnalysisConclusion.id.in_(existing_ids))
        )
    session.flush()
    for ordinal, conclusion in enumerate(conclusions):
        row = AnalysisConclusion(
            id=uuid.uuid4(),
            team_id=task.team_id,
            knowledge_base_id=task.knowledge_base_id,
            task_id=task.id,
            owner_id=task.created_by_id,
            field_id=conclusion.field_id,
            kind=conclusion.kind,
            text=conclusion.text,
            document_ids=[str(value) for value in conclusion.document_ids],
            ordinal=ordinal,
        )
        session.add(row)
        session.flush()
        for citation_ordinal, citation_id in enumerate(conclusion.citation_ids):
            source = citations[citation_id]
            session.add(
                AnalysisCitation(
                    id=uuid.uuid4(),
                    team_id=task.team_id,
                    knowledge_base_id=task.knowledge_base_id,
                    task_id=task.id,
                    owner_id=task.created_by_id,
                    # 结论不是单个文档的结果；document_execution_id 只供
                    # 字段结果引用，汇总依据由 conclusion_id 关联。
                    document_execution_id=summary.document_execution_id,
                    field_result_id=None,
                    conclusion_id=row.id,
                    document_id=source.document_id,
                    chunk_id=source.chunk_id,
                    ordinal=citation_ordinal,
                    locator_label=source.locator_label,
                    excerpt=source.excerpt,
                    source_fingerprint=source.source_fingerprint,
                )
            )
    summary.status = "succeeded"
    summary.provider = provider
    summary.model = model
    summary.prompt_version = prompt_version
    summary.schema_version = schema_version
    summary.error_code = None
    summary.error_detail = None
    summary.lease_token = None
    summary.lease_expires_at = None
    summary.finished_at = datetime.now(UTC)
    task.status = "completed"
    task.stage = "finished"
    task.error_code = None
    task.error_detail = None
    task.finished_at = task.finished_at or datetime.now(UTC)
    session.flush()
    return "succeeded"
