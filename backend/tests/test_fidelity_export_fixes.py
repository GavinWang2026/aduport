"""Fidelity fixes: RTF rich/multiline, DOCX numPr lists, pdf_simple soft-wrap."""

from __future__ import annotations

import io
import zipfile

from app.core.document_model import (
    CodeBlock,
    DocumentModel,
    Heading,
    HtmlBlock,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    MathBlock,
    Paragraph,
    RichText,
    Table,
)
from app.exporters import docx_exporter, rtf_exporter
from app.exporters.pdf_simple import _wrap_text, export_pdf_simple


def test_rtf_codeblock_uses_line_breaks():
    model = DocumentModel(
        nodes=[CodeBlock(content="line1\nline2\nline3", lang="py")]
    )
    out = rtf_exporter.export_rtf(model).decode("utf-8", errors="replace")
    assert r"\line" in out
    assert "line1" in out and "line2" in out and "line3" in out


def test_rtf_rich_link_bold_and_image_math():
    rich = RichText(
        text="see docs here",
        marks=[
            InlineMark(type=InlineMarkType.BOLD, start=0, end=3),
            InlineMark(type=InlineMarkType.LINK, start=4, end=8, href="https://example.com"),
        ],
    )
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="Title H1"),
            Paragraph(text=rich),
            MathBlock(content="x^2", display=True),
            Image(alt_text="chart", data=b"\x89PNG", mime_type="image/png"),
            HtmlBlock(content="<p>raw html body</p>"),
            ListItem(text="alpha", ordered=True, level=0),
            ListItem(text="beta", ordered=True, level=0),
            Table(rows=[["A", "B"], ["1", "2"]]),
        ]
    )
    out = rtf_exporter.export_rtf(model).decode("utf-8", errors="replace")
    assert r"\b" in out and "Title H1" in out
    assert "HYPERLINK" in out and "example.com" in out
    assert "x^2" in out
    assert "[Image: chart]" in out
    assert "raw html body" in out
    assert "1. " in out and "2. " in out
    assert "A" in out and "B" in out


def test_docx_ordered_list_emits_numpr():
    model = DocumentModel(
        nodes=[
            ListItem(text="one", ordered=True, level=0),
            ListItem(text="two", ordered=True, level=0),
            ListItem(text="nested", ordered=False, level=0),
        ]
    )
    blob = docx_exporter.export_docx(model)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
        assert "w:numPr" in xml
        assert "w:numId" in xml
        assert "one" in xml and "two" in xml
        # Must not rely solely on literal "1. " fake numbering for these items
        assert xml.count("w:numPr") >= 2


def test_pdf_simple_soft_wrap_keeps_long_text():
    long = "word " * 80  # ~400 chars
    model = DocumentModel(nodes=[Paragraph(text=long.strip())])
    blob = export_pdf_simple(model)
    assert blob.startswith(b"%PDF")
    assert b"mdbridge-pdf-degraded: true" in blob
    assert b"mdbridge-pdf-engine: pdf_simple" in blob
    # Soft-wrap keeps content; hard truncate would leave ~110 chars only.
    text = blob.decode("latin-1", errors="ignore")
    assert text.count("word") >= 40


def test_pdf_simple_wrap_helper():
    lines = _wrap_text("a " * 50, width=20)
    assert len(lines) > 1
    assert "".join(lines).replace(" ", "").count("a") == 50


def test_pdf_simple_image_and_html_placeholder():
    model = DocumentModel(
        nodes=[
            Image(alt_text="logo", data=b"abc", mime_type="image/png"),
            HtmlBlock(content="<div>HelloBlock</div>"),
            ListItem(text="first", ordered=True),
            ListItem(text="second", ordered=True),
        ]
    )
    blob = export_pdf_simple(model)
    text = blob.decode("latin-1", errors="ignore")
    assert "Image: logo" in text or "logo" in text
    assert "HelloBlock" in text
    assert "1." in text and "2." in text
