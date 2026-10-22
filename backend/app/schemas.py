from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class CsrfOut(BaseModel):
    csrf_token: str
    expires_at: datetime


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str


class TeamSummary(BaseModel):
    id: uuid.UUID
    name: str
    role: str


class MeOut(BaseModel):
    user: UserOut
    teams: list[TeamSummary]
    csrf_token: str


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: str


class InvitationCreate(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern="^(admin|member)$")


class InvitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime


class InvitationCreatedOut(BaseModel):
    invitation: InvitationOut
    # 令牌只在创建响应中返回一次，服务端只保存哈希。
    token: str


class InvitationPreviewOut(BaseModel):
    status: str
    team_name: str | None
    email_masked: str | None


class InvitationAcceptRequest(BaseModel):
    token: str = Field(min_length=8, max_length=200)


class InvitationPreviewRequest(BaseModel):
    token: str = Field(min_length=8, max_length=200)


class RegisterWithInvitationRequest(BaseModel):
    token: str = Field(min_length=8, max_length=200)
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    title: str
    filename: str
    folder_id: uuid.UUID | None = None
    sort_order: int = 0
    size_bytes: int
    status: str
    failure_reason: str | None
    created_at: datetime
    source_url: str | None = None
    replaces_id: uuid.UUID | None = None


class Citation(BaseModel):
    number: int | None = None
    document_id: uuid.UUID
    document_title: str
    locator_label: str
    excerpt: str


class AskRequest(BaseModel):
    knowledge_base_id: uuid.UUID
    question: str = Field(min_length=1, max_length=2000)


class AskResponse(BaseModel):
    answer: str
    grounded: bool
    citations: list[Citation]


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=100)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class TurnAskRequest(BaseModel):
    client_request_id: uuid.UUID
    question: str = Field(min_length=1, max_length=4000)
    # 空值表示当前知识库全部可用资料；显式清单由服务端复核团队、库和状态。
    document_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=100)


class TurnOut(BaseModel):
    id: uuid.UUID
    seq: int
    client_request_id: uuid.UUID
    question: str
    scope_document_ids: list[uuid.UUID]
    status: str
    answer: str | None
    answer_draft: str | None
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None
    citations: list[Citation]


class TurnStreamEventOut(BaseModel):
    """SSE 事件唯一合同，前端按 type 采用对应字段。"""

    type: str
    turn: TurnOut | None = None
    text: str | None = None
    error_code: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# V2 分析合同
#
# 这里是 V2 的契约来源：路由、worker、前端类型与样例都按此实现。字段含义见
# V2-SCOPE.md，状态流转与错误语义见 docs/architecture/V2-ANALYSIS.md。
# ---------------------------------------------------------------------------

AnalysisKind = Literal["extraction", "comparison"]
AnalysisTaskStatus = Literal["queued", "running", "completed", "partial", "failed", "invalidated"]
AnalysisStage = Literal["queued", "extracting", "comparing", "finished"]
AnalysisDocumentStatus = Literal["queued", "running", "succeeded", "failed"]
AnalysisResultStatus = Literal["extracted", "not_mentioned", "needs_review", "failed"]
AnalysisFieldValueType = Literal["text", "number", "date", "text_list"]
AnalysisConclusionKind = Literal["common", "difference", "needs_review"]
AnalysisDatePrecision = Literal["year", "month", "day"]
AnalysisSummaryStatus = Literal["queued", "running", "succeeded", "failed"]

# 服务端错误码：前端按 code 决定文案，不解析 detail 正文。
AnalysisErrorCode = Literal[
    "ANALYSIS_INVALID_INPUT",
    "ANALYSIS_SOURCE_UNAVAILABLE",
    "ANALYSIS_IDEMPOTENCY_CONFLICT",
    "ANALYSIS_LIMIT_EXCEEDED",
    "ANALYSIS_MODEL_UNAVAILABLE",
    "ANALYSIS_MODEL_OUTPUT_INVALID",
    "ANALYSIS_BUDGET_EXHAUSTED",
    "ANALYSIS_SOURCE_INVALIDATED",
    "ANALYSIS_PERMISSION_REVOKED",
    "ANALYSIS_EXHAUSTED",
]


