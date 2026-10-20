"""V2 分析服务层：创建、来源快照、合并发布与失效判定。

职责边界：

- 本模块拥有事务：路由只做协议转换，worker 只做调度与阶段编排。
- 权限、租约、来源快照与发布校验都在这里，任何入口都不能绕过。
- 模型调用不在这里；见 `app.analysis_model`（T03 起接真实供应商）。

来源有效性是 V2 的保守规则：任一所选来源被删除或发布文本变化，整项分析失效，
字段值、摘录与结论一律不再返回。失效状态一旦成立不会因来源恢复而自动复原。
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.analysis_contract import (
    AnalysisIdempotencyConflict,
    AnalysisInvalidInput,
    AnalysisLimitExceeded,
    AnalysisLimits,
    AnalysisSourceUnavailable,
    FieldSpec,
    ValidatedCandidate,
    dedupe_candidates,
    validate_create_request,
)
from app.auth import TeamContext
from app.config import get_settings
from app.db import apply_context
from app.models import (
    AnalysisCitation,
    AnalysisDocument,
    AnalysisField,
    AnalysisFieldResult,
    AnalysisSummary,
    AnalysisTask,
    Document,
    DocumentChunk,
    KnowledgeBase,
    TeamMembership,
)

settings = get_settings()

ACTIVE_TASK_STATUSES = ("queued", "running")
TERMINAL_TASK_STATUSES = ("completed", "partial", "failed", "invalidated")

KIND_LABELS = {"extraction": "信息提取", "comparison": "文档对比"}


def limits() -> AnalysisLimits:
    return AnalysisLimits.from_settings(settings)


class AnalysisConcurrencyLimit(AnalysisLimitExceeded):
    """同时活跃的分析任务达到上限；用户需等待或结束已有任务。"""


# ---------------------------------------------------------------------------
# 来源快照
# ---------------------------------------------------------------------------


def _chunk_rows(session: Session, document_id: uuid.UUID) -> list[tuple[uuid.UUID, str, str]]:
    rows = session.execute(
        select(DocumentChunk.id, DocumentChunk.locator_label, DocumentChunk.content)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.ordinal)
    ).all()
    return [(row.id, row.locator_label, row.content) for row in rows]


def source_fingerprint(chunks: list[tuple[uuid.UUID, str, str]]) -> str:
    """按有序片段的身份、定位与内容计算指纹。

    片段身份一并纳入，所以重索引即使正文不变也会失效——依据无法证明仍对应本次
    快照时按保守规则处理，不把旧依据自动挂到新片段上。
    """
    digest = hashlib.sha256()
    for chunk_id, locator_label, content in chunks:
        digest.update(str(chunk_id).encode())
        digest.update(b"\x1e")
        digest.update(locator_label.encode())
        digest.update(b"\x1e")
        digest.update(content.encode())
        digest.update(b"\x1f")
    return digest.hexdigest()


def published_text_chars(chunks: list[tuple[uuid.UUID, str, str]]) -> int:
    return sum(len(content) for _, _, content in chunks)


def request_fingerprint(
    *,
    kind: str,
    knowledge_base_id: uuid.UUID,
    document_ids: list[uuid.UUID],
    fields: list[dict[str, str]],
    source_task_id: uuid.UUID | None = None,
) -> str:
    """请求指纹包含类型、知识库、文档集合与字段定义。

    文档顺序不影响指纹（集合语义），字段顺序影响（决定信息表列序）。
    """
    payload = json.dumps(
        {
            "kind": kind,
            "knowledge_base_id": str(knowledge_base_id),
            "source_task_id": str(source_task_id) if source_task_id else None,
            "documents": sorted(str(item) for item in document_ids),
            "fields": [
                {
                    "name": field["name"].strip(),
                    "description": field["description"].strip(),
                    "value_type": field["value_type"],
                }
                for field in fields
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------


@dataclass
class CreatedTask:
    task: AnalysisTask
    created: bool


def _knowledge_base(session: Session, team_id: uuid.UUID, kb_id: uuid.UUID) -> KnowledgeBase:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or kb.team_id != team_id:
        raise AnalysisSourceUnavailable("知识库不存在")
    return kb


def _load_sources(
    session: Session, team_id: uuid.UUID, kb_id: uuid.UUID, document_ids: list[uuid.UUID]
) -> list[Document]:
    """读取并校验所选文档：必须属于当前团队与知识库、已就绪且未删除。

    混入其他团队、其他知识库或不可用文档时整次拒绝，不静默忽略。统一返回
    “来源不可用”而不区分不存在与无权限，避免泄漏其他团队资料的存在性。
    """
    rows = session.scalars(
        select(Document).where(Document.id.in_(document_ids), Document.deleted_at.is_(None))
    ).all()
    by_id = {row.id: row for row in rows}
    ordered: list[Document] = []
    for document_id in document_ids:
        document = by_id.get(document_id)
        if (
            document is None
            or document.team_id != team_id
            or document.knowledge_base_id != kb_id
            or document.status != "ready"
        ):
            raise AnalysisSourceUnavailable("所选文档不可用于分析")
        ordered.append(document)
    return ordered


def _count_active_tasks(
    session: Session, team_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[int, int]:
    row = session.execute(
        text(
            "select user_active, team_active "
            "from app_count_active_analysis_tasks(:team_id, :user_id)"
        ),
        {"team_id": team_id, "user_id": user_id},
    ).one()
    return int(row.user_active), int(row.team_active)


def create_task(
    session: Session,
    context: TeamContext,
    *,
    knowledge_base_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    kind: str,
    title: str | None,
    document_ids: list[uuid.UUID],
    fields: list[dict[str, str]],
    source_task_id: uuid.UUID | None,
) -> CreatedTask:
    """创建分析任务；固定输入、来源快照与执行记录在同一事务完成。"""
    bounds = limits()
    _knowledge_base(session, context.team.id, knowledge_base_id)

    from app.schemas import AnalysisCreateRequest, AnalysisFieldInput

    payload = AnalysisCreateRequest(
        kind=kind,
        document_ids=document_ids,
        fields=[AnalysisFieldInput(**field) for field in fields],
        title=title,
        source_task_id=source_task_id,
    )
    validate_create_request(payload, bounds)

    fingerprint = request_fingerprint(
        kind=payload.kind,
        knowledge_base_id=knowledge_base_id,
        document_ids=payload.document_ids,
        fields=[field.model_dump() for field in payload.fields],
        source_task_id=payload.source_task_id,
    )

    # 团队级行锁让活跃任务计数与创建串行化，避免并发提交绕过上限。
    session.execute(text("select app_lock_team(:team_id)"), {"team_id": context.team.id})

    existing = session.scalars(
        select(AnalysisTask).where(
            AnalysisTask.team_id == context.team.id,
            AnalysisTask.created_by_id == context.user.id,
            AnalysisTask.idempotency_key == idempotency_key,
        )
    ).one_or_none()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise AnalysisIdempotencyConflict("幂等键已被不同请求使用")
        return CreatedTask(task=existing, created=False)

    user_active, team_active = _count_active_tasks(session, context.team.id, context.user.id)
    if user_active >= bounds.user_max_active_tasks:
        raise AnalysisConcurrencyLimit(
            f"同时进行的分析任务已达上限 {bounds.user_max_active_tasks} 个，请等待或结束已有任务"
        )
    if team_active >= bounds.team_max_active_tasks:
        raise AnalysisConcurrencyLimit(
            f"团队同时进行的分析任务已达上限 {bounds.team_max_active_tasks} 个"
        )

    documents = _load_sources(session, context.team.id, knowledge_base_id, payload.document_ids)
    if payload.source_task_id is not None:
        source_task = session.get(AnalysisTask, payload.source_task_id)
        if (
            source_task is None
            or source_task.team_id != context.team.id
            or source_task.knowledge_base_id != knowledge_base_id
            or source_task.created_by_id != context.user.id
        ):
            raise AnalysisInvalidInput("来源分析任务不存在或不属于当前范围")

    snapshots = [_chunk_rows(session, document.id) for document in documents]
    from app.analysis_contract import validate_text_budget

    validate_text_budget([published_text_chars(chunks) for chunks in snapshots], bounds)

    resolved_title = (title or "").strip() or (
        f"{KIND_LABELS[payload.kind]} · {len(documents)} 份文档"
    )

    task = AnalysisTask(
        id=uuid.uuid4(),
        team_id=context.team.id,
        knowledge_base_id=knowledge_base_id,
        created_by_id=context.user.id,
        kind=payload.kind,
        title=resolved_title[:120],
        status="queued",
        stage="queued",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        source_task_id=source_task_id,
        document_count=len(documents),
    )
    session.add(task)
    session.flush()

    for ordinal, field in enumerate(payload.fields):
        session.add(
            AnalysisField(
                id=uuid.uuid4(),
                team_id=context.team.id,
                knowledge_base_id=knowledge_base_id,
                task_id=task.id,
                owner_id=context.user.id,
                ordinal=ordinal,
                name=field.name.strip(),
                description=field.description.strip(),
                value_type=field.value_type,
            )
        )

    for ordinal, (document, chunks) in enumerate(zip(documents, snapshots, strict=True)):
        session.add(
            AnalysisDocument(
                id=uuid.uuid4(),
                team_id=context.team.id,
                knowledge_base_id=knowledge_base_id,
                task_id=task.id,
                owner_id=context.user.id,
                document_id=document.id,
                ordinal=ordinal,
                status="queued",
                attempts=0,
                max_attempts=bounds.max_attempts,
                source_fingerprint=source_fingerprint(chunks),
                expected_chunk_count=len(chunks),
            )
        )
    session.flush()
    return CreatedTask(task=task, created=True)


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def encode_cursor(task: AnalysisTask) -> str:
    raw = f"{task.created_at.isoformat()}|{task.id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at_text, task_id_text = raw.split("|", 1)
        return datetime.fromisoformat(created_at_text), uuid.UUID(task_id_text)
    except (ValueError, TypeError) as exc:
        raise AnalysisInvalidInput("分页游标无效") from exc


def list_tasks(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    *,
    cursor: str | None,
    limit: int,
) -> tuple[list[AnalysisTask], str | None]:
    _knowledge_base(session, context.team.id, knowledge_base_id)
    stmt = select(AnalysisTask).where(
        AnalysisTask.team_id == context.team.id,
        AnalysisTask.knowledge_base_id == knowledge_base_id,
        AnalysisTask.created_by_id == context.user.id,
    )
    if cursor:
        created_at, task_id = decode_cursor(cursor)
        stmt = stmt.where(
            (AnalysisTask.created_at < created_at)
            | ((AnalysisTask.created_at == created_at) & (AnalysisTask.id < task_id))
        )
    stmt = stmt.order_by(AnalysisTask.created_at.desc(), AnalysisTask.id.desc()).limit(limit + 1)
    rows = list(session.scalars(stmt))
    next_cursor = None
    if len(rows) > limit:
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1])
    return rows, next_cursor


def get_owned_task(session: Session, context: TeamContext, task_id: uuid.UUID) -> AnalysisTask:
    """本人任务查询；同团队其他成员与管理员一律得到 404。"""
    task = session.get(AnalysisTask, task_id)
    if task is None or task.team_id != context.team.id or task.created_by_id != context.user.id:
        raise AnalysisSourceUnavailable("分析任务不存在")
    return task


def source_invalid_reason(session: Session, executions: list[AnalysisDocument]) -> str | None:
    """实时校验全部来源；任一失效即返回原因，用于隐藏结果。"""
    for execution in executions:
        document = session.get(Document, execution.document_id)
        if document is None or document.deleted_at is not None or document.status == "deleted":
            return "所选文档已被删除"
        if document.status != "ready":
            return "所选文档当前不可用"
        if execution.source_fingerprint is None:
            return "缺少来源快照"
        if source_fingerprint(_chunk_rows(session, document.id)) != execution.source_fingerprint:
            return "所选文档的发布文本已变化"
    return None


def invalidate_task(session: Session, task: AnalysisTask, reason: str) -> None:
    """把任务收敛为失效；结果保留在库中但不再展示。"""
    if task.status == "invalidated":
        return
    task.status = "invalidated"
    task.stage = "finished"
    task.error_code = "ANALYSIS_SOURCE_INVALIDATED"
    task.error_detail = reason[:500]
    task.finished_at = task.finished_at or datetime.now(UTC)
    session.flush()


def mark_permission_revoked(session: Session, task: AnalysisTask) -> None:
    task.status = "failed"
    task.stage = "finished"
    task.error_code = "ANALYSIS_PERMISSION_REVOKED"
    task.error_detail = "创建者已不是该团队成员，分析停止"
    task.finished_at = task.finished_at or datetime.now(UTC)
    session.flush()


def is_member(session: Session, team_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (
        session.scalars(
            select(TeamMembership.id).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user_id,
            )
        ).one_or_none()
        is not None
    )


def field_specs(session: Session, task_id: uuid.UUID) -> dict[uuid.UUID, FieldSpec]:
    rows = session.scalars(
        select(AnalysisField)
        .where(AnalysisField.task_id == task_id)
        .order_by(AnalysisField.ordinal)
    ).all()
    return {row.id: FieldSpec(id=row.id, name=row.name, value_type=row.value_type) for row in rows}


def ordered_fields(session: Session, task_id: uuid.UUID) -> list[AnalysisField]:
    return list(
        session.scalars(
            select(AnalysisField)
            .where(AnalysisField.task_id == task_id)
            .order_by(AnalysisField.ordinal)
        ).all()
    )


# ---------------------------------------------------------------------------
# 合并与发布
# ---------------------------------------------------------------------------


def merge_field_candidates(
    candidates: list[ValidatedCandidate], spec: FieldSpec, bounds: AnalysisLimits
) -> tuple[str, dict[str, object], str | None]:
    """把同一字段的候选合并为一条结果。

    - 没有候选：只有在完整处理全部片段后才允许判为未提及。
    - 一个取值：已提取；候选带歧义标记时转为待核实。
    - 多个不同取值：保留一个取值并标记待核实，依据全部保留，不替用户选一个。
    """
    deduped = dedupe_candidates(candidates)
    if not deduped:
        return "not_mentioned", {}, None
    if any(item.status == "needs_review" for item in deduped):
        first = deduped[0]
        return (
            "needs_review",
            _value_payload(first),
            first.note or "原文表述存在歧义，需要人工核对",
        )

    distinct: dict[tuple[str, str], ValidatedCandidate] = {}
    for item in deduped:
        distinct.setdefault(item.value_key, item)

    if len(distinct) == 1:
        return "extracted", _value_payload(deduped[0]), None

    if spec.value_type == "text_list":
        merged: list[str] = []
        for item in deduped:
            for entry in item.value.value_list or []:
                if entry not in merged:
                    merged.append(entry)
        if len(merged) > bounds.max_list_items:
            raise AnalysisLimitExceeded(
                f"字段“{spec.name}”合并后的列表超过 {bounds.max_list_items} 项上限"
            )
        payload = _value_payload(deduped[0])
        payload["value_list"] = merged
        return (
            "extracted",
            payload,
            None,
        )

    first = deduped[0]
    return (
        "needs_review",
        _value_payload(first),
        f"同一字段出现 {len(distinct)} 个不同取值，需要人工核对",
    )


def _value_payload(candidate: ValidatedCandidate) -> dict[str, object]:
    typed = candidate.value
    return {
        "value_text": typed.value_text,
        "value_number": typed.value_number,
        "value_date": typed.value_date,
        "value_list": typed.value_list,
        "unit": typed.unit,
        "date_precision": typed.date_precision,
        "raw_value": candidate.raw_value,
    }


class LeaseLost(Exception):
    """分析执行租约已失效：其他执行者已接管，本次结果不得发布。"""


def publish_document(
    session: Session,
    *,
    task_id: uuid.UUID,
    execution_id: uuid.UUID,
    lease_token: uuid.UUID,
    candidates: list[ValidatedCandidate],
    model_calls: int,
    processed_chunk_count: int,
    provider: str = "unknown",
    model: str = "unknown",
    prompt_version: str | None = None,
    schema_version: str | None = None,
) -> str:
    """发布单份文档的完整字段结果与依据，并收敛父任务状态。

    加锁顺序固定为“来源文档 → 分析任务 → 执行记录”，与删除路径保持一致，
    避免与删除并发时互相等待或发布已被删除来源的结果。
    """
    bounds = limits()
    execution = session.get(AnalysisDocument, execution_id)
    if execution is None:
        raise LeaseLost
    task = session.get(AnalysisTask, task_id)
    if task is None:
        raise LeaseLost

    locked_source = session.execute(
        text("select id from documents where id = :document_id for update"),
        {"document_id": execution.document_id},
    ).one_or_none()
    locked_task = session.execute(
        text("select id from analysis_tasks where id = :task_id for update"),
        {"task_id": task_id},
    ).one_or_none()
    locked_execution = session.execute(
        text(
            """
            select id from analysis_documents
            where id = :execution_id and status = 'running'
              and lease_token = :lease_token and lease_expires_at > now()
            for update
            """
        ),
        {"execution_id": execution_id, "lease_token": lease_token},
    ).one_or_none()
    if locked_source is None or locked_task is None or locked_execution is None:
        raise LeaseLost

    session.refresh(task)
    session.refresh(execution)

    if not is_member(session, task.team_id, task.created_by_id):
        mark_permission_revoked(session, task)
        execution.status = "failed"
        execution.error_code = "ANALYSIS_PERMISSION_REVOKED"
        execution.error_detail = "创建者已不是该团队成员"
        execution.lease_token = None
        execution.lease_expires_at = None
        execution.finished_at = datetime.now(UTC)
        session.flush()
        return "failed"

    reason = source_invalid_reason(session, [execution])
    if reason is not None:
        invalidate_task(session, task, reason)
        execution.status = "failed"
        execution.error_code = "ANALYSIS_SOURCE_INVALIDATED"
        execution.error_detail = reason[:500]
        execution.lease_token = None
        execution.lease_expires_at = None
        execution.finished_at = datetime.now(UTC)
        session.flush()
        return "invalidated"

    if task.status not in ACTIVE_TASK_STATUSES:
        raise LeaseLost

    specs = field_specs(session, task_id)

    # 重复发布必须幂等：先清空该执行的旧结果与依据再写入。
    session.execute(
        delete(AnalysisFieldResult).where(AnalysisFieldResult.document_execution_id == execution_id)
    )
    session.flush()

    by_field: dict[uuid.UUID, list[ValidatedCandidate]] = {}
    for candidate in candidates:
        by_field.setdefault(candidate.field_id, []).append(candidate)

    for field_id, spec in specs.items():
        status, payload, note = merge_field_candidates(by_field.get(field_id, []), spec, bounds)
        result = AnalysisFieldResult(
            id=uuid.uuid4(),
            team_id=task.team_id,
            knowledge_base_id=task.knowledge_base_id,
            task_id=task.id,
            owner_id=task.created_by_id,
            document_execution_id=execution_id,
            field_id=field_id,
            status=status,
            note=(note or "")[:1000] or None,
            **payload,
        )
        session.add(result)
        session.flush()
        for ordinal, candidate in enumerate(
            dedupe_candidates(by_field.get(field_id, []))[: bounds.max_citations_per_field]
        ):
            session.add(
                AnalysisCitation(
                    id=uuid.uuid4(),
                    team_id=task.team_id,
                    knowledge_base_id=task.knowledge_base_id,
                    task_id=task.id,
                    owner_id=task.created_by_id,
                    document_execution_id=execution_id,
                    field_result_id=result.id,
                    conclusion_id=None,
                    document_id=execution.document_id,
                    chunk_id=candidate.chunk_id,
                    ordinal=ordinal,
                    locator_label=_locator_label(session, candidate.chunk_id),
                    excerpt=candidate.excerpt,
                    source_fingerprint=execution.source_fingerprint or "",
                )
            )

    execution.status = "succeeded"
    execution.model_calls = model_calls
    execution.processed_chunk_count = processed_chunk_count
    execution.provider = provider
    execution.model = model
    execution.prompt_version = prompt_version
    execution.schema_version = schema_version
    execution.error_code = None
    execution.error_detail = None
    execution.lease_token = None
    execution.lease_expires_at = None
    execution.finished_at = datetime.now(UTC)
    session.flush()

    converge_task(session, task)
    return "succeeded"


def _locator_label(session: Session, chunk_id: uuid.UUID) -> str:
    label = session.scalar(select(DocumentChunk.locator_label).where(DocumentChunk.id == chunk_id))
    return (label or "已失效位置")[:200]


def converge_task(session: Session, task: AnalysisTask) -> None:
    """按逐文档终态收敛父任务；不依赖 worker 主动上报。"""
    bounds = limits()
    rows = session.execute(
        select(AnalysisDocument.status, func.count())
        .where(AnalysisDocument.task_id == task.id)
        .group_by(AnalysisDocument.status)
    ).all()
    counts = {status: int(count) for status, count in rows}
    succeeded = counts.get("succeeded", 0)
    failed = counts.get("failed", 0)
    pending = counts.get("queued", 0) + counts.get("running", 0)

    task.completed_document_count = succeeded
    task.failed_document_count = failed

    if pending > 0:
        task.status = "running"
        task.stage = "extracting"
        session.flush()
        return

    if task.kind == "comparison":
        if succeeded == 0:
            task.status = "failed"
            task.stage = "finished"
        elif failed > 0:
            task.status = "partial"
            task.stage = "finished"
        else:
            task.status = "running"
            task.stage = "comparing"
            if (
                session.scalars(
                    select(AnalysisSummary).where(AnalysisSummary.task_id == task.id)
                ).one_or_none()
                is None
            ):
                # 复用父任务的稳定执行行承载所有提取结果的完整范围；汇总依据
                # 通过该行与来源摘要建立一致的任务/创建者约束。
                source_execution = session.scalars(
                    select(AnalysisDocument)
                    .where(
                        AnalysisDocument.task_id == task.id,
                        AnalysisDocument.status == "succeeded",
                    )
                    .order_by(AnalysisDocument.ordinal)
                ).first()
                if source_execution is not None:
                    session.add(
                        AnalysisSummary(
                            id=uuid.uuid4(),
                            team_id=task.team_id,
                            knowledge_base_id=task.knowledge_base_id,
                            task_id=task.id,
                            owner_id=task.created_by_id,
                            document_execution_id=source_execution.id,
                            status="queued",
                            attempts=0,
                            max_attempts=bounds.max_attempts,
                        )
                    )
        if task.status in ("failed", "partial"):
            task.finished_at = task.finished_at or datetime.now(UTC)
        elif task.status == "running":
            task.finished_at = None
        session.flush()
        return

    if succeeded == 0:
        task.status = "failed"
    elif failed > 0:
        task.status = "partial"
    else:
        task.status = "completed"
    task.stage = "finished"
    task.finished_at = task.finished_at or datetime.now(UTC)
    session.flush()


def reconcile_task(session: Session, task_id: uuid.UUID) -> None:
    """对账时收敛一个仍存在的父任务；回收路径与正常发布共用同一状态机。"""
    task = session.get(AnalysisTask, task_id)
    if task is not None and task.status in ACTIVE_TASK_STATUSES:
        converge_task(session, task)


def fail_execution(
    session: Session,
    *,
    execution_id: uuid.UUID,
    error_code: str,
    detail: str,
    retryable: bool,
) -> None:
    """标记一次执行失败；可重试时回到队列，否则收敛任务。"""
    execution = session.get(AnalysisDocument, execution_id)
    if execution is None:
        return
    task = session.get(AnalysisTask, execution.task_id)
    if task is None:
        return
    execution.error_code = error_code
    execution.error_detail = detail[:500]
    execution.lease_token = None
    execution.lease_expires_at = None
    if retryable and execution.attempts < execution.max_attempts:
        execution.status = "queued"
    else:
        execution.status = "failed"
        execution.finished_at = datetime.now(UTC)
    session.flush()
    converge_task(session, task)


def fail_summary(
    session: Session,
    *,
    task_id: uuid.UUID,
    error_code: str,
    detail: str,
) -> None:
    """汇总失败保留成功的逐文档提取表，父任务明确收敛为 partial。"""
    task = session.get(AnalysisTask, task_id)
    if task is None:
        return
    summary = session.scalars(
        select(AnalysisSummary).where(AnalysisSummary.task_id == task_id)
    ).one_or_none()
    if summary is not None:
        summary.status = "failed"
        summary.lease_token = None
        summary.lease_expires_at = None
        summary.error_code = error_code
        summary.error_detail = detail[:500]
        summary.finished_at = datetime.now(UTC)
    task.status = "partial"
    task.stage = "finished"
    task.error_code = error_code
    task.error_detail = detail[:500]
    task.finished_at = task.finished_at or datetime.now(UTC)
    session.flush()


def mark_summary_exhausted(session: Session, summary_id: uuid.UUID) -> None:
    summary = session.get(AnalysisSummary, summary_id)
    if summary is None:
        return
    fail_summary(
        session,
        task_id=summary.task_id,
        error_code=summary.error_code or "ANALYSIS_EXHAUSTED",
        detail=summary.error_detail or "汇总多次失败，已停止自动重试",
    )


def mark_exhausted(session: Session, execution_id: uuid.UUID) -> None:
    """回收时发现尝试次数耗尽：把执行与父任务一起收敛为失败。"""
    execution = session.get(AnalysisDocument, execution_id)
    if execution is None:
        return
    task = session.get(AnalysisTask, execution.task_id)
    if task is None:
        return
    execution.status = "failed"
    execution.error_code = execution.error_code or "ANALYSIS_EXHAUSTED"
    execution.error_detail = execution.error_detail or "处理多次失败，已停止自动重试"
    execution.finished_at = execution.finished_at or datetime.now(UTC)
    session.flush()
    converge_task(session, task)


def ensure_context(session: Session, *, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """每次提交后 SET LOCAL 都会失效，新事务必须重新绑定上下文。"""
    apply_context(session, team_id=team_id, user_id=user_id)
