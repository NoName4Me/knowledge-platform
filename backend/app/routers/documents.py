from __future__ import annotations

import contextlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context
from app.config import get_settings
from app.db import get_session
from app.models import Document, KnowledgeBase
from app.parsing import extension_of
from app.schemas import DocumentOut
from app.storage import build_storage_key, get_object_store
from app.tasks import enqueue_document_task

router = APIRouter(prefix="/api/teams/{team_id}/documents", tags=["documents"])
settings = get_settings()

ALLOWED_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def _get_knowledge_base(session: Session, team_id: uuid.UUID, kb_id: uuid.UUID) -> KnowledgeBase:
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
    stmt = select(Document).where(Document.team_id == context.team.id)
    if knowledge_base_id is not None:
        stmt = stmt.where(Document.knowledge_base_id == knowledge_base_id)
    stmt = stmt.order_by(Document.created_at.desc())
    return [DocumentOut.model_validate(doc) for doc in session.scalars(stmt)]


@router.post("", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> DocumentOut:
    _get_knowledge_base(session, context.team.id, knowledge_base_id)

    filename = file.filename or "未命名"
    extension = extension_of(filename)
    if extension not in ALLOWED_CONTENT_TYPES:
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

    document = Document(
        team_id=context.team.id,
        knowledge_base_id=knowledge_base_id,
        uploaded_by_id=context.user.id,
        title=filename.rsplit(".", 1)[0],
        filename=filename,
        content_type=ALLOWED_CONTENT_TYPES[extension],
        size_bytes=len(data),
        storage_key="",
        status="pending",
    )
    document.storage_key = build_storage_key(context.team.id, document.id, filename)
    session.add(document)
    session.flush()

    store = get_object_store()
    try:
        store.put(document.storage_key, data)
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="文件写入存储失败"
        ) from exc

    enqueue_document_task(session, document)
    session.commit()
    return DocumentOut.model_validate(document)


@router.get("/{document_id}/content")
def download_document(
    team_id: uuid.UUID,
    document_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    document = session.get(Document, document_id)
    if document is None or document.team_id != context.team.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    try:
        data = get_object_store().get(document.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="原始文件已不存在") from exc

    return Response(
        content=data,
        media_type=document.content_type,
        headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    team_id: uuid.UUID,
    document_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    document = session.get(Document, document_id)
    if document is None or document.team_id != context.team.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文档不存在")

    with contextlib.suppress(OSError):
        get_object_store().delete(document.storage_key)
    session.delete(document)
    session.commit()