class AnalysisFieldInput(BaseModel):
    """创建时固定的字段/维度定义；名称仅用于展示，关联一律使用服务端分配的 ID。"""

    name: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)
    value_type: AnalysisFieldValueType


class AnalysisCreateRequest(BaseModel):
    kind: AnalysisKind
    document_ids: list[uuid.UUID] = Field(min_length=1)
    fields: list[AnalysisFieldInput] = Field(min_length=1)
    title: str | None = Field(default=None, max_length=120)
    # 重新运行时指向被复制的旧任务；首版重新运行创建新任务，旧结果不变。
    source_task_id: uuid.UUID | None = None


class AnalysisFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ordinal: int
    name: str
    description: str
    value_type: AnalysisFieldValueType


class AnalysisTaskOut(BaseModel):
    """列表与创建响应的任务元数据；不含字段值、摘录或结论。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    kind: AnalysisKind
    title: str
    status: AnalysisTaskStatus
    stage: AnalysisStage
    document_count: int
    completed_document_count: int
    failed_document_count: int
    source_task_id: uuid.UUID | None
    error_code: AnalysisErrorCode | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class AnalysisTaskListOut(BaseModel):
    items: list[AnalysisTaskOut]
    # 不透明游标；为空表示没有更多记录。
    next_cursor: str | None = None


class AnalysisCitationOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    locator_label: str
    excerpt: str
    table_number: int | None = None
    table_row: int | None = None
    table_headers: list[str] | None = None
    table_cells: list[str] | None = None


class AnalysisFieldResultOut(BaseModel):
    field_id: uuid.UUID
    status: AnalysisResultStatus
    value_text: str | None = None
    value_number: float | None = None
    value_date: date | None = None
    value_list: list[str] | None = None
    raw_value: str | None = None
    unit: str | None = None
    date_precision: AnalysisDatePrecision | None = None
    note: str | None = None
    citations: list[AnalysisCitationOut] = Field(default_factory=list)


class AnalysisDocumentOut(BaseModel):
    """逐文档执行状态与覆盖情况；未成功时不返回字段值。"""

    model_config = ConfigDict(from_attributes=True)

    document_id: uuid.UUID
    document_title: str
    ordinal: int
    status: AnalysisDocumentStatus
    attempts: int
    expected_chunk_count: int
    processed_chunk_count: int
    # 实际完成的供应商/模型；用于区分真实评测与确定性替身。
    provider: str | None = None
    model: str | None = None
    error_code: AnalysisErrorCode | None
    error_detail: str | None


class AnalysisDocumentResultOut(BaseModel):
    document_id: uuid.UUID
    document_title: str
    ordinal: int
    fields: list[AnalysisFieldResultOut]


class AnalysisSummaryOut(BaseModel):
    """对比汇总阶段；提取任务不返回它。"""

    status: AnalysisSummaryStatus
    attempts: int
    provider: str | None = None
    model: str | None = None
    error_code: AnalysisErrorCode | None = None
    error_detail: str | None = None


class AnalysisConclusionOut(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    kind: AnalysisConclusionKind
    text: str
    document_ids: list[uuid.UUID]
    citations: list[AnalysisCitationOut]


class AnalysisDetailOut(BaseModel):
    """详情合同。

    来源失效时只返回 task 元数据、字段定义与文档范围，字段值、摘录与结论一律不返回；
    `source_valid=false` 且 `invalid_reason` 说明原因，前端据此显示受限视图。
    """

    task: AnalysisTaskOut
    fields: list[AnalysisFieldOut]
    documents: list[AnalysisDocumentOut]
    results: list[AnalysisDocumentResultOut]
    conclusions: list[AnalysisConclusionOut]
    summary: AnalysisSummaryOut | None = None
    source_valid: bool
    invalid_reason: str | None = None


# ---------------------------------------------------------------------------
# V3 笔记、报告与可复用要求合同
# ---------------------------------------------------------------------------

NoteSourceType = Literal["manual", "document", "conversation", "report"]
ReportStatus = Literal["draft", "reviewed"]
ReportSourceType = Literal["analysis", "document", "note"]
SavedRequirementKind = Literal["analysis_fields", "report"]
ReportLength = Literal["short", "medium", "long"]


class MissingItemOut(BaseModel):
    kind: Literal["missing", "review", "source"]
    text: str


class EvidenceSnapshotInput(BaseModel):
    document_id: uuid.UUID | None = None
    document_title: str = Field(min_length=1, max_length=300)
    locator_label: str = Field(default="", max_length=200)
    excerpt: str = Field(min_length=1, max_length=4000)


class EvidenceSnapshotOut(EvidenceSnapshotInput):
    pass


class NoteCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    source_type: NoteSourceType = "manual"
    source_id: uuid.UUID | None = None
    citations: list[EvidenceSnapshotInput] = Field(default_factory=list, max_length=50)


class NoteUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    citations: list[EvidenceSnapshotInput] = Field(default_factory=list, max_length=50)


class NoteOut(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    title: str
    content: str
    source_type: NoteSourceType
    source_id: uuid.UUID | None
    citations: list[EvidenceSnapshotOut]
    created_at: datetime
    updated_at: datetime


class NoteListOut(BaseModel):
    items: list[NoteOut]


class ReportCitationOut(EvidenceSnapshotOut):
    id: uuid.UUID
    ordinal: int


class ReportSectionOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    heading: str
    body: str
    citations: list[ReportCitationOut]


class ReportSectionUpdate(BaseModel):
    id: uuid.UUID | None = None
    heading: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=100_000)


class ReportSourceOut(BaseModel):
    id: uuid.UUID
    source_type: ReportSourceType
    source_id: uuid.UUID
    title_snapshot: str
    available: bool


class ReportOut(BaseModel):
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    source_analysis_id: uuid.UUID | None
    title: str
    purpose: str
    focus: str
    length: ReportLength
    status: ReportStatus
    needs_review: bool
    review_note: str | None
    source_review_required: bool
    source_review_note: str | None
    gaps: list[MissingItemOut]
    sections: list[ReportSectionOut]
    sources: list[ReportSourceOut]
    created_at: datetime
    updated_at: datetime


class ReportSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    status: ReportStatus
    needs_review: bool
    source_review_required: bool
    source_analysis_id: uuid.UUID | None
    section_count: int
    created_at: datetime
    updated_at: datetime


class ReportListOut(BaseModel):
    items: list[ReportSummaryOut]


class ReportFromAnalysisRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    purpose: str = Field(default="", max_length=2000)
    focus: str = Field(default="", max_length=2000)
    length: ReportLength = "medium"


class ReportFromSourcesRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=2000)
    focus: str = Field(default="", max_length=2000)
    length: ReportLength = "medium"
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    note_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class ReportUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default="", max_length=2000)
    focus: str = Field(default="", max_length=2000)
    length: ReportLength
    status: ReportStatus
    source_review_acknowledged: bool = False
    gaps: list[MissingItemOut] = Field(default_factory=list, max_length=50)
    sections: list[ReportSectionUpdate] = Field(min_length=1, max_length=30)


class RequirementPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class SavedRequirementCreate(BaseModel):
    kind: SavedRequirementKind
    name: str = Field(min_length=1, max_length=120)
    payload: dict[str, object]


class SavedRequirementOut(BaseModel):
    id: uuid.UUID
    kind: SavedRequirementKind
    name: str
    payload: dict[str, object]
    updated_at: datetime


class SavedRequirementListOut(BaseModel):
    items: list[SavedRequirementOut]


class AnalysisSuggestionRequest(BaseModel):
    requirement: str = Field(min_length=2, max_length=2000)
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=10)


class AnalysisSuggestionOut(BaseModel):
    fields: list[AnalysisFieldInput]
    rationale: str


class DocumentGuideOut(BaseModel):
    document_id: uuid.UUID
    document_title: str
    summary: str
    section_labels: list[str]
    key_concepts: list[str]
    suggested_questions: list[str]
    coverage_note: str


class DocumentSearchHitOut(BaseModel):
    document_id: uuid.UUID
    document_title: str
    locator_label: str
    excerpt: str
    before: str
    after: str


class DocumentSearchOut(BaseModel):
    query: str
    hits: list[DocumentSearchHitOut]


class TextDocumentCreate(BaseModel):
    knowledge_base_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=2_000_000)
    source_url: str | None = Field(default=None, max_length=2000)
    folder_id: uuid.UUID | None = None


class WebDocumentCreate(BaseModel):
    knowledge_base_id: uuid.UUID
    url: str = Field(min_length=8, max_length=2000, pattern=r"^https?://")
    title: str | None = Field(default=None, max_length=300)
    folder_id: uuid.UUID | None = None


class DocumentImpactOut(BaseModel):
    document_id: uuid.UUID
    document_title: str
    note_count: int
    analysis_count: int
    report_count: int
    stale_report_ids: list[uuid.UUID]


class DocumentReplaceOut(BaseModel):
    document: DocumentOut
    affected: DocumentImpactOut


class ReportGapOut(BaseModel):
    items: list[MissingItemOut]
    coverage_note: str


class ReportSectionSuggestionRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=2000)


class ReportSectionSuggestionOut(BaseModel):
    original: str
    suggestion: str
    rationale: str
    provider: str


# ---------------------------------------------------------------------------
# V4 固定计划的从分析到报告执行
# ---------------------------------------------------------------------------

AutomationStatus = Literal[
    "queued", "running", "completed", "failed", "canceled", "invalidated"
]
AutomationStage = Literal["queued", "analysis", "report", "canceling", "finished"]
AutomationErrorCode = Literal[
    "AUTOMATION_INVALID_INPUT",
    "AUTOMATION_SOURCE_UNAVAILABLE",
    "AUTOMATION_IDEMPOTENCY_CONFLICT",
    "AUTOMATION_LIMIT_EXCEEDED",
    "AUTOMATION_ANALYSIS_PARTIAL",
    "AUTOMATION_ANALYSIS_FAILED",
    "AUTOMATION_ANALYSIS_INVALIDATED",
    "AUTOMATION_REPORT_FAILED",
    "AUTOMATION_PERMISSION_REVOKED",
    "AUTOMATION_EXHAUSTED",
]


class AutomationCreateRequest(BaseModel):
    kind: AnalysisKind
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    fields: list[AnalysisFieldInput] = Field(min_length=1, max_length=20)
    analysis_title: str | None = Field(default=None, max_length=120)
    report_title: str = Field(default="", max_length=200)
    report_purpose: str = Field(default="", max_length=2000)
    report_focus: str = Field(default="", max_length=2000)
    report_length: ReportLength = "medium"


class AutomationStepOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    kind: str
    status: str
    target_id: uuid.UUID | None
    error_code: str | None
    detail: str | None
    created_at: datetime
    finished_at: datetime | None


class AutomationOut(BaseModel):
    """执行元数据与确认计划；不重复返回分析字段值、报告正文或原文摘录。"""

    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    title: str
    status: AutomationStatus
    stage: AutomationStage
    analysis_kind: AnalysisKind
    document_ids: list[uuid.UUID]
    document_count: int
    fields: list[AnalysisFieldInput]
    analysis_title: str
    report_title: str
    report_purpose: str
    report_focus: str
    report_length: ReportLength
    analysis_id: uuid.UUID | None
    report_id: uuid.UUID | None
    attempts: int
    max_attempts: int
    error_code: AutomationErrorCode | None
    error_detail: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class AutomationDetailOut(AutomationOut):
    steps: list[AutomationStepOut]


class AutomationListOut(BaseModel):
    items: list[AutomationOut]
