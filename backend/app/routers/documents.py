from __future__ import annotations

import uuid
from collections.abc import Callable
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
from app.ingestion import (
    IngestionError,
    fetch_web_page,
    impact_summary,
    mark_source_reviews,
    safe_filename,
    web_title,
)
from app.models import Document, DocumentFolder, KnowledgeBase
from app.reconciliation import retry_failed_upload
from app.schemas import (
    DocumentImpactOut,
    DocumentOut,
    DocumentReplaceOut,
    TextDocumentCreate,
    WebDocumentCreate,
)
from app.storage import delete_objects_best_effort, get_object_store
from app.upload_service import (
    SUPPORTED_FORMAT_LABEL,
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
    stmt = stmt.order_by(
        Document.folder_id.asc().nullsfirst(),
        Document.sort_order,
        Document.created_at,
        Document.id,
    )
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
            detail=f"暂不支持该格式，当前支持 {SUPPORTED_FORMAT_LABEL}",
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

def _folder(
    session: Session,
    team_id: uuid.UUID,
    kb_id: uuid.UUID,
    folder_id: uuid.UUID | None,
) -> None:
    if folder_id is None:
        return
    folder = session.scalar(
        select(DocumentFolder).where(
            DocumentFolder.id == folder_id,
            DocumentFolder.team_id == team_id,
            DocumentFolder.knowledge_base_id == kb_id,
        )
    )
    if folder is None:
        raise HTTPException(404, "目标文件夹不存在或不属于当前知识库")


def _store_document(
    *,
    session: Session,
    team_id: uuid.UUID,
    team_name: str,
    user_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    filename: str,
    content_type: str,
    data: bytes,
    folder_id: uuid.UUID | None,
    title: str,
    source_url: str | None,
    replaces_id: uuid.UUID | None = None,
    after_reservation: Callable[[Document], object] | None = None,
) -> Document:
    try:
        reservation = reserve_upload(
            session,
            team_id=team_id,
            team_name=team_name,
            knowledge_base_id=knowledge_base_id,
            user_id=user_id,
            idempotency_key=uuid.uuid4(),
            filename=filename,
            content_type=content_type,
            data=data,
            folder_id=folder_id,
            source_url=source_url,
            replaces_id=replaces_id,
        )
    except QuotaExceeded as exc:
        session.rollback()
        raise HTTPException(409, "团队存储额度不足") from exc
    document = reservation.document
    document.title = title[:300]
    if after_reservation is not None:
        try:
            after_reservation(document)
        except Exception:
            session.rollback()
            raise
    session.commit()
    apply_context(session, user_id=user_id, team_id=team_id)
    try:
        get_object_store().put(document.storage_key, data)
    except Exception as exc:  # noqa: BLE001
        mark_upload_failed(session, document, "文件写入存储失败")
        session.commit()
        raise HTTPException(502, "文件写入存储失败") from exc
    mark_uploaded(session, document)
    session.commit()
    return document


@router.get("/{document_id}/impact", response_model=DocumentImpactOut)
def get_document_impact(
    document_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> DocumentImpactOut:
    document = session.get(Document, document_id)
    if document is None or document.team_id != context.team.id or document.deleted_at is not None:
        raise HTTPException(404, "资料不存在")
    return DocumentImpactOut.model_validate(
        impact_summary(session, document, context.team.id, context.user.id)
    )


@router.post("/text", response_model=DocumentOut, status_code=201)
def create_text_document(
    payload: TextDocumentCreate,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> DocumentOut:
    _get_knowledge_base(session, context.team.id, payload.knowledge_base_id)
    _folder(session, context.team.id, payload.knowledge_base_id, payload.folder_id)
    data = payload.content.encode("utf-8")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, "粘贴内容超过单份资料大小限制")
    document = _store_document(
        session=session,
        team_id=context.team.id,
        team_name=context.team.name,
        user_id=context.user.id,
        knowledge_base_id=payload.knowledge_base_id,
        filename=safe_filename(payload.title, ".txt"),
        content_type="text/plain",
        data=data,
        folder_id=payload.folder_id,
        title=payload.title,
        source_url=payload.source_url,
    )
    return DocumentOut.model_validate(document)


@router.post("/web", response_model=DocumentOut, status_code=201)
def create_web_document(
    payload: WebDocumentCreate,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> DocumentOut:
    _get_knowledge_base(session, context.team.id, payload.knowledge_base_id)
    _folder(session, context.team.id, payload.knowledge_base_id, payload.folder_id)
    try:
        final_url, data, content_type = fetch_web_page(payload.url, settings.max_upload_bytes)
    except IngestionError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    title = (payload.title or web_title(data, "网页资料")).strip() or "网页资料"
    extension = ".html" if "html" in content_type else ".txt"
    document = _store_document(
        session=session,
        team_id=context.team.id,
        team_name=context.team.name,
        user_id=context.user.id,
        knowledge_base_id=payload.knowledge_base_id,
        filename=safe_filename(title, extension),
        content_type="text/html" if extension == ".html" else "text/plain",
        data=data,
        folder_id=payload.folder_id,
        title=title,
        source_url=final_url,
    )
    return DocumentOut.model_validate(document)


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
    delete_objects_best_effort([document.storage_key])
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
        if document.folder_id is not None:
            maximum = session.scalar(
                select(Document.sort_order)
                .where(
                    Document.knowledge_base_id == document.knowledge_base_id,
                    Document.folder_id == document.folder_id,
                    Document.id != document.id,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.sort_order.desc())
                .limit(1)
            )
        else:
            maximum = session.scalar(
                select(Document.sort_order)
                .where(
                    Document.knowledge_base_id == document.knowledge_base_id,
                    Document.folder_id.is_(None),
                    Document.id != document.id,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.sort_order.desc())
                .limit(1)
            )
        document.sort_order = (maximum or -1) + 1
    session.commit()
    return DocumentOut.model_validate(document)


@router.post("/{document_id}/replace", response_model=DocumentReplaceOut, status_code=201)
async def replace_document(
    document_id: uuid.UUID,
    knowledge_base_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> DocumentReplaceOut:
    old = session.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.team_id == context.team.id,
            Document.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if old is None:
        raise HTTPException(404, "资料不存在")
    if old.knowledge_base_id != knowledge_base_id:
        raise HTTPException(422, "目标资料不属于当前知识库")
    filename = file.filename or "未命名"
    extension = extension_lower(filename)
    if extension not in SUPPORTED_TYPES:
        raise HTTPException(415, f"暂不支持该格式，当前支持 {SUPPORTED_FORMAT_LABEL}")
    data = await file.read()
    if not data or len(data) > settings.max_upload_bytes:
        raise HTTPException(422, "文件为空或超过大小限制")
    affected = impact_summary(session, old, context.team.id, context.user.id)
    document = _store_document(
        session=session,
        team_id=context.team.id,
        team_name=context.team.name,
        user_id=context.user.id,
        knowledge_base_id=old.knowledge_base_id,
        filename=filename,
        content_type=SUPPORTED_TYPES[extension],
        data=data,
        folder_id=old.folder_id,
        title=old.title,
        source_url=None,
        replaces_id=old.id,
        after_reservation=lambda replacement: mark_source_reviews(session, old, replacement),
    )
    return DocumentReplaceOut(
        document=DocumentOut.model_validate(document),
        affected=DocumentImpactOut.model_validate(affected),
    )
