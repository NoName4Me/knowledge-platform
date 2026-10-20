from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context, require_admin
from app.db import get_session
from app.models import Document, DocumentFolder, KnowledgeBase
from app.storage import delete_objects_best_effort
from app.upload_service import mark_deleted, release_quota_once

router = APIRouter(
    prefix="/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}/folders",
    tags=["folders"],
)


class FolderName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200, pattern=r"^[^/\\\x00-\x1f]+$")


class FolderCreate(FolderName):
    parent_id: uuid.UUID | None = None


class FolderMove(BaseModel):
    parent_id: uuid.UUID | None = None


class FolderOut(FolderName):
    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)
    id: uuid.UUID
    knowledge_base_id: uuid.UUID
    parent_id: uuid.UUID | None


def require_kb(session: Session, context: TeamContext, kb_id: uuid.UUID) -> None:
    kb = session.get(KnowledgeBase, kb_id)
    if kb is None or kb.team_id != context.team.id:
        raise HTTPException(404, "知识库不存在")


def get_folder(
    session: Session, context: TeamContext, kb_id: uuid.UUID, folder_id: uuid.UUID
) -> DocumentFolder:
    require_kb(session, context, kb_id)
    folder = session.scalar(
        select(DocumentFolder)
        .where(
            DocumentFolder.id == folder_id,
            DocumentFolder.knowledge_base_id == kb_id,
            DocumentFolder.team_id == context.team.id,
        )
        .with_for_update()
    )
    if folder is None:
        raise HTTPException(404, "文件夹不存在")
    return folder


def require_parent(
    session: Session,
    context: TeamContext,
    kb_id: uuid.UUID,
    parent_id: uuid.UUID | None,
) -> None:
    if parent_id is None:
        return
    if session.scalar(
        select(DocumentFolder.id).where(
            DocumentFolder.id == parent_id,
            DocumentFolder.knowledge_base_id == kb_id,
            DocumentFolder.team_id == context.team.id,
        )
    ) is None:
        raise HTTPException(404, "目标文件夹不存在或不属于当前知识库")


def commit_name(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "同级目录中已存在该名称") from exc


@router.get("", response_model=list[FolderOut])
def list_folders(
    knowledge_base_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> list[FolderOut]:
    require_kb(session, context, knowledge_base_id)
    return [
        FolderOut.model_validate(folder)
        for folder in session.scalars(
            select(DocumentFolder)
            .where(
                DocumentFolder.knowledge_base_id == knowledge_base_id,
                DocumentFolder.team_id == context.team.id,
            )
            .order_by(DocumentFolder.name, DocumentFolder.id)
        )
    ]


@router.post("", response_model=FolderOut, status_code=201)
def create_folder(
    knowledge_base_id: uuid.UUID,
    payload: FolderCreate,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> FolderOut:
    require_kb(session, context, knowledge_base_id)
    require_parent(session, context, knowledge_base_id, payload.parent_id)
    folder = DocumentFolder(
        team_id=context.team.id,
        knowledge_base_id=knowledge_base_id,
        parent_id=payload.parent_id,
        name=payload.name,
    )
    session.add(folder)
    commit_name(session)
    return FolderOut.model_validate(folder)


@router.patch("/{folder_id}", response_model=FolderOut)
def rename_folder(
    knowledge_base_id: uuid.UUID,
    folder_id: uuid.UUID,
    payload: FolderName,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> FolderOut:
    folder = get_folder(session, context, knowledge_base_id, folder_id)
    folder.name = payload.name
    commit_name(session)
    return FolderOut.model_validate(folder)


@router.patch("/{folder_id}/parent", response_model=FolderOut)
def move_folder(
    knowledge_base_id: uuid.UUID,
    folder_id: uuid.UUID,
    payload: FolderMove,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> FolderOut:
    folder = get_folder(session, context, knowledge_base_id, folder_id)
    require_parent(session, context, knowledge_base_id, payload.parent_id)
    if payload.parent_id == folder.id:
        raise HTTPException(409, "不能把文件夹移动到自身")

    # 逐级向上检查，拒绝把目录放进自己的后代，避免形成环。
    cursor = payload.parent_id
    seen: set[uuid.UUID] = set()
    while cursor is not None:
        if cursor == folder.id:
            raise HTTPException(409, "不能把文件夹移动到自身的子目录")
        if cursor in seen:
            raise HTTPException(409, "目录层级中存在循环")
        seen.add(cursor)
        cursor = session.scalar(
            select(DocumentFolder.parent_id).where(DocumentFolder.id == cursor)
        )

    folder.parent_id = payload.parent_id
    commit_name(session)
    return FolderOut.model_validate(folder)


@router.delete("/{folder_id}", status_code=204)
def delete_folder(
    knowledge_base_id: uuid.UUID,
    folder_id: uuid.UUID,
    context: TeamContext = Depends(require_admin),
    session: Session = Depends(get_session),
) -> None:
    folder = get_folder(session, context, knowledge_base_id, folder_id)
    folder_ids = [folder.id]
    pending = [folder.id]
    seen = {folder.id}
    while pending:
        children = list(
            session.scalars(
                select(DocumentFolder)
                .where(
                    DocumentFolder.parent_id.in_(pending),
                    DocumentFolder.knowledge_base_id == knowledge_base_id,
                    DocumentFolder.team_id == context.team.id,
                )
                .with_for_update()
            )
        )
        pending = []
        for child in children:
            if child.id in seen:
                continue
            seen.add(child.id)
            folder_ids.append(child.id)
            pending.append(child.id)

    documents = list(
        session.scalars(
            select(Document)
            .where(
                Document.folder_id.in_(folder_ids),
                Document.knowledge_base_id == knowledge_base_id,
                Document.team_id == context.team.id,
            )
            .with_for_update()
        )
    )
    storage_keys = [document.storage_key for document in documents]
    for document in documents:
        mark_deleted(session, document)
        release_quota_once(session, context.team.id, document)

    # 资料记录保留已删除状态供历史引用核对；先解除目录外键，再让数据库递归级联子目录。
    session.execute(
        update(Document)
        .where(Document.id.in_([document.id for document in documents]))
        .values(folder_id=None)
    )
    session.delete(folder)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(409, "目录删除失败，可能仍有关联数据未清理，请刷新后重试") from exc
    delete_objects_best_effort(storage_keys)
