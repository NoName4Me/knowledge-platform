"""V4 固定计划的从分析到报告执行。

用户确认的文档范围、字段和报告要求是本次执行唯一的输入。worker 先创建并等待一项
现有 V2 分析，只有分析完整成功时才调用现有报告服务。执行记录、步骤与成果 ID 都在
同一归属范围内持久化；每一步发布前重新检查权限、来源和取消状态。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import analysis, knowledge_products
from app.analysis_contract import AnalysisError, validate_create_request, validate_text_budget
from app.auth import TeamContext
from app.config import get_settings
from app.db import apply_context
from app.models import (
    AnalysisDocument,
    AnalysisSummary,
    AnalysisTask,
    Document,
    KnowledgeAutomation,
    KnowledgeAutomationStep,
    KnowledgeBase,
    Report,
    Team,
    TeamMembership,
    User,
)
from app.schemas import (
    AutomationCreateRequest,
    AutomationDetailOut,
    AutomationOut,
    AutomationStepOut,
    ReportFromAnalysisRequest,
)

settings = get_settings()

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("completed", "failed", "canceled", "invalidated")


class AutomationError(Exception):
    code = "AUTOMATION_INVALID_INPUT"
    status_code = 422

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class AutomationNotFound(AutomationError):
    status_code = 404


class AutomationConflict(AutomationError):
    code = "AUTOMATION_IDEMPOTENCY_CONFLICT"
    status_code = 409


class AutomationLimitExceeded(AutomationError):
    code = "AUTOMATION_LIMIT_EXCEEDED"
    status_code = 409


class AutomationSourceUnavailable(AutomationError):
    code = "AUTOMATION_SOURCE_UNAVAILABLE"
    status_code = 422


class AutomationLeaseLost(Exception):
    """当前执行已由其他 worker、取消或完成事务接管，本次不得发布成果。"""


@dataclass(frozen=True)
class ClaimedAutomation:
    automation_id: uuid.UUID
    team_id: uuid.UUID
    owner_id: uuid.UUID
    lease_token: uuid.UUID


def _fingerprint(payload: AutomationCreateRequest) -> str:
    data = {
        "kind": payload.kind,
        "documents": sorted(str(item) for item in payload.document_ids),
        "fields": [
            {
                "name": field.name.strip(),
                "description": field.description.strip(),
                "value_type": field.value_type,
            }
            for field in payload.fields
        ],
        "analysis_title": (payload.analysis_title or "").strip(),
        "report_title": payload.report_title.strip(),
        "report_purpose": payload.report_purpose.strip(),
        "report_focus": payload.report_focus.strip(),
        "report_length": payload.report_length,
    }
    serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _knowledge_base(
    session: Session, context: TeamContext, knowledge_base_id: uuid.UUID
) -> KnowledgeBase:
    kb = session.get(KnowledgeBase, knowledge_base_id)
    if kb is None or kb.team_id != context.team.id:
        raise AutomationSourceUnavailable("知识库不存在")
    return kb


def _validate_sources(
    session: Session,
    *,
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    document_ids: list[uuid.UUID],
) -> list[Document]:
    documents = list(
        session.scalars(
            select(Document)
            .where(
                Document.id.in_(document_ids),
                Document.team_id == team_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.id)
        )
    )
    by_id = {document.id: document for document in documents}
    ordered: list[Document] = []
    for document_id in document_ids:
        document = by_id.get(document_id)
        if document is None or document.status != "ready":
            raise AutomationSourceUnavailable("所选资料不可用于执行")
        ordered.append(document)
    return ordered


def _validate_analysis_payload(payload: AutomationCreateRequest) -> None:
    if len(set(payload.document_ids)) != len(payload.document_ids):
        raise AutomationError("资料范围不能包含重复项")
    if payload.kind == "comparison" and len(payload.document_ids) < 2:
        raise AutomationError("文档对比至少需要两份资料")
    names = [field.name.strip() for field in payload.fields]
    if len(set(names)) != len(names):
        raise AutomationError("字段名称不能重复")


def _add_step(
    session: Session,
    *,
    automation: KnowledgeAutomation,
    kind: str,
    status: str,
    target_id: uuid.UUID | None = None,
    request_id: str | None = None,
    error_code: str | None = None,
    detail: str | None = None,
) -> KnowledgeAutomationStep:
    ordinal = (
        session.scalar(
            select(func.coalesce(func.max(KnowledgeAutomationStep.ordinal), 0)).where(
                KnowledgeAutomationStep.automation_id == automation.id
            )
        )
        or 0
    )
    step = KnowledgeAutomationStep(
        team_id=automation.team_id,
        knowledge_base_id=automation.knowledge_base_id,
        automation_id=automation.id,
        owner_id=automation.created_by_id,
        ordinal=int(ordinal) + 1,
        kind=kind,
        status=status,
        target_id=target_id,
        request_id=request_id,
        error_code=error_code,
        detail=detail[:500] if detail else None,
        finished_at=datetime.now(UTC) if status != "started" else None,
    )
    session.add(step)
    session.flush()
    return step


def _find_open_step(
    session: Session, automation_id: uuid.UUID, kind: str
) -> KnowledgeAutomationStep | None:
    return session.scalars(
        select(KnowledgeAutomationStep)
        .where(
            KnowledgeAutomationStep.automation_id == automation_id,
            KnowledgeAutomationStep.kind == kind,
            KnowledgeAutomationStep.status == "started",
        )
        .order_by(KnowledgeAutomationStep.ordinal.desc())
        .limit(1)
    ).first()


def _latest_step(
    session: Session, automation_id: uuid.UUID, kind: str
) -> KnowledgeAutomationStep | None:
    return session.scalars(
        select(KnowledgeAutomationStep)
        .where(
            KnowledgeAutomationStep.automation_id == automation_id,
            KnowledgeAutomationStep.kind == kind,
        )
        .order_by(KnowledgeAutomationStep.ordinal.desc())
        .limit(1)
    ).first()


def create_automation(
    session: Session,
    context: TeamContext,
    *,
    knowledge_base_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    payload: AutomationCreateRequest,
) -> tuple[KnowledgeAutomation, bool]:
    """创建固定计划；幂等重放不得重新排队或修改已确认输入。"""
    _knowledge_base(session, context, knowledge_base_id)
    _validate_analysis_payload(payload)
    fingerprint = _fingerprint(payload)

    # 团队锁让活跃执行数与创建串行化，不能靠前端禁用按钮限制并发。
    session.execute(text("select app_lock_team(:team_id)"), {"team_id": context.team.id})
    existing = session.scalars(
        select(KnowledgeAutomation).where(
            KnowledgeAutomation.team_id == context.team.id,
            KnowledgeAutomation.created_by_id == context.user.id,
            KnowledgeAutomation.idempotency_key == idempotency_key,
        )
    ).one_or_none()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise AutomationConflict("幂等键已被不同执行要求使用")
        return existing, False

    user_active = session.scalar(
        select(func.count(KnowledgeAutomation.id)).where(
            KnowledgeAutomation.team_id == context.team.id,
            KnowledgeAutomation.created_by_id == context.user.id,
            KnowledgeAutomation.status.in_(ACTIVE_STATUSES),
        )
    )
    team_active = session.scalar(
        select(func.count(KnowledgeAutomation.id)).where(
            KnowledgeAutomation.team_id == context.team.id,
            KnowledgeAutomation.status.in_(ACTIVE_STATUSES),
        )
    )
    if int(user_active or 0) >= settings.automation_user_max_active:
        raise AutomationLimitExceeded("同时进行的自动执行已达上限，请等待或结束已有执行")
    if int(team_active or 0) >= settings.automation_team_max_active:
        raise AutomationLimitExceeded("团队同时进行的自动执行已达上限")

    documents = _validate_sources(
        session,
        team_id=context.team.id,
        knowledge_base_id=knowledge_base_id,
        document_ids=payload.document_ids,
    )
    # 直接复用 V2 的请求与文本预算校验，不能给执行器另一套宽松上限。
    from app.schemas import AnalysisCreateRequest

    try:
        validate_create_request(
            AnalysisCreateRequest(
                kind=payload.kind,
                document_ids=payload.document_ids,
                fields=payload.fields,
                title=payload.analysis_title,
            ),
            analysis.limits(),
        )
        snapshots = [analysis._chunk_rows(session, document.id) for document in documents]
        validate_text_budget(
            [analysis.published_text_chars(chunks) for chunks in snapshots], analysis.limits()
        )
    except AnalysisError as exc:
        raise AutomationError(exc.detail) from exc

    analysis_title = (payload.analysis_title or "").strip() or (
        f"{analysis.KIND_LABELS[payload.kind]} · {len(documents)} 份文档"
    )
    report_title = payload.report_title.strip() or f"{analysis_title} · 报告"
    automation = KnowledgeAutomation(
        team_id=context.team.id,
        knowledge_base_id=knowledge_base_id,
        created_by_id=context.user.id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        title=report_title[:120],
        analysis_title=analysis_title[:120],
        status="queued",
        stage="queued",
        analysis_kind=payload.kind,
        document_ids=[str(item) for item in payload.document_ids],
        fields=[field.model_dump(mode="json") for field in payload.fields],
        report_title=report_title,
        report_purpose=payload.report_purpose.strip(),
        report_focus=payload.report_focus.strip(),
        report_length=payload.report_length,
        document_count=len(documents),
        attempts=0,
        max_attempts=settings.automation_max_attempts,
    )
    session.add(automation)
    session.flush()
    _add_step(
        session,
        automation=automation,
        kind="start",
        status="succeeded",
        detail="用户已确认资料范围、分析字段和报告要求",
    )
    session.flush()
    return automation, True


def _automation_out(item: KnowledgeAutomation) -> AutomationOut:
    from app.schemas import AnalysisFieldInput

    return AutomationOut(
        id=item.id,
        knowledge_base_id=item.knowledge_base_id,
        title=item.title,
        status=item.status,
        stage=item.stage,
        analysis_kind=item.analysis_kind,
        document_ids=[uuid.UUID(value) for value in item.document_ids],
        document_count=item.document_count,
        fields=[AnalysisFieldInput.model_validate(field) for field in item.fields],
        analysis_title=item.analysis_title,
        report_title=item.report_title,
        report_purpose=item.report_purpose,
        report_focus=item.report_focus,
        report_length=item.report_length,
        analysis_id=item.analysis_id,
        report_id=item.report_id,
        attempts=item.attempts,
        max_attempts=item.max_attempts,
        error_code=item.error_code,
        error_detail=item.error_detail,
        created_at=item.created_at,
        updated_at=item.updated_at,
        started_at=item.started_at,
        finished_at=item.finished_at,
    )


def list_automations(
    session: Session, context: TeamContext, knowledge_base_id: uuid.UUID
) -> list[AutomationOut]:
    _knowledge_base(session, context, knowledge_base_id)
    rows = session.scalars(
        select(KnowledgeAutomation)
        .where(
            KnowledgeAutomation.team_id == context.team.id,
            KnowledgeAutomation.knowledge_base_id == knowledge_base_id,
            KnowledgeAutomation.created_by_id == context.user.id,
        )
        .order_by(KnowledgeAutomation.created_at.desc(), KnowledgeAutomation.id.desc())
        .limit(50)
    )
    return [_automation_out(item) for item in rows]


def get_automation(
    session: Session,
    context: TeamContext,
    *,
    knowledge_base_id: uuid.UUID,
    automation_id: uuid.UUID,
) -> AutomationDetailOut:
    _knowledge_base(session, context, knowledge_base_id)
    item = session.scalar(
        select(KnowledgeAutomation).where(
            KnowledgeAutomation.id == automation_id,
            KnowledgeAutomation.team_id == context.team.id,
            KnowledgeAutomation.knowledge_base_id == knowledge_base_id,
            KnowledgeAutomation.created_by_id == context.user.id,
        )
    )
    if item is None:
        raise AutomationNotFound("执行记录不存在")
    steps = session.scalars(
        select(KnowledgeAutomationStep)
        .where(KnowledgeAutomationStep.automation_id == item.id)
        .order_by(KnowledgeAutomationStep.ordinal)
    )
    base = _automation_out(item)
    return AutomationDetailOut(
        **base.model_dump(),
        steps=[
            AutomationStepOut(
                id=step.id,
                ordinal=step.ordinal,
                kind=step.kind,
                status=step.status,
                target_id=step.target_id,
                error_code=step.error_code,
                detail=step.detail,
                created_at=step.created_at,
                finished_at=step.finished_at,
            )
            for step in steps
        ],
    )


def request_cancel(
    session: Session,
    context: TeamContext,
    *,
    knowledge_base_id: uuid.UUID,
    automation_id: uuid.UUID,
) -> AutomationOut:
    _knowledge_base(session, context, knowledge_base_id)
    item = session.scalar(
        select(KnowledgeAutomation)
        .where(
            KnowledgeAutomation.id == automation_id,
            KnowledgeAutomation.team_id == context.team.id,
            KnowledgeAutomation.knowledge_base_id == knowledge_base_id,
            KnowledgeAutomation.created_by_id == context.user.id,
        )
        .with_for_update()
    )
    if item is None:
        raise AutomationNotFound("执行记录不存在")
    if item.status not in ACTIVE_STATUSES:
        return _automation_out(item)
    item.cancel_requested_at = item.cancel_requested_at or datetime.now(UTC)
    item.cancel_requested_after_continue = True
    child_stopped = _cancel_child_analysis(session, item)
    _add_step(
        session,
        automation=item,
        kind="cancel",
        status="succeeded",
        detail=(
            "执行已取消，独占子分析已停止；已完成成果保留"
            if child_stopped
            else "执行已取消，不再创建或发布后续成果；已完成成果保留"
        ),
    )
    # 取消立即收敛；正在执行的外部调用返回后会因租约或状态变化而回滚发布。
    _close_open_step(session, item, kind="analysis", status="canceled")
    _close_open_step(session, item, kind="report", status="canceled")
    finish(session, item, status="canceled", detail="执行已按请求取消", reset_attempts=True)
    session.flush()
    return _automation_out(item)


def _cancel_child_analysis(
    session: Session, item: KnowledgeAutomation
) -> bool:
    """停止本次执行独占且仍在运行的子分析，迟到模型结果不得再发布。"""
    if item.analysis_id is None:
        return False
    task = session.get(AnalysisTask, item.analysis_id)
    if task is None or task.created_by_id != item.created_by_id:
        return False
    if task.status not in ACTIVE_STATUSES:
        return False

    now = datetime.now(UTC)
    task.status = "failed"
    task.stage = "finished"
    task.error_code = "ANALYSIS_CANCELED"
    task.error_detail = "上级自动成稿已取消"
    task.finished_at = now
    for execution in session.scalars(
        select(AnalysisDocument).where(
            AnalysisDocument.task_id == task.id,
            AnalysisDocument.status.in_(ACTIVE_STATUSES),
        )
    ):
        execution.status = "failed"
        execution.error_code = "ANALYSIS_CANCELED"
        execution.error_detail = "上级自动成稿已取消"
        execution.lease_token = None
        execution.lease_expires_at = None
        execution.finished_at = now
    for summary in session.scalars(
        select(AnalysisSummary).where(
            AnalysisSummary.task_id == task.id,
            AnalysisSummary.status.in_(ACTIVE_STATUSES),
        )
    ):
        summary.status = "failed"
        summary.error_code = "ANALYSIS_CANCELED"
        summary.error_detail = "上级自动成稿已取消"
        summary.lease_token = None
        summary.lease_expires_at = None
        summary.finished_at = now
    session.flush()
    return True


def request_continue(
    session: Session,
    context: TeamContext,
    *,
    knowledge_base_id: uuid.UUID,
    automation_id: uuid.UUID,
) -> AutomationOut:
    _knowledge_base(session, context, knowledge_base_id)
    item = session.scalar(
        select(KnowledgeAutomation)
        .where(
            KnowledgeAutomation.id == automation_id,
            KnowledgeAutomation.team_id == context.team.id,
            KnowledgeAutomation.knowledge_base_id == knowledge_base_id,
            KnowledgeAutomation.created_by_id == context.user.id,
        )
        .with_for_update()
    )
    if item is None:
        raise AutomationNotFound("执行记录不存在")
    if item.status != "failed":
        return _automation_out(item)
    if item.error_code in {
        "AUTOMATION_ANALYSIS_PARTIAL",
        "AUTOMATION_ANALYSIS_INVALIDATED",
        "AUTOMATION_PERMISSION_REVOKED",
    }:
        raise AutomationError("当前失败原因需要调整资料范围后新建执行")
    _validate_sources(
        session,
        team_id=item.team_id,
        knowledge_base_id=item.knowledge_base_id,
        document_ids=[uuid.UUID(value) for value in item.document_ids],
    )
    if not analysis.is_member(session, item.team_id, item.created_by_id):
        raise AutomationError("当前账号已不能继续这项执行")
    if item.error_code == "AUTOMATION_ANALYSIS_FAILED":
        # V2 终态分析通过新任务重跑；旧任务及其步骤 ID 保留在步骤历史中。
        item.analysis_id = None
    item.status = "queued"
    item.stage = "analysis" if item.analysis_id is None else "report"
    item.error_code = None
    item.error_detail = None
    item.finished_at = None
    item.attempts = 0
    item.continue_requested_at = datetime.now(UTC)
    item.cancel_requested_at = None
    item.cancel_requested_after_continue = False
    _add_step(
        session,
        automation=item,
        kind="continue",
        status="succeeded",
        detail="用户重新启动失败步骤，成功检查点将复用",
    )
    session.flush()
    return _automation_out(item)


def delete_automation(
    session: Session,
    context: TeamContext,
    *,
    knowledge_base_id: uuid.UUID,
    automation_id: uuid.UUID,
) -> None:
    """仅删除执行记录与步骤；已生成的分析和报告保持独立可访问。"""
    _knowledge_base(session, context, knowledge_base_id)
    item = session.scalar(
        select(KnowledgeAutomation)
        .where(
            KnowledgeAutomation.id == automation_id,
            KnowledgeAutomation.team_id == context.team.id,
            KnowledgeAutomation.knowledge_base_id == knowledge_base_id,
            KnowledgeAutomation.created_by_id == context.user.id,
        )
        .with_for_update()
    )
    if item is None:
        raise AutomationNotFound("执行记录不存在")
    if item.status in ACTIVE_STATUSES:
        # 活动执行先收口取消状态；删除接口在同一事务中继续移除记录。
        item.cancel_requested_at = item.cancel_requested_at or datetime.now(UTC)
        item.cancel_requested_after_continue = True
        _cancel_child_analysis(session, item)
        _close_open_step(session, item, kind="analysis", status="canceled")
        _close_open_step(session, item, kind="report", status="canceled")
        finish(session, item, status="canceled", detail="删除执行记录前已停止后续步骤")
    session.delete(item)
    session.flush()


def claim_next(session: Session) -> ClaimedAutomation | None:
    row = session.execute(
        text(
            "select * from app_claim_knowledge_automation(:lease_seconds)"
        ),
        {"lease_seconds": settings.automation_lease_seconds},
    ).one_or_none()
    if row is None:
        return None
    claimed = ClaimedAutomation(
        automation_id=row.automation_id,
        team_id=row.team_id,
        owner_id=row.owner_id,
        lease_token=row.lease_token,
    )
    return claimed


def lease_held(
    session: Session, automation_id: uuid.UUID, lease_token: uuid.UUID
) -> bool:
    return (
        session.scalar(
            select(KnowledgeAutomation.id).where(
                KnowledgeAutomation.id == automation_id,
                KnowledgeAutomation.status == "running",
                KnowledgeAutomation.lease_token == lease_token,
                KnowledgeAutomation.lease_expires_at > func.now(),
            )
        )
        is not None
    )


def finish(
    session: Session,
    item: KnowledgeAutomation,
    *,
    status: str,
    error_code: str | None = None,
    detail: str | None = None,
    reset_attempts: bool = False,
) -> None:
    item.status = status
    item.stage = "finished"
    item.error_code = error_code
    item.error_detail = detail[:500] if detail else None
    item.lease_token = None
    item.lease_expires_at = None
    if reset_attempts:
        item.attempts = 0
    item.finished_at = item.finished_at or datetime.now(UTC)
    session.flush()


def release_lease(
    session: Session,
    item: KnowledgeAutomation,
    *,
    reset_attempts: bool,
) -> None:
    """完成一个有界步骤后归还 worker，避免等待阶段持续占用重试次数。"""
    # 领取函数只接受 queued 或租约已过期的 running；正常归还后必须重新排队。
    item.status = "queued"
    item.lease_token = None
    item.lease_expires_at = None
    if reset_attempts:
        item.attempts = 0
    session.flush()


def _close_open_step(
    session: Session,
    item: KnowledgeAutomation,
    *,
    kind: str,
    status: str,
    error_code: str | None = None,
    detail: str | None = None,
) -> None:
    step = _find_open_step(session, item.id, kind)
    if step is None:
        return
    step.status = status
    step.error_code = error_code
    step.detail = detail[:500] if detail else step.detail
    step.finished_at = datetime.now(UTC)
    session.flush()


def _check_owner(session: Session, item: KnowledgeAutomation) -> bool:
    return (
        session.scalar(
            select(TeamMembership.id).where(
                TeamMembership.team_id == item.team_id,
                TeamMembership.user_id == item.created_by_id,
            )
        )
        is not None
    )


def _create_analysis(session: Session, item: KnowledgeAutomation, context: TeamContext) -> None:
    from app.schemas import AnalysisFieldInput

    prior_attempts = session.scalar(
        select(func.count(KnowledgeAutomationStep.id)).where(
            KnowledgeAutomationStep.automation_id == item.id,
            KnowledgeAutomationStep.kind == "analysis",
        )
    )
    created = analysis.create_task(
        session,
        context,
        knowledge_base_id=item.knowledge_base_id,
        idempotency_key=uuid.uuid5(item.id, f"analysis-{int(prior_attempts or 0) + 1}"),
        kind=item.analysis_kind,
        title=item.analysis_title,
        document_ids=[uuid.UUID(value) for value in item.document_ids],
        fields=[AnalysisFieldInput.model_validate(field).model_dump() for field in item.fields],
        source_task_id=None,
    )
    item.analysis_id = created.task.id
    _add_step(
        session,
        automation=item,
        kind="analysis",
        status="started",
        target_id=created.task.id,
        request_id=str(item.id),
        detail="已创建分析任务",
    )


def _publish_report(session: Session, item: KnowledgeAutomation, context: TeamContext) -> None:
    if item.analysis_id is None:
        raise AutomationError("执行缺少分析任务")
    if item.report_id is not None:
        existing_report = session.get(Report, item.report_id)
        if existing_report is not None:
            return
    created_report = knowledge_products.create_report_from_analysis(
        session,
        context,
        item.knowledge_base_id,
        item.analysis_id,
        ReportFromAnalysisRequest(
            title=item.report_title,
            purpose=item.report_purpose,
            focus=item.report_focus,
            length=item.report_length,
        ),
    )
    item.report_id = created_report.id
    _close_open_step(
        session,
        item,
        kind="report",
        status="succeeded",
        detail="报告草稿已生成，仍需人工核对",
    )


def _worker_context(team_id: uuid.UUID, owner_id: uuid.UUID) -> TeamContext:
    """worker 只使用已由领取函数验证的身份 ID，不读取受限的 teams/users 表。"""
    user = User(
        id=owner_id,
        email="worker@invalid.local",
        display_name="worker",
        password_hash="",
        is_active=True,
    )
    team = Team(id=team_id, name="")
    return TeamContext(user=user, team=team, role="member")


def run_once(
    session: Session,
    *,
    automation_id: uuid.UUID,
    team_id: uuid.UUID,
    owner_id: uuid.UUID,
    lease_token: uuid.UUID,
) -> str:
    """执行一个有界协调步骤，然后归还 worker。"""
    apply_context(session, team_id=team_id, user_id=owner_id)
    item = session.get(KnowledgeAutomation, automation_id)
    if item is None:
        return "missing"
    if item.lease_token != lease_token or item.status != "running":
        # 旧 worker、取消或完成事务已经接管；迟到结果必须静默放弃。
        return "lease_lost"
    if not _check_owner(session, item):
        finish(
            session,
            item,
            status="failed",
            error_code="AUTOMATION_PERMISSION_REVOKED",
            detail="创建者已不能继续执行",
        )
        session.commit()
        return "permission_revoked"
    item.error_code = None
    item.error_detail = None
    if item.cancel_requested_at is not None and item.cancel_requested_after_continue:
        _close_open_step(session, item, kind="analysis", status="canceled")
        _close_open_step(session, item, kind="report", status="canceled")
        _close_open_step(session, item, kind="cancel", status="succeeded")
        finish(
            session,
            item,
            status="canceled",
            detail="执行已按请求取消",
            reset_attempts=True,
        )
        session.commit()
        return "canceled"

    if item.analysis_id is None:
        context = _worker_context(team_id, owner_id)
        try:
            _create_analysis(session, item, context)
        except AnalysisError as exc:
            _add_step(
                session,
                automation=item,
                kind="analysis",
                status="failed",
                error_code=exc.code,
                detail=exc.detail,
            )
            finish(
                session,
                item,
                status="failed",
                error_code="AUTOMATION_ANALYSIS_FAILED",
                detail=exc.detail,
            )
            session.commit()
            return "analysis_create_failed"
        item.stage = "analysis"
        release_lease(session, item, reset_attempts=True)
        session.commit()
        return "analysis_queued"

    analysis_task = session.get(AnalysisTask, item.analysis_id)
    if analysis_task is None:
        _close_open_step(
            session,
            item,
            kind="analysis",
            status="failed",
            error_code="AUTOMATION_ANALYSIS_FAILED",
            detail="分析记录不存在",
        )
        finish(
            session,
            item,
            status="failed",
            error_code="AUTOMATION_ANALYSIS_FAILED",
            detail="分析记录不存在",
        )
        session.commit()
        return "analysis_missing"
    if analysis_task.status in ACTIVE_STATUSES:
        item.stage = "analysis"
        release_lease(session, item, reset_attempts=True)
        session.commit()
        return "waiting_analysis"
    if analysis_task.status == "partial":
        _close_open_step(
            session,
            item,
            kind="analysis",
            status="failed",
            error_code="AUTOMATION_ANALYSIS_PARTIAL",
            detail="分析部分完成，保留中间结果，未自动生成报告",
        )
        finish(
            session,
            item,
            status="failed",
            error_code="AUTOMATION_ANALYSIS_PARTIAL",
            detail="分析部分完成，请查看分析记录或调整资料范围",
        )
        session.commit()
        return "analysis_partial"
    if analysis_task.status == "invalidated":
        _close_open_step(
            session,
            item,
            kind="analysis",
            status="failed",
            error_code="AUTOMATION_ANALYSIS_INVALIDATED",
            detail=analysis_task.error_detail,
        )
        finish(
            session,
            item,
            status="invalidated",
            error_code="AUTOMATION_ANALYSIS_INVALIDATED",
            detail=analysis_task.error_detail,
        )
        session.commit()
        return "analysis_invalidated"
    if analysis_task.status != "completed":
        detail = analysis_task.error_detail or "分析未完成"
        _close_open_step(
            session,
            item,
            kind="analysis",
            status="failed",
            error_code="AUTOMATION_ANALYSIS_FAILED",
            detail=detail,
        )
        finish(
            session,
            item,
            status="failed",
            error_code="AUTOMATION_ANALYSIS_FAILED",
            detail=detail,
        )
        session.commit()
        return "analysis_failed"

    report_step = _find_open_step(session, item.id, "report")
    if report_step is None:
        previous_report_step = _latest_step(session, item.id, "report")
        if previous_report_step is not None and previous_report_step.status == "failed":
            # 自动重试复用报告步骤；尝试次数已由领取函数累计，不能创建新步骤重置预算。
            previous_report_step.status = "started"
            previous_report_step.error_code = None
            previous_report_step.detail = "正在重试报告草稿生成"
            previous_report_step.finished_at = None
            report_step = previous_report_step
            session.flush()
    if item.report_id is None:
        analysis_step = _find_open_step(session, item.id, "analysis")
        if analysis_step:
            analysis_step.status = "succeeded"
            analysis_step.finished_at = datetime.now(UTC)
        item.stage = "report"
        if report_step is None:
            _add_step(
                session,
                automation=item,
                kind="report",
                status="started",
                request_id=str(item.id),
                detail="分析完整成功，正在生成报告草稿",
            )
            # 报告步骤在一次领取中有界完成；失败的重试由下方统一处理并累计次数。
            release_lease(session, item, reset_attempts=True)
            session.commit()
            return "report_started"

    if item.report_id is not None:
        _close_open_step(session, item, kind="report", status="succeeded")
        finish(session, item, status="completed", detail="报告草稿已生成")
        session.commit()
        return "completed"

    context = _worker_context(team_id, owner_id)
    try:
        _publish_report(session, item, context)
    except knowledge_products.KnowledgeProductError as exc:
        _close_open_step(
            session,
            item,
            kind="report",
            status="failed",
            error_code="AUTOMATION_REPORT_FAILED",
            detail=exc.detail,
        )
        if item.attempts < item.max_attempts:
            item.stage = "report"
            item.error_code = "AUTOMATION_REPORT_FAILED"
            item.error_detail = exc.detail[:500]
            release_lease(session, item, reset_attempts=False)
        else:
            finish(
                session,
                item,
                status="failed",
                error_code="AUTOMATION_EXHAUSTED",
                detail=f"报告步骤重试次数已用完：{exc.detail}",
            )
        session.commit()
        return "report_retry" if item.status == "running" else "report_failed"
    # 报告模型调用期间可能收到取消或失去租约；发布前按数据库最新状态复核。
    if not lease_held(session, item.id, lease_token):
        raise AutomationLeaseLost
    finish(
        session,
        item,
        status="completed",
        detail="分析与报告草稿均已生成",
        reset_attempts=True,
    )
    session.commit()
    return "completed"
