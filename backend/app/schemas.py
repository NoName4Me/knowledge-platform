from __future__ import annotations

import uuid
from datetime import datetime

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
