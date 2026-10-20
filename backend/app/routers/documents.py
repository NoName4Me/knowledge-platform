from __future__ import annotations

import contextlib
import uuid
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context, require_admin
from app.config import get_settings
from app.db import apply_context, get_session
from app.models import Document, DocumentFolder, KnowledgeBase
from app.reconciliation import retry_failed_upload
from app.schemas import DocumentOut
from app.storage import get_object_store
from app.upload_service import (
    SUPPORTED_TYPES,
    IdempotencyConflict,
    QuotaExceeded,
    extension_lower,
    mark_deleted,
    mark_upload_failed,
    mark_uploaded,
    release_quota_once,
    reserve_upload,
)

router = APIRouter(prefix="/api/teams/{team_id}/documents", tags=["documents"])
settings = get_settings()


def _get_knowledge_base(session: Session, team_id: uuid.UUID, kb_id: uuid.UUID) -> KnowledgeBase:
    # 调用方已经应用团队上下文；这里仍显式校验，避免把路径参数当作归属。
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or kb.team_id != team_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")
    return kb


@router.get("", response_model=list[DocumentOut])
def list_documents(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID | None = None,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> list[DocumentOut]:
    stmt = select(Document).where(
        Document.team_id == context.team.id,
        Document.deleted_at.is_(None),
    )
    if knowledge_base_id is not None:
        stmt = stmt.where(Document.knowledge_base_id == knowledge_base_id)
    stmt = stmt.order_by(Document.created_at.desc())
    return [DocumentOut.model_validate(doc) for doc in session.scalars(stmt)]


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    folder_id: uuid.UUID | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    _get_knowledge_base(session, context.team.id, knowledge_base_id)
    if folder_id is not None:
        folder = session.scalar(
            select(DocumentFolder)
            .where(
                DocumentFolder.id == folder_id,
                DocumentFolder.team_id == context.team.id,
                DocumentFolder.knowledge_base_id == knowledge_base_id,
            )
            .with_for_update()
        )
        if folder is None:
            raise HTTPException(404, "目标文件夹不存在或不属于当前知识库")

    if idempotency_key is None:
        raise HTTPException(status_code=422, detail="缺少 Idempotency-Key")
    try:
        parsed_key = uuid.UUID(idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Idempotency-Key 必须是 UUID") from exc

    filename = file.filename or "未命名"
    extension = extension_lower(filename)
    if extension not in SUPPORTED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="暂不支持该格式，当前支持 PDF、DOCX、TXT、Markdown",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件内容为空")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"文件超过 {settings.max_upload_bytes // (1024 * 1024)} MB 限制",
        )

    try:
        reservation = reserve_upload(
            session,
            team_id=context.team.id,
            team_name=context.team.name,
            knowledge_base_id=knowledge_base_id,
            user_id=context.user.id,
            idempotency_key=parsed_key,
            filename=filename,
            content_type=SUPPORTED_TYPES[extension],
            data=data,
            folder_id=folder_id,
        )
    except QuotaExceeded as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="团队存储额度不足") from exc
    except IdempotencyConflict as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="幂等键已被不同内容使用") from exc
    document = reservation.document
    # 预留先提交：即使对象写入或后续事务失败，也能通过对账识别而不是丢掉额度记录。
    session.commit()
    # SET LOCAL 随上一个事务结束而失效；恢复团队上下文后才能完成上传。
    apply_context(
        session,
        user_id=context.user.id,
        team_id=context.team.id,
    )

    if reservation.response_status == 200:
        return Response(
            content=DocumentOut.model_validate(document).model_dump_json(),
            status_code=200,
            media_type="application/json",
        )

    store = get_object_store()
    try:
        store.put(document.storage_key, data)
    except Exception as exc:  # noqa: BLE001
        mark_upload_failed(session, document, "文件写入存储失败")
        session.commit()
        raise HTTPException(status_code=502, detail="文件写入存储失败") from exc

    mark_uploaded(session, document)
    session.commit()
    return Response(
        content=DocumentOut.model_validate(document).model_dump_json(),
        status_code=201,
        media_type="application/json",
    )


@router.get("/{document_id}/content")
def download_document(
    team_id: uuid.UUID,
    document_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    document = session.get(Document, document_id)
    if document is None or document.team_id != context.team.id or document.status == "deleted":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")
    if document.status == "uploading":
        raise HTTPException(status_code=409, detail="文件仍在上传")

    try:
        data = get_object_store().get(document.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="原始文件已不存在") from exc

    return Response(
        content=data,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(document.filename, safe='')}"
        },
    )


@router.post("/{document_id}/retry", response_model=DocumentOut, status_code=202)
def retry_document(
    team_id: uuid.UUID,
    document_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> DocumentOut:
    document = session.get(Document, document_id)
    if document is None or document.team_id != context.team.id:
        raise HTTPException(status_code=404, detail="文档不存在")
    if document.status == "deleted":
        raise HTTPException(status_code=410, detail="文档已删除")
    if document.status in {"pending", "processing"}:
        return DocumentOut.model_validate(document)
    if document.status != "failed":
        raise HTTPException(status_code=409, detail="当前文档状态不可重试")
    try:
        get_object_store().get(document.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=410, detail="原始文件已不存在") from exc
    retry_failed_upload(session, document)
    session.commit()
    return DocumentOut.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    team_id: uuid.UUID,
    document_id: uuid.UUID,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    document = session.get(Document, document_id)
    if document is None or document.team_id != context.team.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    # 先提交不可见状态；后续对象删除失败不会让文档复活。
    mark_deleted(session, document)
    session.commit()
    with contextlib.suppress(OSError):
        get_object_store().delete(document.storage_key)
    apply_context(session, user_id=context.user.id, team_id=context.team.id)
    release_quota_once(session, context.team.id, document)
    session.commit()


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    filename: str | None = Field(
        default=None, min_length=1, max_length=300, pattern=r"^[^/\\\x00-\x1f]+$"
    )
    folder_id: uuid.UUID | None = None


@router.patch("/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> DocumentOut:
    document = session.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.team_id == context.team.id,
            Document.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if document is None:
        raise HTTPException(404, "文档不存在")
    if "filename" in payload.model_fields_set:
        if payload.filename is None:
            raise HTTPException(422, "资料名称不能为空")
        if extension_lower(payload.filename) != extension_lower(document.filename):
            raise HTTPException(422, "重命名时不能修改文件扩展名")
        document.filename = payload.filename
        document.title = payload.filename.rsplit(".", 1)[0]
    if "folder_id" in payload.model_fields_set:
        if payload.folder_id is not None:
            folder = session.scalar(
                select(DocumentFolder)
                .where(
                    DocumentFolder.id == payload.folder_id,
                    DocumentFolder.team_id == context.team.id,
                    DocumentFolder.knowledge_base_id == document.knowledge_base_id,
                )
                .with_for_update()
            )
            if folder is None:
                raise HTTPException(404, "目标文件夹不存在或不属于当前知识库")
        document.folder_id = payload.folder_id
    session.commit()
    return DocumentOut.model_validate(document)
