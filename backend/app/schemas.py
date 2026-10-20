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
    size_bytes: int
    status: str
    failure_reason: str | None
    created_at: datetime


class Citation(BaseModel):
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


class TurnOut(BaseModel):
    id: uuid.UUID
    seq: int
    client_request_id: uuid.UUID
    question: str
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
