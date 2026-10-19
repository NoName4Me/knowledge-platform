from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


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
