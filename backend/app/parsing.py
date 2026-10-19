from __future__ import annotations

import io
import re
from dataclasses import dataclass

from docx import Document as DocxDocument
from pypdf import PdfReader

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")

CHUNK_CHAR_LIMIT = 1200
CHUNK_OVERLAP = 150


class UnsupportedDocument(Exception):
    pass


@dataclass
class ExtractedChunk:
    content: str
    locator_label: str
    locator_start: int | None = None
    locator_end: int | None = None


@dataclass
class ExtractedDocument:
    chunks: list[ExtractedChunk]
    title: str


def extension_of(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def _split_text(text: str, label_prefix: str) -> list[ExtractedChunk]:
    normalized = re.sub(r"[ \t\r\n]+", " ", text).strip()
    if not normalized:
        return []

    chunks: list[ExtractedChunk] = []
    start = 0
    ordinal = 0
    while start < len(normalized):
        end = min(start + CHUNK_CHAR_LIMIT, len(normalized))
        piece = normalized[start:end].strip()
        if piece:
            ordinal += 1
            chunks.append(
                ExtractedChunk(
                    content=piece,
                    locator_label=f"{label_prefix} {ordinal}",
                    locator_start=start,
                    locator_end=end,
                )
            )
        if end >= len(normalized):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _extract_pdf(data: bytes) -> list[ExtractedChunk]:
    reader = PdfReader(io.BytesIO(data))
    chunks: list[ExtractedChunk] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for chunk in _split_text(text, f"第 {page_number} 页"):
            chunk.locator_label = f"第 {page_number} 页 · {chunk.locator_label}"
            chunks.append(chunk)
    return chunks


def _extract_docx(data: bytes) -> list[ExtractedChunk]:
    document = DocxDocument(io.BytesIO(data))
    text = "\n".join(p.text for p in document.paragraphs)
    return _split_text(text, "段落")


def _extract_plain(data: bytes) -> list[ExtractedChunk]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    return _split_text(text, "片段")


def extract_document(filename: str, data: bytes) -> ExtractedDocument:
    extension = extension_of(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedDocument(
            f"暂不支持 {extension or '该'} 格式，当前支持 PDF、DOCX、TXT、Markdown"
        )

    if extension == ".pdf":
        chunks = _extract_pdf(data)
    elif extension == ".docx":
        chunks = _extract_docx(data)
    else:
        chunks = _extract_plain(data)

    if not chunks:
        raise UnsupportedDocument("没有提取到可检索的文字，可能是扫描件或空白文件")

    title = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return ExtractedDocument(chunks=chunks, title=title)
