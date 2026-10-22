"""V2 分析 API。

路由只处理协议：校验请求形状、调用服务层、把可分类错误映射为 HTTP 状态。所有读取
都重新校验成员关系与本人归属，同团队其他成员与管理员猜 ID 一律 404。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import analysis
from app.analysis_contract import AnalysisError
from app.auth import TeamContext, get_team_context
from app.db import apply_context, get_session
from app.knowledge_base_access import require_knowledge_base
from app.models import (
    AnalysisCitation,
    AnalysisConclusion,
    AnalysisDocument,
    AnalysisField,
    AnalysisFieldResult,
    AnalysisSummary,
    AnalysisTask,
    Document,
    KnowledgeAutomation,
    Report,
    ReportSource,
)
from app.schemas import (
    AnalysisCitationOut,
    AnalysisConclusionOut,
    AnalysisCreateRequest,
    AnalysisDetailOut,
    AnalysisDocumentOut,
    AnalysisDocumentResultOut,
    AnalysisFieldOut,
    AnalysisFieldResultOut,
    AnalysisSummaryOut,
    AnalysisTaskListOut,
    AnalysisTaskOut,
)

router = APIRouter(
    prefix="/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}/analyses",
    tags=["analyses"],
)

# 分类错误 -> HTTP 状态。冲突与容量问题用 409，来源不可用用 422，其余按请求非法处理。
_ERROR_STATUS: dict[str, int] = {
    "ANALYSIS_INVALID_INPUT": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "ANALYSIS_SOURCE_UNAVAILABLE": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "ANALYSIS_IDEMPOTENCY_CONFLICT": status.HTTP_409_CONFLICT,
    "ANALYSIS_LIMIT_EXCEEDED": status.HTTP_409_CONFLICT,
}


def _raise_http(exc: AnalysisError) -> None:
    raise HTTPException(
        status_code=_ERROR_STATUS.get(exc.code, status.HTTP_422_UNPROCESSABLE_CONTENT),
        detail={"code": exc.code, "message": exc.detail},
    ) from exc


def _not_found() -> HTTPException:
    # 统一返回“不存在”，不区分无权限，避免泄漏其他团队或他人任务的存在性。
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分析任务不存在")


@router.post("", response_model=AnalysisTaskOut, status_code=status.HTTP_202_ACCEPTED)
def create_analysis(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    payload: AnalysisCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    if idempotency_key is None:
        raise HTTPException(status_code=422, detail="缺少 Idempotency-Key")
    try:
        parsed_key = uuid.UUID(idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Idempotency-Key 必须是 UUID") from exc

    try:
        created = analysis.create_task(
            session,
            context,
            knowledge_base_id=knowledge_base_id,
            idempotency_key=parsed_key,
            kind=payload.kind,
            title=payload.title,
            document_ids=payload.document_ids,
            fields=[field.model_dump() for field in payload.fields],
            source_task_id=payload.source_task_id,
        )
    except AnalysisError as exc:
        session.rollback()
        _raise_http(exc)

    session.commit()
    body = AnalysisTaskOut.model_validate(created.task).model_dump_json()
    return Response(
        content=body,
        status_code=202 if created.created else 200,
        media_type="application/json",
    )


@router.get("", response_model=AnalysisTaskListOut)
def list_analyses(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> AnalysisTaskListOut:
    try:
        rows, next_cursor = analysis.list_tasks(
            session, context, knowledge_base_id, cursor=cursor, limit=limit
        )
    except AnalysisError as exc:
        _raise_http(exc)
    return AnalysisTaskListOut(
        items=[AnalysisTaskOut.model_validate(row) for row in rows],
        next_cursor=next_cursor,
    )


def _task_or_404(session: Session, context: TeamContext, task_id: uuid.UUID) -> AnalysisTask:
    task = session.get(AnalysisTask, task_id)
    if task is None or task.team_id != context.team.id or task.created_by_id != context.user.id:
        raise _not_found()
    return task


@router.get("/{task_id}", response_model=AnalysisDetailOut)
def get_analysis(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    task_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> AnalysisDetailOut:
    require_knowledge_base(session, context, knowledge_base_id)
    task = session.scalar(
        select(AnalysisTask)
        .where(
            AnalysisTask.id == task_id,
            AnalysisTask.team_id == context.team.id,
            AnalysisTask.knowledge_base_id == knowledge_base_id,
            AnalysisTask.created_by_id == context.user.id,
        )
        .with_for_update()
    )
    if task is None:
        raise _not_found()

    executions = list(
        session.scalars(
            select(AnalysisDocument)
            .where(AnalysisDocument.task_id == task.id)
            .order_by(AnalysisDocument.ordinal)
        ).all()
    )
    fields = analysis.ordered_fields(session, task.id)

    reason = analysis.source_invalid_reason(session, executions)
    task_out = AnalysisTaskOut.model_validate(task)
    if reason is not None:
        # 来源失效：先收敛状态，再只返回元数据。字段定义与文档范围保留，
        # 字段值、摘录与结论一律不返回。
        analysis.invalidate_task(session, task, reason)
        task_out = AnalysisTaskOut.model_validate(task)
        session.commit()
        # 提交后事务级 SET LOCAL 即失效，读取任何过期属性都会触发不带上下文的
        # 重新查询并被 RLS 拒绝；失效视图所需数据已在事务内取好。

    invalid = reason is not None or task.status == "invalidated"
    invalid_reason = task.error_detail if task.status == "invalidated" else reason

    documents: list[AnalysisDocumentOut] = []
    for execution in executions:
        document = session.get(Document, execution.document_id)
        title = document.title if document is not None else "已删除的文档"
        documents.append(
            AnalysisDocumentOut(
                document_id=execution.document_id,
                document_title=title,
                ordinal=execution.ordinal,
                status=execution.status,
                attempts=execution.attempts,
                expected_chunk_count=execution.expected_chunk_count,
                processed_chunk_count=execution.processed_chunk_count,
                provider=execution.provider,
                model=execution.model,
                error_code=execution.error_code,
                error_detail=execution.error_detail,
            )
        )

    results: list[AnalysisDocumentResultOut] = []
    conclusions: list[AnalysisConclusionOut] = []
    summary_out: AnalysisSummaryOut | None = None
    if not invalid:
        results = _results(session, task.id, executions)
        conclusions = _conclusions(session, task.id)
        summary = session.scalars(
            select(AnalysisSummary).where(AnalysisSummary.task_id == task.id)
        ).one_or_none()
        if summary is not None:
            summary_out = AnalysisSummaryOut(
                status=summary.status,
                attempts=summary.attempts,
                provider=summary.provider,
                model=summary.model,
                error_code=summary.error_code,
                error_detail=summary.error_detail,
            )

    return AnalysisDetailOut(
        task=task_out,
        fields=[AnalysisFieldOut.model_validate(field) for field in fields],
        documents=documents,
        results=results,
        conclusions=conclusions,
        summary=summary_out,
        source_valid=not invalid,
        invalid_reason=invalid_reason,
    )


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_analysis(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    task_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    require_knowledge_base(session, context, knowledge_base_id)
    task = _task_or_404(session, context, task_id)
    if task.knowledge_base_id != knowledge_base_id:
        raise _not_found()
    if task.status in analysis.ACTIVE_TASK_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ANALYSIS_ACTIVE",
                "message": "分析仍在处理中；请等待完成，或先取消对应的自动成稿后再删除。",
            },
        )

    report_ids = list(
        session.scalars(select(Report.id).where(Report.source_analysis_id == task.id))
    )
    if report_ids:
        # ReportSource 的复合唯一键包含来源 ID；删除前换成不可冲突的旧 ID，
        # 并保留可读的冻结标题快照。报告正文与章节引用不变。
        sources = list(
            session.scalars(
                select(ReportSource).where(
                    ReportSource.report_id.in_(report_ids),
                    ReportSource.source_type == "analysis",
                    ReportSource.source_id == task.id,
                )
            )
        )
        for source in sources:
            source.source_id = uuid.uuid4()
            source.title_snapshot = f"{source.title_snapshot}（来源分析已删除）"[:300]
        session.execute(
            update(Report)
            .where(Report.id.in_(report_ids), Report.source_analysis_id == task.id)
            .values(source_analysis_id=None)
        )
    session.execute(
        update(KnowledgeAutomation)
        .where(
            KnowledgeAutomation.team_id == task.team_id,
            KnowledgeAutomation.knowledge_base_id == task.knowledge_base_id,
            KnowledgeAutomation.created_by_id == task.created_by_id,
            KnowledgeAutomation.analysis_id == task.id,
        )
        .values(analysis_id=None)
    )
    session.execute(
        update(AnalysisTask)
        .where(AnalysisTask.source_task_id == task.id)
        .values(source_task_id=None)
    )
    # 同一事务先触发了跨表更新；在父任务删除前显式恢复已校验的本人范围。
    apply_context(session, team_id=task.team_id, user_id=task.created_by_id)
    session.delete(task)
    session.flush()
    session.commit()


def _results(
    session: Session, task_id: uuid.UUID, executions: list[AnalysisDocument]
) -> list[AnalysisDocumentResultOut]:
    execution_by_id = {execution.id: execution for execution in executions}
    titles: dict[uuid.UUID, str] = {}
    for execution in executions:
        source = session.get(Document, execution.document_id)
        if source is not None:
            titles[execution.document_id] = source.title

    # 按文档执行顺序、再按字段定义顺序输出，与固定输入一致。
    execution_order = {execution.id: execution.ordinal for execution in executions}
    rows = session.execute(
        select(AnalysisFieldResult, AnalysisField)
        .join(AnalysisField, AnalysisField.id == AnalysisFieldResult.field_id)
        .where(
            AnalysisFieldResult.task_id == task_id,
            AnalysisFieldResult.status != "failed",
        )
        .order_by(AnalysisField.ordinal)
    ).all()
    rows = sorted(
        rows,
        key=lambda item: (
            execution_order.get(item[0].document_execution_id, 0),
            item[1].ordinal,
        ),
    )

    citations = _citations(session, task_id)
    by_result: dict[uuid.UUID, list[AnalysisCitationOut]] = {}
    for citation, field_result_id in citations:
        by_result.setdefault(field_result_id, []).append(citation)

    grouped: dict[uuid.UUID, AnalysisDocumentResultOut] = {}
    for result, _field in rows:
        source_execution = execution_by_id.get(result.document_execution_id)
        if source_execution is None or source_execution.status != "succeeded":
            # 失败文档不展示半成品。
            continue
        group = grouped.get(source_execution.id)
        if group is None:
            group = AnalysisDocumentResultOut(
                document_id=source_execution.document_id,
                document_title=titles.get(source_execution.document_id, "文档"),
                ordinal=source_execution.ordinal,
                fields=[],
            )
            grouped[source_execution.id] = group
        group.fields.append(
            AnalysisFieldResultOut(
                field_id=result.field_id,
                status=result.status,
                value_text=result.value_text,
                value_number=float(result.value_number)
                if result.value_number is not None
                else None,
                value_date=result.value_date,
                value_list=result.value_list,
                raw_value=result.raw_value,
                unit=result.unit,
                date_precision=result.date_precision,
                note=result.note,
                citations=by_result.get(result.id, []),
            )
        )
    return sorted(grouped.values(), key=lambda item: item.ordinal)


def _citations(
    session: Session, task_id: uuid.UUID
) -> list[tuple[AnalysisCitationOut, uuid.UUID]]:
    rows = session.execute(
        select(AnalysisCitation, Document.title)
        .join(Document, Document.id == AnalysisCitation.document_id)
        .where(
            AnalysisCitation.task_id == task_id,
            (
                AnalysisCitation.field_result_id.is_not(None)
                | AnalysisCitation.conclusion_id.is_not(None)
            ),
        )
        .order_by(
            AnalysisCitation.field_result_id,
            AnalysisCitation.conclusion_id,
            AnalysisCitation.ordinal,
        )
    ).all()
    result: list[tuple[AnalysisCitationOut, uuid.UUID]] = []
    for citation, title in rows:
        target_id = citation.field_result_id or citation.conclusion_id
        if target_id is None:
            continue
        result.append(
            (
                AnalysisCitationOut(
                    id=citation.id,
                    document_id=citation.document_id,
                    document_title=title,
                    locator_label=citation.locator_label,
                    excerpt=citation.excerpt,
                    table_number=citation.table_number,
                    table_row=citation.table_row,
                    table_headers=citation.table_headers,
                    table_cells=citation.table_cells,
                ),
                target_id,
            )
        )
    return result


def _conclusions(session: Session, task_id: uuid.UUID) -> list[AnalysisConclusionOut]:
    rows = session.execute(
        select(AnalysisConclusion)
        .where(AnalysisConclusion.task_id == task_id)
        .order_by(AnalysisConclusion.ordinal)
    ).scalars()
    citations = _citations(session, task_id)
    by_conclusion: dict[uuid.UUID, list[AnalysisCitationOut]] = {}
    for citation, target in citations:
        by_conclusion.setdefault(target, []).append(citation)

    conclusions: list[AnalysisConclusionOut] = []
    for row in rows:
        conclusions.append(
            AnalysisConclusionOut(
                id=row.id,
                field_id=row.field_id,
                kind=row.kind,
                text=row.text,
                document_ids=[uuid.UUID(value) for value in row.document_ids],
                citations=by_conclusion.get(row.id, []),
            )
        )
    return conclusions
