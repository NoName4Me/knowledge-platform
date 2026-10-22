from __future__ import annotations

import uuid
from typing import NoReturn

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app import automation
from app.auth import TeamContext, get_team_context
from app.db import get_session
from app.schemas import (
    AutomationCreateRequest,
    AutomationDetailOut,
    AutomationListOut,
    AutomationOut,
)

router = APIRouter(
    prefix="/api/teams/{team_id}/knowledge-bases/{knowledge_base_id}/automations",
    tags=["automations"],
)


def _raise(exc: automation.AutomationError) -> NoReturn:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.detail},
    ) from exc


@router.get("", response_model=AutomationListOut)
def list_automations(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> AutomationListOut:
    try:
        return AutomationListOut(
            items=automation.list_automations(session, context, knowledge_base_id)
        )
    except automation.AutomationError as exc:
        _raise(exc)
    raise AssertionError("unreachable")


@router.post("", response_model=AutomationOut, status_code=status.HTTP_202_ACCEPTED)
def create_automation(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    payload: AutomationCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> Response:
    if idempotency_key is None:
        raise HTTPException(status_code=422, detail="缺少 Idempotency-Key")
    try:
        key = uuid.UUID(idempotency_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Idempotency-Key 必须是 UUID") from exc
    try:
        item, created = automation.create_automation(
            session,
            context,
            knowledge_base_id=knowledge_base_id,
            idempotency_key=key,
            payload=payload,
        )
    except automation.AutomationError as exc:
        session.rollback()
        _raise(exc)
    session.commit()
    body = AutomationOut.model_validate(automation._automation_out(item)).model_dump_json()
    return Response(
        content=body,
        status_code=202 if created else 200,
        media_type="application/json",
    )


@router.get("/{automation_id}", response_model=AutomationDetailOut)
def get_automation(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    automation_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> AutomationDetailOut:
    try:
        return automation.get_automation(
            session,
            context,
            knowledge_base_id=knowledge_base_id,
            automation_id=automation_id,
        )
    except automation.AutomationError as exc:
        _raise(exc)
    raise AssertionError("unreachable")


@router.post("/{automation_id}/cancel", response_model=AutomationOut)
def cancel_automation(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    automation_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> AutomationOut:
    try:
        item = automation.request_cancel(
            session,
            context,
            knowledge_base_id=knowledge_base_id,
            automation_id=automation_id,
        )
    except automation.AutomationError as exc:
        session.rollback()
        _raise(exc)
    session.commit()
    return item


@router.post("/{automation_id}/continue", response_model=AutomationOut)
def continue_automation(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    automation_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> AutomationOut:
    try:
        item = automation.request_continue(
            session,
            context,
            knowledge_base_id=knowledge_base_id,
            automation_id=automation_id,
        )
    except automation.AutomationError as exc:
        session.rollback()
        _raise(exc)
    session.commit()
    return item


@router.delete("/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_automation(
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    automation_id: uuid.UUID,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> None:
    try:
        automation.delete_automation(
            session,
            context,
            knowledge_base_id=knowledge_base_id,
            automation_id=automation_id,
        )
    except automation.AutomationError as exc:
        session.rollback()
        _raise(exc)
    session.commit()
