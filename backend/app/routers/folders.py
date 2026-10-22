from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import TeamContext, get_team_context
from app.db import apply_context, get_session
from app.knowledge_base_access import require_knowledge_base, require_knowledge_base_permission
from app.models import Document, DocumentFolder
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
    sort_order: int


class TreeReorderItem(BaseModel):
    kind: Literal["folder", "document"]
    id: uuid.UUID


class TreeReorder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_id: uuid.UUID | None = None
    items: list[TreeReorderItem] = Field(min_length=1, max_length=1000)
    # 拖拽时可在同一个事务里移动并重新排序；仅调整顺序时省略。
    moving: TreeReorderItem | None = None


def require_kb(session: Session, context: TeamContext, kb_id: uuid.UUID) -> None:
    require_knowledge_base(session, context, kb_id)


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
            .order_by(DocumentFolder.sort_order, DocumentFolder.name, DocumentFolder.id)
        )
    ]


@router.post("", response_model=FolderOut, status_code=201)
def create_folder(
    knowledge_base_id: uuid.UUID,
    payload: FolderCreate,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> FolderOut:
    require_knowledge_base_permission(session, context, knowledge_base_id, "manage")
    require_parent(session, context, knowledge_base_id, payload.parent_id)
    next_order = session.scalar(
        select(func.coalesce(func.max(DocumentFolder.sort_order), -1) + 1).where(
            DocumentFolder.knowledge_base_id == knowledge_base_id,
            DocumentFolder.team_id == context.team.id,
            DocumentFolder.parent_id.is_(None)
            if payload.parent_id is None
            else DocumentFolder.parent_id == payload.parent_id,
        )
    )
    folder = DocumentFolder(
        team_id=context.team.id,
        knowledge_base_id=knowledge_base_id,
        parent_id=payload.parent_id,
        name=payload.name,
        sort_order=next_order or 0,
    )
    session.add(folder)
    commit_name(session)
    return FolderOut.model_validate(folder)


def reorder_scope(
    session: Session,
    context: TeamContext,
    knowledge_base_id: uuid.UUID,
    payload: TreeReorder,
) -> list[FolderOut]:
    """Persist one sibling's complete order; folders and files share one list."""
    require_kb(session, context, knowledge_base_id)
    require_parent(session, context, knowledge_base_id, payload.parent_id)
    duplicates = len({(item.kind, item.id) for item in payload.items}) != len(payload.items)
    if duplicates:
        raise HTTPException(422, "排序列表中存在重复项目")
    folder_stmt = select(DocumentFolder).where(
        DocumentFolder.knowledge_base_id == knowledge_base_id,
        DocumentFolder.team_id == context.team.id,
        DocumentFolder.parent_id.is_(None)
        if payload.parent_id is None
        else DocumentFolder.parent_id == payload.parent_id,
    ).with_for_update()
    document_stmt = select(Document).where(
        Document.knowledge_base_id == knowledge_base_id,
        Document.team_id == context.team.id,
        Document.deleted_at.is_(None),
        Document.folder_id.is_(None)
        if payload.parent_id is None
        else Document.folder_id == payload.parent_id,
    ).with_for_update()
    folders = list(session.scalars(folder_stmt))
    documents = list(session.scalars(document_stmt))
    requested_folders = {item.id for item in payload.items if item.kind == "folder"}
    requested_documents = {item.id for item in payload.items if item.kind == "document"}
    expected_folders = {item.id for item in folders}
    expected_documents = {item.id for item in documents}
    if payload.moving is not None:
        if payload.moving.kind == "folder":
            if payload.moving.id not in expected_folders:
                expected_folders.add(payload.moving.id)
                requested_folders.add(payload.moving.id)
        elif payload.moving.id not in expected_documents:
            expected_documents.add(payload.moving.id)
            requested_documents.add(payload.moving.id)
    if requested_folders != expected_folders or requested_documents != expected_documents:
        raise HTTPException(409, "排序列表与当前目录内容不一致，请刷新后重试")
    folder_by_id = {folder.id: folder for folder in folders}
    document_by_id = {document.id: document for document in documents}
    moving_folder: DocumentFolder | None = None
    moving_document: Document | None = None
    if payload.moving is not None:
        if payload.moving.kind == "folder":
            moving_folder = session.scalar(
                select(DocumentFolder).where(
                    DocumentFolder.id == payload.moving.id,
                    DocumentFolder.knowledge_base_id == knowledge_base_id,
                    DocumentFolder.team_id == context.team.id,
                ).with_for_update()
            )
            if moving_folder is None:
                raise HTTPException(404, "要移动的目录不存在")
            cursor = payload.parent_id
            while cursor is not None:
                if cursor == moving_folder.id:
                    raise HTTPException(409, "不能把目录移动到自身的子目录")
                cursor = session.scalar(
                    select(DocumentFolder.parent_id).where(
                        DocumentFolder.id == cursor,
                        DocumentFolder.knowledge_base_id == knowledge_base_id,
                        DocumentFolder.team_id == context.team.id,
                    )
                )
            folder_by_id[moving_folder.id] = moving_folder
        else:
            moving_document = session.scalar(
                select(Document).where(
                    Document.id == payload.moving.id,
                    Document.knowledge_base_id == knowledge_base_id,
                    Document.team_id == context.team.id,
                    Document.deleted_at.is_(None),
                ).with_for_update()
            )
            if moving_document is None:
                raise HTTPException(404, "要移动的资料不存在")
            document_by_id[moving_document.id] = moving_document
    for ordinal, item in enumerate(payload.items):
        if item.kind == "folder":
            folder_by_id[item.id].sort_order = ordinal
        else:
            document_by_id[item.id].sort_order = ordinal
    if moving_folder is not None:
        moving_folder.parent_id = payload.parent_id
    if moving_document is not None:
        moving_document.folder_id = payload.parent_id
    session.commit()
    # commit 会清除事务级 RLS 上下文；若直接回读，目录会被过滤为空。
    apply_context(
        session,
        user_id=context.user.id,
        team_id=context.team.id,
    )
    return [
        FolderOut.model_validate(folder)
        for folder in session.scalars(
            select(DocumentFolder)
            .where(
                DocumentFolder.knowledge_base_id == knowledge_base_id,
                DocumentFolder.team_id == context.team.id,
            )
            .order_by(DocumentFolder.sort_order, DocumentFolder.name, DocumentFolder.id)
        )
    ]


@router.post("/reorder", response_model=list[FolderOut])
def reorder_tree(
    knowledge_base_id: uuid.UUID,
    payload: TreeReorder,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> list[FolderOut]:
    """Persist one sibling's complete order; mixed folders and documents share a list."""
    require_knowledge_base_permission(session, context, knowledge_base_id, "manage")
    return reorder_scope(session, context, knowledge_base_id, payload)


@router.patch("/{folder_id}", response_model=FolderOut)
def rename_folder(
    knowledge_base_id: uuid.UUID,
    folder_id: uuid.UUID,
    payload: FolderName,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> FolderOut:
    require_knowledge_base_permission(session, context, knowledge_base_id, "manage")
    folder = get_folder(session, context, knowledge_base_id, folder_id)
    folder.name = payload.name
    commit_name(session)
    return FolderOut.model_validate(folder)


@router.patch("/{folder_id}/parent", response_model=FolderOut)
def move_folder(
    knowledge_base_id: uuid.UUID,
    folder_id: uuid.UUID,
    payload: FolderMove,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> FolderOut:
    require_knowledge_base_permission(session, context, knowledge_base_id, "manage")
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

    maximum = session.scalar(
        select(func.coalesce(func.max(DocumentFolder.sort_order), -1) + 1).where(
            DocumentFolder.knowledge_base_id == knowledge_base_id,
            DocumentFolder.team_id == context.team.id,
            DocumentFolder.parent_id.is_(None)
            if payload.parent_id is None
            else DocumentFolder.parent_id == payload.parent_id,
        )
    )
    folder.parent_id = payload.parent_id
    folder.sort_order = maximum or 0
    commit_name(session)
    return FolderOut.model_validate(folder)


@router.delete("/{folder_id}", status_code=204)
def delete_folder(
    knowledge_base_id: uuid.UUID,
    folder_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    require_knowledge_base_permission(session, context, knowledge_base_id, "manage")
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
