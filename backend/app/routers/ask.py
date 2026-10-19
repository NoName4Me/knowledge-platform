from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.answering import get_answer_provider
from app.auth import TeamContext, get_team_context
from app.config import get_settings
from app.db import get_session
from app.models import KnowledgeBase
from app.ratelimit import FixedWindowLimiter, enforce_rate_limit
from app.retrieval import retrieve
from app.schemas import AskRequest, AskResponse, Citation

router = APIRouter(prefix="/api/teams/{team_id}/ask", tags=["ask"])
settings = get_settings()
ask_limiter = FixedWindowLimiter(
    settings.ask_rate_limit, settings.ask_rate_window_seconds
)


@router.post("", response_model=AskResponse)
def ask(
    team_id: uuid.UUID,
    payload: AskRequest,
    context: TeamContext = Depends(get_team_context),
    session: Session = Depends(get_session),
) -> AskResponse:
    enforce_rate_limit(ask_limiter, str(context.user.id))
    kb = session.get(KnowledgeBase, payload.knowledge_base_id)
    if kb is None or kb.team_id != context.team.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="知识库不存在")

    chunks = retrieve(
        session,
        team_id=context.team.id,
        knowledge_base_id=payload.knowledge_base_id,
        question=payload.question,
    )
    result = get_answer_provider().answer(payload.question, chunks)

    by_id = {str(chunk.chunk_id): chunk for chunk in chunks}
    citations = [
        Citation(
            document_id=by_id[chunk_id].document_id,
            document_title=by_id[chunk_id].document_title,
            locator_label=by_id[chunk_id].locator_label,
            excerpt=by_id[chunk_id].content[:400],
        )
        for chunk_id in result.used_chunk_ids
        if chunk_id in by_id
    ]

    return AskResponse(answer=result.answer, grounded=bool(citations), citations=citations)
