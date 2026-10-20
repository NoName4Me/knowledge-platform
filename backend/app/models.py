from __future__ import annotations

import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 1024

DOCUMENT_STATUSES = ("uploading", "pending", "processing", "ready", "failed", "deleted")
TASK_STATUSES = ("queued", "running", "succeeded", "failed", "canceled")


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


TEAM_ROLES = ("admin", "member")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list[TeamMembership]] = relationship(back_populates="user")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    memberships: Mapped[list[TeamMembership]] = relationship(back_populates="team")
    knowledge_bases: Mapped[list[KnowledgeBase]] = relationship(back_populates="team")


class TeamMembership(Base):
    __tablename__ = "team_memberships"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", name="uq_team_membership"),
        CheckConstraint("role in ('admin', 'member')", name="ck_membership_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped[Team] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("team_id", "name", name="uq_kb_team_name"),
        # 复合外键的引用目标：保证文档不能挂到其他团队的知识库上。
        UniqueConstraint("id", "team_id", name="uq_kb_id_team"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    team: Mapped[Team] = relationship(back_populates="knowledge_bases")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status in ('uploading', 'pending', 'processing', 'ready', 'failed', 'deleted')",
            name="ck_document_status",
        ),
        UniqueConstraint("id", "team_id", name="uq_document_id_team"),
        ForeignKeyConstraint(
            ["knowledge_base_id", "team_id"],
            ["knowledge_bases.id", "knowledge_bases.team_id"],
            name="fk_document_kb_team",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(300))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(400), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quota_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TeamQuota(Base):
    __tablename__ = "team_quotas"
    __table_args__ = (
        CheckConstraint("reserved_documents >= 0", name="ck_quota_documents_nonnegative"),
        CheckConstraint("reserved_bytes >= 0", name="ck_quota_bytes_nonnegative"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        primary_key=True,
    )
    reserved_documents: Mapped[int] = mapped_column(Integer, default=0)
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


class UploadRequest(Base):
    __tablename__ = "upload_requests"
    __table_args__ = (
        UniqueConstraint("team_id", "user_id", "idempotency_key", name="uq_upload_idempotency"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    fingerprint: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunk_ordinal"),
        ForeignKeyConstraint(
            ["document_id", "team_id"],
            ["documents.id", "documents.team_id"],
            name="fk_chunk_document_team",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    locator_label: Mapped[str] = mapped_column(String(200))
    locator_start: Mapped[int | None] = mapped_column(Integer)
    locator_end: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProcessingTask(Base):
    __tablename__ = "processing_tasks"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed', 'canceled')",
            name="ck_task_status",
        ),
        ForeignKeyConstraint(
            ["document_id", "team_id"],
            ["documents.id", "documents.team_id"],
            name="fk_task_document_team",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "knowledge_base_id",
            "id",
            "created_by_id",
            name="uq_conversation_owner_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100), default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_turn_seq"),
        UniqueConstraint("conversation_id", "client_request_id", name="uq_turn_request"),
        CheckConstraint(
            "status in ('answering', 'completed', 'insufficient', 'failed', 'interrupted')",
            name="ck_turn_status",
        ),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "conversation_id", "created_by_id"],
            [
                "conversations.team_id",
                "conversations.knowledge_base_id",
                "conversations.id",
                "conversations.created_by_id",
            ],
            name="fk_turn_conversation_owner",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "team_id",
            "knowledge_base_id",
            "id",
            "created_by_id",
            name="uq_turn_id_owner_scope",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    client_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    seq: Mapped[int] = mapped_column(Integer)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="answering", index=True)
    answer: Mapped[str | None] = mapped_column(Text)
    # 流式生成的中间文本；完成或失败后清空，避免与最终答案重复。
    answer_draft: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CitationRecord(Base):
    __tablename__ = "citations"
    __table_args__ = (
        UniqueConstraint("turn_id", "ordinal", name="uq_citation_ordinal"),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "turn_id", "owner_id"],
            [
                "turns.team_id",
                "turns.knowledge_base_id",
                "turns.id",
                "turns.created_by_id",
            ],
            name="fk_citation_turn_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "team_id"],
            ["documents.id", "documents.team_id"],
            name="fk_citation_document_team",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE")
    )
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_chunks.id", ondelete="SET NULL")
    )
    filename_snapshot: Mapped[str] = mapped_column(String(300))
    locator_label_snapshot: Mapped[str] = mapped_column(String(200))


