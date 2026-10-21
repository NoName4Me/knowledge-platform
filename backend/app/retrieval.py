from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
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
    lexical_score: float = 0.0


_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_TERM = re.compile(r"[a-z0-9][a-z0-9._/+:-]*", re.IGNORECASE)
_STOP_CHARS = set("的了吗呢吧啊呀是在和与及或一个多少什么如何哪些哪种应当必须可以是否")
_MAX_RECALL_TERMS = 40


def lexical_terms(value: str) -> set[str]:
    """提取中英文关键词；只用于召回与相关度下限，不替代语义向量。"""
    normalized = value.lower().replace("\n", " ")
    tokens = {token for token in _TERM.findall(normalized) if len(token) > 1}
    for run in _CJK_RUN.findall(normalized):
        tokens.add(run)
        for size in (2, 3, 4):
            tokens.update(
                run[index : index + size]
                for index in range(max(0, len(run) - size + 1))
                if not any(char in _STOP_CHARS for char in run[index : index + size])
            )
    return {token for token in tokens if token.strip() and token not in _STOP_CHARS}


def lexical_score(question: str, content: str) -> float:
    terms = lexical_terms(question)
    if not terms:
        return 0.0
    haystack = content.lower()
    in_text = sum(term in haystack for term in terms)
    # 两三字主题词已足以证明问题与片段存在交集；按全部派生 n-gram
    # 求比例会把短问题稀释，导致明显相关的资料被误判为不足。
    return min(1.0, in_text / 2)


def retrieve(
    session: Session,
    team_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    question: str,
    top_k: int | None = None,
    *,
    candidate_k: int | None = None,
    distance_limit: float | None = None,
    min_lexical_score: float | None = None,
    document_ids: list[uuid.UUID] | None = None,
) -> list[RetrievedChunk]:
    """在当前团队、知识库及可选资料范围内融合向量与独立词项召回。"""
    provider = get_embedding_provider()
    query_vector = provider.embed([question])[0]

    limit = top_k or settings.retrieval_top_k
    candidate_limit = max(
        top_k or settings.retrieval_top_k,
        candidate_k or settings.retrieval_candidate_k,
    )
    threshold = settings.retrieval_distance_limit if distance_limit is None else distance_limit
    lexical_threshold = (
        settings.retrieval_min_lexical_score if min_lexical_score is None else min_lexical_score
    )
    distance = DocumentChunk.embedding.l2_distance(query_vector)
    scope_filters = [
        DocumentChunk.team_id == team_id,
        Document.knowledge_base_id == knowledge_base_id,
        Document.status == "ready",
        Document.deleted_at.is_(None),
    ]
    if document_ids is not None:
        scope_filters.append(Document.id.in_(document_ids))

    vector_stmt = (
        select(DocumentChunk, Document.title, distance)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            *scope_filters,
            DocumentChunk.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(candidate_limit)
    )

    candidates_by_id: dict[uuid.UUID, RetrievedChunk] = {}
    for chunk, title, dist in session.execute(vector_stmt).all():
        dist_value = float(dist)
        content_score = lexical_score(question, chunk.content)
        title_score = lexical_score(question, title)
        score = max(content_score, title_score)
        candidates_by_id[chunk.id] = RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            document_title=title,
            locator_label=chunk.locator_label,
            content=chunk.content,
            distance=dist_value,
            lexical_score=score,
        )

    # 编号、专名和缩写可能并不靠近问题向量的最近邻。词项候选独立查询一次，
    # 再按片段 ID 与向量候选合并，避免“只在最近邻里找关键词”造成的漏召回。
    recall_terms = sorted(lexical_terms(question), key=lambda term: (-len(term), term))[
        :_MAX_RECALL_TERMS
    ]
    if recall_terms:
        patterns = [
            pattern
            for term in recall_terms
            if (pattern := f"%{_escape_like(term)}%")
        ]
        keyword_stmt = (
            select(DocumentChunk, Document.title, distance)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                *scope_filters,
                DocumentChunk.embedding.is_not(None),
                or_(
                    *(DocumentChunk.content.ilike(pattern, escape="\\") for pattern in patterns),
                    *(Document.title.ilike(pattern, escape="\\") for pattern in patterns),
                ),
            )
            .limit(candidate_limit)
        )
        for chunk, title, dist in session.execute(keyword_stmt).all():
            content_score = lexical_score(question, chunk.content)
            title_score = lexical_score(question, title)
            score = max(content_score, title_score)
            previous = candidates_by_id.get(chunk.id)
            if previous is None or score > previous.lexical_score:
                candidates_by_id[chunk.id] = RetrievedChunk(
                    chunk_id=chunk.id,
                    document_id=chunk.document_id,
                    document_title=title,
                    locator_label=chunk.locator_label,
                    content=chunk.content,
                    distance=float(dist),
                    lexical_score=score,
                )

    # 词项命中与向量相似度共同决定是否有可用证据；全库总会有“最近邻”，
    # 但不代表问题可回答。至少一个信号达标才把候选送入生成模型。
    relevant = [
        chunk
        for chunk in candidates_by_id.values()
        if chunk.lexical_score >= lexical_threshold or chunk.distance <= threshold
    ]
    relevant.sort(
        key=lambda chunk: (
            -(chunk.lexical_score * 2.0 + max(0.0, 1.8 - chunk.distance)),
            chunk.distance,
        )
    )
    return relevant[:limit]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
