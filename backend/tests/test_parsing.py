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
    assert "片段" in result.chunks[0].locator_label
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
    assert "第 1 段" in result.chunks[0].locator_label


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


def test_rejects_fake_pdf():
    with pytest.raises(UnsupportedDocument, match="不是有效 PDF"):
        extract_document("fake.pdf", b"not a pdf")


def test_rejects_non_utf8_text():
    with pytest.raises(UnsupportedDocument, match="UTF-8"):
        extract_document("legacy.txt", b"\xff\xfe")


def test_rejects_fake_docx():
    with pytest.raises(UnsupportedDocument, match="不是有效 DOCX"):
        extract_document("fake.docx", b"not a zip")


def test_txt_blocks_keep_paragraph_positions():
    result = extract_document("notes.txt", "第一段\n内容\n\n第二段\n内容".encode())
    assert len(result.chunks) == 2
    assert result.chunks[0].content == "第一段 内容"
    assert "第 1 段" in result.chunks[0].locator_label
    assert "第 2 段" in result.chunks[1].locator_label
