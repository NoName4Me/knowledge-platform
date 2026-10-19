from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.embeddings import get_embedding_provider
from app.models import Document, DocumentChunk

settings = get_settings()


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    locator_label: str
    content: str
    distance: float


def retrieve(
    session: Session,
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    question: str,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """在当前团队与知识库范围内按向量相似度检索，先限定范围再排序。"""
    provider = get_embedding_provider()
    query_vector = provider.embed([question])[0]

    limit = top_k or settings.retrieval_top_k
    distance = DocumentChunk.embedding.l2_distance(query_vector)
    stmt = (
        select(DocumentChunk, Document.title, distance)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.team_id == team_id,
            Document.knowledge_base_id == knowledge_base_id,
            Document.status == "ready",
            Document.deleted_at.is_(None),
            DocumentChunk.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(limit)
    )

    results: list[RetrievedChunk] = []
    for chunk, title, dist in session.execute(stmt).all():
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                document_title=title,
                locator_label=chunk.locator_label,
                content=chunk.content,
                distance=float(dist),
            )
        )
    return results
