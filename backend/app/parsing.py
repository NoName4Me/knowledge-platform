from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

from docx import Document as DocxDocument
from pypdf import PdfReader

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".txt", ".md")

CHUNK_CHAR_LIMIT = 1200
CHUNK_OVERLAP = 150
MAX_EXTRACTED_CHARS = 2_000_000
MAX_CHUNKS = 5_000
DOCX_MAX_EXPANDED_BYTES = 100 * 1024 * 1024
DOCX_MAX_ENTRIES = 10_000
DOCX_MAX_COMPRESSION_RATIO = 100


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


def _normalize_plain_text(text: str) -> str:
    # 保留段落边界，避免把两个独立段落拼成同一引用位置。
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _extract_pdf(data: bytes) -> list[ExtractedChunk]:
    if not data.lstrip().startswith(b"%PDF-"):
        raise UnsupportedDocument("文件声明为 PDF，但内容不是有效 PDF")
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise UnsupportedDocument("PDF 已加密，无法提取文字")
    chunks: list[ExtractedChunk] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for chunk in _split_text(text, "片段"):
            # 与纯文本、Markdown 保持一致的“位置 · 片段序号”读法，
            # 避免出现“第 1 页 · 第 1 页 1”这类重复前缀。
            chunk.locator_label = f"第 {page_number} 页 · {chunk.locator_label}"
            chunks.append(chunk)
    return chunks


def _extract_docx(data: bytes) -> list[ExtractedChunk]:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > DOCX_MAX_ENTRIES:
                raise UnsupportedDocument("DOCX 内部条目过多")
            total_size = sum(info.file_size for info in infos)
            if total_size > DOCX_MAX_EXPANDED_BYTES:
                raise UnsupportedDocument("DOCX 解压后体积超限")
            if any(
                info.compress_size > 0
                and info.file_size / info.compress_size > DOCX_MAX_COMPRESSION_RATIO
                for info in infos
            ):
                raise UnsupportedDocument("DOCX 压缩比异常")
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocument("文件声明为 DOCX，但内容不是有效 DOCX") from exc
    document = DocxDocument(io.BytesIO(data))
    chunks: list[ExtractedChunk] = []
    for block, paragraph in enumerate(document.paragraphs, start=1):
        for chunk in _split_text(paragraph.text, "段落"):
            chunk.locator_label = f"第 {block} 段 · {chunk.locator_label}"
            chunks.append(chunk)
    return chunks


def _extract_plain(data: bytes) -> list[ExtractedChunk]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedDocument("文本文件不是 UTF-8 编码") from exc
    blocks = [block for block in re.split(r"\n\s*\n", _normalize_plain_text(text)) if block]
    chunks: list[ExtractedChunk] = []
    for block_number, block in enumerate(blocks, start=1):
        for chunk in _split_text(block, "片段"):
            chunk.locator_label = f"第 {block_number} 段 · {chunk.locator_label}"
            chunks.append(chunk)
    return chunks


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
    if sum(len(chunk.content) for chunk in chunks) > MAX_EXTRACTED_CHARS:
        raise UnsupportedDocument("提取文字超过 200 万字符限制")
    if len(chunks) > MAX_CHUNKS:
        raise UnsupportedDocument("资料片段超过 5000 个限制")

    title = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return ExtractedDocument(chunks=chunks, title=title)
