from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Document, TeamQuota, UploadRequest
from app.storage import build_storage_key
from app.tasks import enqueue_document_task

settings = get_settings()

SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class QuotaExceeded(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


@dataclass
class Reservation:
    document: Document
    response_status: int


def extension_lower(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot >= 0 else ""


def fingerprint(
    knowledge_base_id: uuid.UUID,
    filename: str,
    content_type: str,
    size_bytes: int,
    digest: str,
) -> str:
    encoded = f"{knowledge_base_id}\0{filename}\0{content_type}\0{size_bytes}\0{digest}".encode()
    return hashlib.sha256(encoded).hexdigest()


def reserve_upload(
    session: Session,
    *,
    team_id: uuid.UUID,
    team_name: str,
    knowledge_base_id: uuid.UUID,
    user_id: uuid.UUID,
    idempotency_key: uuid.UUID,
    filename: str,
    content_type: str,
    data: bytes,
) -> Reservation:
    digest = hashlib.sha256(data).hexdigest()
    request_fingerprint = fingerprint(knowledge_base_id, filename, content_type, len(data), digest)
    # 先取团队额度行锁。所有团队内新的上传都串行到这里，避免读取同一份剩余额度。
    quota = session.scalars(
        select(TeamQuota).where(TeamQuota.team_id == team_id).with_for_update()
    ).one_or_none()
    if quota is None:
        quota = TeamQuota(team_id=team_id)
        session.add(quota)
        session.flush()

    existing = session.scalars(
        select(UploadRequest).where(
            UploadRequest.team_id == team_id,
            UploadRequest.user_id == user_id,
            UploadRequest.idempotency_key == idempotency_key,
        )
    ).one_or_none()
    if existing is not None:
        if existing.fingerprint != request_fingerprint:
            raise IdempotencyConflict
        document = session.get(Document, existing.document_id)
        if document is None:
            raise IdempotencyConflict
        return Reservation(document=document, response_status=200)

    if (
        quota.reserved_documents >= settings.team_max_documents
        or quota.reserved_bytes + len(data) > settings.team_max_bytes
    ):
        raise QuotaExceeded

    document_id = uuid.uuid4()
    document = Document(
        id=document_id,
        team_id=team_id,
        knowledge_base_id=knowledge_base_id,
        uploaded_by_id=user_id,
        title=filename.rsplit(".", 1)[0],
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        storage_key=build_storage_key(team_id, document_id, filename),
        status="uploading",
    )
    quota.reserved_documents += 1
    quota.reserved_bytes += len(data)
    session.add_all(
        [
            document,
            UploadRequest(
                id=uuid.uuid4(),
                team_id=team_id,
                user_id=user_id,
                idempotency_key=idempotency_key,
                fingerprint=request_fingerprint,
                document_id=document_id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            ),
        ]
    )
    session.flush()
    return Reservation(document=document, response_status=201)


def mark_uploaded(session: Session, document: Document) -> None:
    # 行锁让“删除”“上传完成”“重复请求”串行化。
    locked = session.execute(
        text("select status from documents where id = :id for update"),
        {"id": document.id},
    ).one_or_none()
    if locked is None:
        raise RuntimeError("上传记录不存在")
    if locked.status != "uploading":
        # 删除已先提交时，晚到的上传完成不能复活文档。
        return
    document.status = "pending"
    enqueue_document_task(session, document)


def mark_upload_failed(session: Session, document: Document, reason: str) -> None:
    document.status = "failed"
    document.failure_reason = reason[:500]


def mark_deleted(session: Session, document: Document) -> None:
    if document.status == "deleted":
        return
    document.status = "deleted"
    document.deleted_at = datetime.now(UTC)
    # 让尚未领取的解析任务失效；running 任务会在发布时看到 deleted。
    session.execute(
        text(
            """
            update processing_tasks
            set status = 'canceled', lease_token = null, lease_expires_at = null,
                last_error = '来源已删除', updated_at = now()
            where document_id = :document_id and status in ('queued', 'running')
            """
        ),
        {"document_id": document.id},
    )
    session.flush()


def release_quota_once(session: Session, team_id: uuid.UUID, document: Document) -> None:
    if document.quota_released_at is not None:
        return
    quota = session.scalars(
        select(TeamQuota).where(TeamQuota.team_id == team_id).with_for_update()
    ).one_or_none()
    if quota is None:
        quota = TeamQuota(team_id=team_id)
        session.add(quota)
        session.flush()
    quota.reserved_documents = max(0, quota.reserved_documents - 1)
    quota.reserved_bytes = max(0, quota.reserved_bytes - document.size_bytes)
    document.quota_released_at = datetime.now(UTC)
    session.flush()
