from __future__ import annotations

import io

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter

from app.parsing import UnsupportedDocument, extract_document


def test_extract_plain_text_splits_and_locates():
    long_text = "这是一个测试句子。" * 200
    result = extract_document("note.txt", long_text.encode("utf-8"))
    assert len(result.chunks) > 1
    assert result.chunks[0].locator_label.startswith("片段")
    assert result.chunks[0].content


def test_extract_markdown():
    result = extract_document("doc.md", b"# Title\n\nsome body text")
    assert result.title == "doc"
    assert result.chunks[0].content.startswith("# Title")


def test_extract_docx_paragraphs():
    buffer = io.BytesIO()
    document = DocxDocument()
    document.add_paragraph("第一段内容")
    document.add_paragraph("第二段内容")
    document.save(buffer)
    result = extract_document("report.docx", buffer.getvalue())
    assert "第一段内容" in result.chunks[0].content
    assert result.chunks[0].locator_label.startswith("段落")


def test_extract_pdf_labels_pages():
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(UnsupportedDocument):
        extract_document("scan.pdf", buffer.getvalue())


def test_rejects_unsupported_extension():
    with pytest.raises(UnsupportedDocument):
        extract_document("image.png", b"data")


def test_rejects_empty_text():
    with pytest.raises(UnsupportedDocument):
        extract_document("empty.txt", b"   ")
