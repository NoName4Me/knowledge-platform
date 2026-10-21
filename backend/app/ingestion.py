"""V3 direct ingestion helpers for pasted text and a single public web page."""

from __future__ import annotations

import re
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.analysis import source_fingerprint
from app.models import (
    AnalysisDocument,
    Document,
    DocumentChunk,
    KnowledgeNote,
    Report,
    ReportSource,
)


class IngestionError(Exception):
    def __init__(self, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def fetch_web_page(url: str, max_bytes: int, timeout: float = 20.0) -> tuple[str, bytes, str]:
    """Read one public page; never execute scripts or crawl outgoing links."""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise IngestionError("只支持公开的 http 或 https 网页")
    try:
        with httpx.stream(
            "GET",
            url,
            follow_redirects=True,
            timeout=timeout,
            headers={"User-Agent": "KPlatform/0.1 (+single-page import)"},
        ) as response:
            response.raise_for_status()
            final_url = str(response.url)
            if not re.match(r"^https?://", final_url, re.IGNORECASE):
                raise IngestionError("网页跳转到了不支持的地址")
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(
                marker in content_type
                for marker in ("text/html", "application/xhtml+xml", "text/plain")
            ):
                raise IngestionError("该地址不是可导入的网页正文")
            blocks: list[bytes] = []
            total = 0
            for block in response.iter_bytes():
                total += len(block)
                if total > max_bytes:
                    raise IngestionError("网页正文超过单份资料大小限制", 413)
                blocks.append(block)
            data = b"".join(blocks)
    except httpx.HTTPStatusError as exc:
        raise IngestionError(f"网页返回 {exc.response.status_code}，无法导入", 502) from exc
    except httpx.HTTPError as exc:
        raise IngestionError("网页暂时无法读取，请检查地址或稍后重试", 502) from exc
    if not data:
        raise IngestionError("网页没有可导入的正文")
    return final_url, data, content_type


def web_title(html: bytes, fallback: str) -> str:
    text = html.decode("utf-8", errors="ignore")
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    if not match:
        return fallback[:300]
    value = re.sub(r"\s+", " ", match.group(1)).strip()
    return (value or fallback)[:300]


def safe_filename(title: str, extension: str) -> str:
    stem = re.sub(r"[/\\\x00-\x1f]+", "-", title).strip(" .") or "导入资料"
    return f"{stem[:260]}{extension}"


def _current_fingerprint(session: Session, document_id: uuid.UUID) -> str:
    rows = session.execute(
        select(DocumentChunk.id, DocumentChunk.locator_label, DocumentChunk.content)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.ordinal)
    ).all()
    return source_fingerprint([(row.id, row.locator_label, row.content) for row in rows])


def impact_summary(
    session: Session, document: Document, team_id: uuid.UUID, owner_id: uuid.UUID
) -> dict[str, object]:
    note_count = session.scalar(
        select(func.count(KnowledgeNote.id)).where(
            KnowledgeNote.team_id == team_id,
            KnowledgeNote.created_by_id == owner_id,
            KnowledgeNote.source_id == document.id,
        )
    ) or 0
    analysis_count = session.scalar(
        select(func.count(func.distinct(AnalysisDocument.task_id))).where(
            AnalysisDocument.team_id == team_id,
            AnalysisDocument.owner_id == owner_id,
            AnalysisDocument.document_id == document.id,
        )
    ) or 0
    reports = list(
        session.scalars(
            select(Report)
            .join(ReportSource, ReportSource.report_id == Report.id)
            .where(
                Report.team_id == team_id,
                Report.created_by_id == owner_id,
                ReportSource.source_type == "document",
                ReportSource.source_id == document.id,
            )
        )
    )
    current = _current_fingerprint(session, document.id)
    stale: list[uuid.UUID] = []
    for report in reports:
        source = session.scalar(
            select(ReportSource).where(
                ReportSource.report_id == report.id,
                ReportSource.source_type == "document",
                ReportSource.source_id == document.id,
            )
        )
        if source is not None and source.fingerprint != current:
            stale.append(report.id)
    return {
        "document_id": document.id,
        "document_title": document.title,
        "note_count": note_count,
        "analysis_count": analysis_count,
        "report_count": len(reports),
        "stale_report_ids": stale,
    }


def mark_source_reviews(
    session: Session, document: Document, replacement: Document
) -> list[uuid.UUID]:
    """Flag personal reports that cite a replaced source without rewriting them."""
    report_ids = list(
        session.scalars(
            select(ReportSource.report_id)
            .where(
                ReportSource.team_id == document.team_id,
                ReportSource.source_type == "document",
                ReportSource.source_id == document.id,
            )
            .distinct()
        )
    )
    if not report_ids:
        return []
    note = (
        f"资料《{document.title}》已有新版本（{replacement.filename}）。"
        "请核对报告中的条件、结论和引用，确认后再保存。"
    )[:500]
    reports = list(
        session.scalars(
            select(Report)
            .where(
                Report.team_id == document.team_id,
                Report.id.in_(report_ids),
            )
            .with_for_update()
        )
    )
    for report in reports:
        report.source_review_required = True
        report.source_review_note = note
    session.flush()
    return [report.id for report in reports]
