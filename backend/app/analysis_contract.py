"""V2 分析的协议与校验层。

这里只放纯函数：请求语义校验、模型输出解析、依据重新定位与类型化取值。它不接触
数据库、对象存储或模型客户端，因此可以在无模型、无业务库的测试里直接验证。
事务、权限、租约与发布属于 `app.analysis`（T02 起）。

关键约定：

- 模型只能返回字段 ID、片段 ID、类型化取值、原文表达与歧义标记；文件名、页码与
  摘录位置一律由服务端根据片段原文重新计算，模型自报的位置不作为依据。
- 日期保留原文精度：`date_precision` 由取值字符串推导，`value_date` 是区间首日，
  展示层必须按精度渲染，不能把“2026 年”显示成“2026-01-01”。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import Settings
from app.schemas import AnalysisCreateRequest


class AnalysisError(Exception):
    """分析流程的可分类错误；路由按 `code` 决定 HTTP 状态与前端文案。"""

    default_code = "ANALYSIS_INVALID_INPUT"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code: str = code or type(self).default_code


class AnalysisInvalidInput(AnalysisError):
    default_code = "ANALYSIS_INVALID_INPUT"


class AnalysisSourceUnavailable(AnalysisError):
    default_code = "ANALYSIS_SOURCE_UNAVAILABLE"


class AnalysisIdempotencyConflict(AnalysisError):
    default_code = "ANALYSIS_IDEMPOTENCY_CONFLICT"


class AnalysisLimitExceeded(AnalysisError):
    default_code = "ANALYSIS_LIMIT_EXCEEDED"


class AnalysisModelOutputInvalid(AnalysisError):
    default_code = "ANALYSIS_MODEL_OUTPUT_INVALID"


class AnalysisModelUnavailable(AnalysisError):
    default_code = "ANALYSIS_MODEL_UNAVAILABLE"


class AnalysisBudgetExhausted(AnalysisError):
    default_code = "ANALYSIS_BUDGET_EXHAUSTED"


class AnalysisPermissionRevoked(AnalysisError):
    default_code = "ANALYSIS_PERMISSION_REVOKED"


@dataclass(frozen=True)
class AnalysisLimits:
    """服务端有界配置；创建时校验请求，执行时校验预算。"""

    max_documents: int
    max_fields: int
    max_document_chars: int
    max_total_chars: int
    batch_chars: int
    max_calls_per_document: int
    max_output_chars: int
    max_value_chars: int
    max_list_items: int
    max_citations_per_field: int
    timeout_seconds: int
    max_attempts: int
    user_max_active_tasks: int
    team_max_active_tasks: int
    lease_seconds: int

    @classmethod
    def from_settings(cls, settings: Settings) -> AnalysisLimits:
        return cls(
            max_documents=settings.analysis_max_documents,
            max_fields=settings.analysis_max_fields,
            max_document_chars=settings.analysis_max_document_chars,
            max_total_chars=settings.analysis_max_total_chars,
            batch_chars=settings.analysis_batch_chars,
            max_calls_per_document=settings.analysis_max_calls_per_document,
            max_output_chars=settings.analysis_max_output_chars,
            max_value_chars=settings.analysis_max_value_chars,
            max_list_items=settings.analysis_max_list_items,
            max_citations_per_field=settings.analysis_max_citations_per_field,
            timeout_seconds=settings.analysis_timeout_seconds,
            max_attempts=settings.analysis_max_attempts,
            user_max_active_tasks=settings.analysis_user_max_active_tasks,
            team_max_active_tasks=settings.analysis_team_max_active_tasks,
            lease_seconds=settings.analysis_lease_seconds,
        )


@dataclass(frozen=True)
class FieldSpec:
    """一个固定字段/维度；关联一律使用 `id`，名称只用于展示。"""

    id: uuid.UUID
    name: str
    value_type: str


@dataclass(frozen=True)
class TypedValue:
    """通过类型校验的取值；同一时刻只有一个分支非空。"""

    value_text: str | None = None
    value_number: float | None = None
    value_date: date | None = None
    value_list: list[str] | None = None
    unit: str | None = None
    date_precision: str | None = None


@dataclass(frozen=True)
class ValidatedCandidate:
    """通过服务端校验的候选值；摘录与位置来自片段原文，不来自模型自报。"""

    field_id: uuid.UUID
    status: str
    chunk_id: uuid.UUID
    excerpt: str
    locator_start: int
    locator_end: int
    raw_value: str
    value: TypedValue
    note: str | None = None

    @property
    def value_key(self) -> tuple[str, str]:
        """用于去重与合并的取值指纹：类型 + 规范化取值。"""
        typed = self.value
        if typed.value_number is not None:
            return ("number", format(typed.value_number, ".10g"))
        if typed.value_date is not None:
            return ("date", f"{typed.date_precision}:{typed.value_date.isoformat()}")
        if typed.value_list is not None:
            return ("text_list", "\u0000".join(typed.value_list))
        return ("text", (typed.value_text or "").strip())


# ---------------------------------------------------------------------------
# 请求语义校验
# ---------------------------------------------------------------------------


def _normalized_name(value: str) -> str:
    return re.sub(r"\s+", "", value)


def validate_create_request(payload: AnalysisCreateRequest, limits: AnalysisLimits) -> None:
    """创建请求的语义校验；结构形状由 Pydantic 保证，这里只判业务边界。"""
    document_count = len(payload.document_ids)
    if len(set(payload.document_ids)) != document_count:
        raise AnalysisInvalidInput("文档清单中存在重复文档")
    if document_count > limits.max_documents:
        raise AnalysisLimitExceeded(
            f"一次最多分析 {limits.max_documents} 份文档，本次选择 {document_count} 份"
        )
    if payload.kind == "comparison" and document_count < 2:
        raise AnalysisInvalidInput("文档对比至少需要两份文档")

    if len(payload.fields) > limits.max_fields:
        raise AnalysisLimitExceeded(
            f"一次最多定义 {limits.max_fields} 个字段，本次定义 {len(payload.fields)} 个"
        )

    seen: set[str] = set()
    for field in payload.fields:
        name = _normalized_name(field.name)
        description = _normalized_name(field.description)
        if not name:
            raise AnalysisInvalidInput("字段名称不能为空白")
        if not description:
            raise AnalysisInvalidInput(f"字段“{field.name}”缺少说明")
        if name in seen:
            raise AnalysisInvalidInput(f"字段名称重复：{field.name}")
        seen.add(name)


def validate_text_budget(char_counts: list[int], limits: AnalysisLimits) -> None:
    """按已发布文本长度校验预算；超限必须整次拒绝，不能截断后继续。"""
    for count in char_counts:
        if count <= 0:
            raise AnalysisSourceUnavailable("所选文档没有可分析的已发布文本")
        if count > limits.max_document_chars:
            raise AnalysisLimitExceeded(f"单份文档文本超过 {limits.max_document_chars} 字符限制")
    total = sum(char_counts)
    if total > limits.max_total_chars:
        raise AnalysisLimitExceeded(f"所选文档合计文本超过 {limits.max_total_chars} 字符限制")


def validate_batch_budget(batch_chars: int, limits: AnalysisLimits) -> None:
    if batch_chars <= 0:
        raise AnalysisInvalidInput("批次不能为空")
    if batch_chars > limits.batch_chars:
        raise AnalysisLimitExceeded("批次超过单次模型调用输入上限")


def validate_model_call_count(used: int, limits: AnalysisLimits) -> None:
    if used >= limits.max_calls_per_document:
        raise AnalysisBudgetExhausted(
            f"该文档的模型调用已达上限 {limits.max_calls_per_document} 次"
        )


# ---------------------------------------------------------------------------
# 模型输出解析与校验
# ---------------------------------------------------------------------------


class ModelCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: uuid.UUID
    quote: str = Field(min_length=1)
    raw_value: str = Field(min_length=1)
    value_text: str | None = None
    value_number: float | None = None
    value_date: str | None = None
    value_list: list[str] | None = None
    unit: str | None = None
    ambiguous: bool = False
    note: str | None = None


class ModelFieldResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: uuid.UUID
    candidates: list[ModelCandidate] = Field(default_factory=list)


class ModelBatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[ModelFieldResult]


class ModelConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: uuid.UUID
    kind: str
    text: str = Field(min_length=1, max_length=2_000)
    document_ids: list[uuid.UUID]
    citation_refs: list[tuple[uuid.UUID, uuid.UUID]] = Field(default_factory=list)


class ModelComparisonOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conclusions: list[ModelConclusion]


@dataclass(frozen=True)
class ValidatedConclusion:
    field_id: uuid.UUID
    kind: str
    text: str
    document_ids: list[uuid.UUID]
    citation_ids: list[uuid.UUID]


def parse_json_object(text: str, limits: AnalysisLimits) -> dict[str, Any]:
    """从模型回执里取出 JSON 对象；只接受对象，容忍常见的代码围栏。"""
    if len(text) > limits.max_output_chars:
        raise AnalysisLimitExceeded(f"模型输出超过 {limits.max_output_chars} 字符限制")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
        if cleaned[:4].lower() == "json":
            cleaned = cleaned[4:]
        cleaned = cleaned.rstrip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise AnalysisModelOutputInvalid("模型输出不是 JSON 对象")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AnalysisModelOutputInvalid(f"模型输出 JSON 解析失败：{exc.msg}") from exc
    if not isinstance(payload, dict):
        raise AnalysisModelOutputInvalid("模型输出必须是 JSON 对象")
    return payload


def strip_whitespace_with_map(text: str) -> tuple[str, list[int]]:
    """去掉全部空白，并记录每个保留字符在原文中的偏移。

    模型复述摘录时常会重排换行或空格，空白差异不应导致误判；去掉空白后仍要求
    字符序列逐字一致，既不接受改写过的文字，也容得下排版差异。
    """
    kept: list[str] = []
    mapping: list[int] = []
    for index, char in enumerate(text):
        if char.isspace():
            continue
        kept.append(char)
        mapping.append(index)
    return "".join(kept), mapping


def locate_excerpt(content: str, quote: str) -> tuple[int, int] | None:
    """在片段原文中重新定位摘录，返回原文偏移区间；返回的区间始终取自原文。"""
    stripped, mapping = strip_whitespace_with_map(content)
    target = "".join(quote.split())
    if not target or not mapping:
        return None
    position = stripped.find(target)
    if position < 0:
        return None
    start = mapping[position]
    end = mapping[position + len(target) - 1] + 1
    return start, end


_DATE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), "day"),
    (re.compile(r"^(\d{4})-(\d{2})$"), "month"),
    (re.compile(r"^(\d{4})$"), "year"),
)


def parse_date_value(raw: str) -> tuple[date, str]:
    """解析 ISO 风格日期并推导精度；不接受缺位、越界或自由格式。"""
    for pattern, precision in _DATE_PATTERNS:
        match = pattern.match(raw)
        if match is None:
            continue
        groups = [int(value) for value in match.groups()]
        year = groups[0]
        month = groups[1] if len(groups) > 1 else 1
        day = groups[2] if len(groups) > 2 else 1
        try:
            return date(year, month, day), precision
        except ValueError as exc:
            raise AnalysisModelOutputInvalid(f"日期取值越界：{raw}") from exc
    raise AnalysisModelOutputInvalid(f"日期格式不被接受：{raw}")


def _typed_value(candidate: ModelCandidate, spec: FieldSpec, limits: AnalysisLimits) -> TypedValue:
    """按字段声明类型校验取值；同时提供多个类型或都不提供即非法。"""
    provided = [
        name
        for name, value in (
            ("text", candidate.value_text),
            ("number", candidate.value_number),
            ("date", candidate.value_date),
            ("list", candidate.value_list),
        )
        if value is not None
    ]
    if not provided:
        raise AnalysisModelOutputInvalid("候选缺少取值")
    if len(provided) > 1:
        raise AnalysisModelOutputInvalid("候选同时提供了多种类型的取值")
    kind = provided[0]
    expected = {
        "text": "text",
        "number": "number",
        "date": "date",
        "list": "text_list",
    }[kind]
    if spec.value_type != expected:
        raise AnalysisModelOutputInvalid(
            f"字段“{spec.name}”声明为 {spec.value_type}，模型返回 {expected}"
        )

    if kind == "text":
        text_value = (candidate.value_text or "").strip()
        if not text_value:
            raise AnalysisModelOutputInvalid("文本取值不能为空白")
        if len(text_value) > limits.max_value_chars:
            raise AnalysisLimitExceeded("文本取值超过长度上限")
        return TypedValue(value_text=text_value)
    if kind == "number":
        number = candidate.value_number
        assert number is not None
        if number != number or number in (float("inf"), float("-inf")):
            raise AnalysisModelOutputInvalid("数值取值必须是有限数")
        if candidate.unit is not None and len(candidate.unit) > 40:
            raise AnalysisLimitExceeded("单位超过长度上限")
        return TypedValue(value_number=number, unit=candidate.unit)
    if kind == "date":
        raw_date = candidate.value_date
        assert raw_date is not None
        parsed, precision = parse_date_value(raw_date)
        # 精度由取值字符串推导，不采信模型自报的精度声明。
        return TypedValue(value_date=parsed, date_precision=precision)

    items = candidate.value_list or []
    if not items:
        raise AnalysisModelOutputInvalid("文本列表不能为空")
    if len(items) > limits.max_list_items:
        raise AnalysisLimitExceeded(f"文本列表超过 {limits.max_list_items} 项上限")
    cleaned_items: list[str] = []
    for item in items:
        stripped = item.strip()
        if not stripped:
            raise AnalysisModelOutputInvalid("文本列表存在空白项")
        if len(stripped) > limits.max_value_chars:
            raise AnalysisLimitExceeded("文本列表项超过长度上限")
        if stripped not in cleaned_items:
            cleaned_items.append(stripped)
    return TypedValue(value_list=cleaned_items)


def parse_batch_output(
    raw: str,
    *,
    specs: dict[uuid.UUID, FieldSpec],
    chunk_contents: dict[uuid.UUID, str],
    limits: AnalysisLimits,
) -> list[ValidatedCandidate]:
    """校验一批模型输出，返回可信候选。

    拒绝条件：未知字段 ID、非本批片段 ID、摘录无法在片段原文中定位、取值类型与字段
    声明不符、超出长度或数量上限。任一候选非法即整批失败，不做部分接受。
    """
    payload = parse_json_object(raw, limits)
    try:
        batch = ModelBatchOutput.model_validate(payload)
    except ValidationError as exc:
        raise AnalysisModelOutputInvalid(f"模型输出结构非法：{exc.error_count()} 处") from exc

    validated: list[ValidatedCandidate] = []
    for field_result in batch.fields:
        spec = specs.get(field_result.field_id)
        if spec is None:
            raise AnalysisModelOutputInvalid("模型返回了本次任务未定义的字段")
        if len(field_result.candidates) > limits.max_citations_per_field:
            raise AnalysisLimitExceeded(
                f"字段“{spec.name}”的候选超过 {limits.max_citations_per_field} 条上限"
            )
        for candidate in field_result.candidates:
            content = chunk_contents.get(candidate.chunk_id)
            if content is None:
                raise AnalysisModelOutputInvalid("模型引用了本次批次之外的片段")
            span = locate_excerpt(content, candidate.quote)
            if span is None:
                raise AnalysisModelOutputInvalid("模型摘录与片段原文不一致")
            start, end = span
            excerpt = content[start:end]
            if len(excerpt) > limits.max_value_chars:
                raise AnalysisLimitExceeded("依据摘录超过单值长度上限")
            raw_value = candidate.raw_value.strip()
            if len(raw_value) > limits.max_value_chars:
                raise AnalysisLimitExceeded("原文表达超过单值长度上限")
            if candidate.note is not None and len(candidate.note) > limits.max_value_chars:
                raise AnalysisLimitExceeded("说明文字超过单值长度上限")
            validated.append(
                ValidatedCandidate(
                    field_id=field_result.field_id,
                    status="needs_review" if candidate.ambiguous else "extracted",
                    chunk_id=candidate.chunk_id,
                    excerpt=excerpt,
                    locator_start=start,
                    locator_end=end,
                    raw_value=raw_value,
                    value=_typed_value(candidate, spec, limits),
                    note=candidate.note,
                )
            )
    return validated


def dedupe_candidates(candidates: list[ValidatedCandidate]) -> list[ValidatedCandidate]:
    """按取值与原文位置去重，避免重叠片段把同一处内容算作多个候选。"""
    seen: set[tuple[uuid.UUID, tuple[str, str], int]] = set()
    deduped: list[ValidatedCandidate] = []
    for candidate in candidates:
        key = (candidate.field_id, candidate.value_key, candidate.locator_start)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def parse_comparison_output(
    raw: str,
    *,
    field_ids: set[uuid.UUID],
    document_ids: set[uuid.UUID],
    citation_documents: dict[uuid.UUID, uuid.UUID],
    limits: AnalysisLimits,
) -> list[ValidatedConclusion]:
    """拒绝编造参与文档、字段、依据及无依据的差异/共同点结论。"""
    payload = parse_json_object(raw, limits)
    try:
        output = ModelComparisonOutput.model_validate(payload)
    except ValidationError as exc:
        raise AnalysisModelOutputInvalid(f"汇总输出结构非法：{exc.error_count()} 处") from exc
    if len(output.conclusions) > limits.max_fields * 3:
        raise AnalysisLimitExceeded("汇总结论数量超过任务上限")

    validated: list[ValidatedConclusion] = []
    seen: set[tuple[uuid.UUID, str, str]] = set()
    for conclusion in output.conclusions:
        if conclusion.field_id not in field_ids:
            raise AnalysisModelOutputInvalid("汇总引用了本次任务未定义的字段")
        if conclusion.kind not in {"common", "difference", "needs_review"}:
            raise AnalysisModelOutputInvalid("汇总结论类别不受支持")
        participants = list(dict.fromkeys(conclusion.document_ids))
        if not participants or any(item not in document_ids for item in participants):
            raise AnalysisModelOutputInvalid("汇总引用了任务范围之外的文档")
        citation_ids = list(
            dict.fromkeys(citation_id for _, citation_id in conclusion.citation_refs)
        )
        if any(citation_id not in citation_documents for citation_id in citation_ids):
            raise AnalysisModelOutputInvalid("汇总引用了本次结果之外的依据")
        for document_id, citation_id in conclusion.citation_refs:
            if citation_documents[citation_id] != document_id:
                raise AnalysisModelOutputInvalid("汇总依据与参与文档不一致")
            if document_id not in participants:
                raise AnalysisModelOutputInvalid("汇总依据来自未参与该结论的文档")
        if conclusion.kind == "difference" and (
            len(participants) < 2 or len(citation_ids) < 2
        ):
            raise AnalysisModelOutputInvalid("差异结论必须关联至少两份文档及其依据")
        if conclusion.kind == "common" and (
            len(participants) < 2 or not citation_ids
        ):
            raise AnalysisModelOutputInvalid("共同点必须列出至少两份文档及依据")
        key = (conclusion.field_id, conclusion.kind, conclusion.text.strip())
        if key in seen:
            continue
        seen.add(key)
        validated.append(
            ValidatedConclusion(
                field_id=conclusion.field_id,
                kind=conclusion.kind,
                text=conclusion.text.strip(),
                document_ids=participants,
                citation_ids=citation_ids,
            )
        )
    return validated