class SessionToken(Base):
    __tablename__ = "session_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamInvitation(Base):
    """一次性团队邀请：只保存令牌哈希，兑换后立即失效。"""

    __tablename__ = "team_invitations"
    __table_args__ = (
        CheckConstraint("role in ('admin', 'member')", name="ck_invitation_role"),
        UniqueConstraint("token_hash", name="uq_invitation_token"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(20), default="member")
    token_hash: Mapped[str] = mapped_column(String(64))
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


ANALYSIS_KINDS = ("extraction", "comparison")
ANALYSIS_STATUSES = ("queued", "running", "completed", "partial", "failed", "invalidated")
ANALYSIS_DOCUMENT_STATUSES = ("queued", "running", "succeeded", "failed")
ANALYSIS_RESULT_STATUSES = ("extracted", "not_mentioned", "needs_review", "failed")
ANALYSIS_FIELD_TYPES = ("text", "number", "date", "text_list")
ANALYSIS_CONCLUSION_KINDS = ("common", "difference", "needs_review")
ANALYSIS_SUMMARY_STATUSES = ("queued", "running", "succeeded", "failed")


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    __table_args__ = (
        CheckConstraint("kind in ('extraction', 'comparison')", name="ck_analysis_kind"),
        CheckConstraint(
            "status in ('queued', 'running', 'completed', 'partial', 'failed', 'invalidated')",
            name="ck_analysis_status",
        ),
        CheckConstraint(
            "stage in ('queued', 'extracting', 'comparing', 'finished')",
            name="ck_analysis_stage",
        ),
        CheckConstraint("document_count > 0", name="ck_analysis_document_count"),
        CheckConstraint(
            "completed_document_count >= 0 and failed_document_count >= 0",
            name="ck_analysis_document_counts",
        ),
        UniqueConstraint(
            "team_id",
            "created_by_id",
            "idempotency_key",
            name="uq_analysis_idempotency",
        ),
        UniqueConstraint(
            "team_id",
            "knowledge_base_id",
            "id",
            "created_by_id",
            name="uq_analysis_task_owner_scope",
        ),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "source_task_id", "created_by_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_source_task_owner",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(30), default="queued")
    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    source_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    document_count: Mapped[int] = mapped_column(Integer)
    completed_document_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_document_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisField(Base):
    __tablename__ = "analysis_fields"
    __table_args__ = (
        CheckConstraint(
            "value_type in ('text', 'number', 'date', 'text_list')",
            name="ck_analysis_field_type",
        ),
        UniqueConstraint("task_id", "ordinal", name="uq_analysis_field_ordinal"),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_field_task_owner",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(String(500))
    value_type: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisDocument(Base):
    __tablename__ = "analysis_documents"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="ck_analysis_document_status",
        ),
        CheckConstraint(
            "attempts >= 0 and max_attempts > 0 and processed_chunk_count >= 0 "
            "and expected_chunk_count >= 0 and model_calls >= 0",
            name="ck_analysis_document_counts",
        ),
        UniqueConstraint("task_id", "document_id", name="uq_analysis_document"),
        UniqueConstraint("task_id", "ordinal", name="uq_analysis_document_ordinal"),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_document_task_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "team_id"],
            ["documents.id", "documents.team_id"],
            name="fk_analysis_document_source_team",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id", ondelete="CASCADE")
    )
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_bases.id", ondelete="CASCADE")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_fingerprint: Mapped[str | None] = mapped_column(String(64))
    expected_chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    # 实际使用的供应商/模型与提示词、输出 schema 版本，用于区分替身与真实结果。
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    schema_version: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnalysisFieldResult(Base):
    __tablename__ = "analysis_field_results"
    __table_args__ = (
        CheckConstraint(
            "status in ('extracted', 'not_mentioned', 'needs_review', 'failed')",
            name="ck_analysis_result_status",
        ),
        UniqueConstraint("document_execution_id", "field_id", name="uq_analysis_field_result"),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_result_task_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_execution_id", "task_id"],
            ["analysis_documents.id", "analysis_documents.task_id"],
            name="fk_analysis_result_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["field_id", "task_id"],
            ["analysis_fields.id", "analysis_fields.task_id"],
            name="fk_analysis_result_field",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    field_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20))
    value_text: Mapped[str | None] = mapped_column(Text)
    value_number: Mapped[float | None] = mapped_column(Numeric(30, 10))
    value_date: Mapped[date | None] = mapped_column(Date)
    value_list: Mapped[list[str] | None] = mapped_column(JSONB)
    raw_value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(40))
    date_precision: Mapped[str | None] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisConclusion(Base):
    __tablename__ = "analysis_conclusions"
    __table_args__ = (
        CheckConstraint(
            "kind in ('common', 'difference', 'needs_review')",
            name="ck_analysis_conclusion_kind",
        ),
        UniqueConstraint("task_id", "ordinal", name="uq_analysis_conclusion_ordinal"),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_conclusion_task_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["field_id", "task_id"],
            ["analysis_fields.id", "analysis_fields.task_id"],
            name="fk_analysis_conclusion_field",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    field_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    document_ids: Mapped[list[str]] = mapped_column(JSONB)
    ordinal: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AnalysisSummary(Base):
    """对比任务的汇总阶段；只有全部逐文档提取完成后才创建和领取。"""

    __tablename__ = "analysis_summaries"
    __table_args__ = (
        CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="ck_analysis_summary_status",
        ),
        CheckConstraint("attempts >= 0 and max_attempts > 0", name="ck_analysis_summary_counts"),
        UniqueConstraint("task_id", name="uq_analysis_summary_task"),
        UniqueConstraint(
            "id",
            "team_id",
            "knowledge_base_id",
            "task_id",
            "owner_id",
            name="uq_analysis_summary_scope",
        ),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_summary_task_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            [
                "document_execution_id",
                "team_id",
                "knowledge_base_id",
                "task_id",
                "owner_id",
            ],
            [
                "analysis_documents.id",
                "analysis_documents.team_id",
                "analysis_documents.knowledge_base_id",
                "analysis_documents.task_id",
                "analysis_documents.owner_id",
            ],
            name="fk_analysis_summary_document_scope",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    schema_version: Mapped[str | None] = mapped_column(String(40))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AnalysisCitation(Base):
    __tablename__ = "analysis_citations"
    __table_args__ = (
        CheckConstraint(
            "(field_result_id is not null) <> (conclusion_id is not null)",
            name="ck_analysis_citation_one_target",
        ),
        ForeignKeyConstraint(
            ["team_id", "knowledge_base_id", "task_id", "owner_id"],
            [
                "analysis_tasks.team_id",
                "analysis_tasks.knowledge_base_id",
                "analysis_tasks.id",
                "analysis_tasks.created_by_id",
            ],
            name="fk_analysis_citation_task_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_execution_id", "task_id"],
            ["analysis_documents.id", "analysis_documents.task_id"],
            name="fk_analysis_citation_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["chunk_id", "document_id", "team_id"],
            [
                "document_chunks.id",
                "document_chunks.document_id",
                "document_chunks.team_id",
            ],
            name="fk_analysis_citation_chunk_team",
            ondelete="SET NULL",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    document_execution_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    field_result_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    conclusion_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    chunk_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ordinal: Mapped[int] = mapped_column(Integer)
    locator_label: Mapped[str] = mapped_column(String(200))
    excerpt: Mapped[str] = mapped_column(Text)
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
