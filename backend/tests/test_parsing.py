from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

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


def test_extract_html_body_without_scripts_or_style():
    result = extract_document(
        "page.html",
        (
            b"<html><head><title>Example</title><style>.hidden{display:none}</style>"
            b"<script>alert('not content')</script></head>"
            b"<body><h1>Guide</h1><p>First paragraph.</p>"
            b"<div>Second paragraph.</div></body></html>"
        ),
    )
    text = "\n".join(chunk.content for chunk in result.chunks)
    assert "Guide" in text
    assert "First paragraph." in text
    assert "Second paragraph." in text
    assert "not content" not in text
    assert "display:none" not in text


def test_extract_legacy_doc_via_converter(monkeypatch, tmp_path):
    source = tmp_path / "source.docx"
    document = DocxDocument()
    document.add_paragraph("旧版 Word 转换后的正文")
    document.save(source)
    converter = tmp_path / "fake-soffice"
    converter.write_text("")
    converter.chmod(0o755)

    def fake_converter(command, **_kwargs):
        assert command[0] == str(converter)
        assert "--headless" in command
        outdir = Path(command[command.index("--outdir") + 1])
        shutil.copyfile(source, outdir / "source.docx")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("app.parsing.subprocess.run", fake_converter)
    result = extract_document(
        "legacy.doc",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy",
        legacy_doc_converter=str(converter),
        legacy_doc_timeout=5,
    )
    assert "旧版 Word 转换后的正文" in result.chunks[0].content


def test_legacy_doc_reports_missing_converter(monkeypatch):
    monkeypatch.setattr("app.parsing.shutil.which", lambda _command: None)
    with pytest.raises(UnsupportedDocument, match="LibreOffice"):
        extract_document(
            "legacy.doc",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy",
            legacy_doc_converter="missing-soffice",
        )


def test_extract_docx_paragraphs():
    buffer = io.BytesIO()
    document = DocxDocument()
    document.add_paragraph("第一段内容")
    document.add_paragraph("第二段内容")
    document.save(buffer)
    result = extract_document("report.docx", buffer.getvalue())
    assert "第一段内容" in result.chunks[0].content
    assert "第 1 段" in result.chunks[0].locator_label


def test_extract_docx_table_cells_with_readable_locator():
    buffer = io.BytesIO()
    document = DocxDocument()
    document.add_paragraph("费用标准")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "标准"
    table.cell(1, 0).text = "住宿费"
    table.cell(1, 1).text = "每晚 600 元"
    document.save(buffer)

    result = extract_document("standards.docx", buffer.getvalue())

    table_chunk = next(chunk for chunk in result.chunks if "住宿费" in chunk.content)
    assert table_chunk.content == "项目：住宿费；标准：每晚 600 元"
    assert "第 1 张表" in table_chunk.locator_label
    assert "第 2 行" in table_chunk.locator_label
    assert table_chunk.table_number == 1
    assert table_chunk.table_row == 2
    assert table_chunk.table_headers == ["项目", "标准"]
    assert table_chunk.table_cells == ["住宿费", "每晚 600 元"]


def test_docx_table_keeps_empty_cells_and_row_alignment():
    buffer = io.BytesIO()
    document = DocxDocument()
    table = document.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "上限"
    table.cell(0, 2).text = "备注"
    table.cell(1, 0).text = "住宿费"
    table.cell(1, 1).text = "600 元"
    table.cell(2, 0).text = "交通费"
    table.cell(2, 2).text = "凭票"
    document.save(buffer)

    result = extract_document("matrix.docx", buffer.getvalue())
    row = next(chunk for chunk in result.chunks if chunk.table_row == 3)
    assert row.table_headers == ["项目", "上限", "备注"]
    assert row.table_cells == ["交通费", "", "凭票"]


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


def test_docx_paragraph_positions_follow_body_order_around_tables():
    buffer = io.BytesIO()
    document = DocxDocument()
    document.add_paragraph("前置段落")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "值"
    table.cell(1, 0).text = "编号"
    table.cell(1, 1).text = "A-17"
    document.add_paragraph("后置段落")
    document.save(buffer)

    result = extract_document("ordered.docx", buffer.getvalue())

    assert any(
        chunk.content == "前置段落" and "第 1 段" in chunk.locator_label
        for chunk in result.chunks
    )
    assert any(
        chunk.content == "后置段落" and "第 2 段" in chunk.locator_label
        for chunk in result.chunks
    )
