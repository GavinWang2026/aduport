"""v0.14: DOCX table styles, PDF placeholders, heading numbers, LaTeX/CSV."""

from __future__ import annotations

import zipfile
from io import BytesIO

from aduport.config import settings
from aduport.core.converter import SUPPORTED_TARGET_FORMATS, convert
from aduport.core.document_model import DocumentModel, Heading, Paragraph, Table
from aduport.core.heading_numbers import apply_heading_numbers
from aduport.exporters.csv_exporter import export_csv
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.latex_exporter import export_latex
from aduport.exporters.pdf_exporter import _expand_placeholders, _header_footer_css


def test_docx_table_header_style():
    model = DocumentModel(
        title="T",
        nodes=[Table(rows=[["H1", "H2"], ["a", "b"], ["c", "d"]])],
    )
    data = export_docx(model)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "tblHeader" in xml or "F0F4F7" in xml
    assert "H1" in xml


def test_pdf_header_placeholders():
    expanded = _expand_placeholders("Doc {title} on {date}", title="Report")
    assert "Report" in expanded
    assert "{title}" not in expanded
    css = _header_footer_css(
        header="{title}",
        footer="p{page}",
        page_numbers=False,
        title="MyDoc",
    )
    assert "MyDoc" in css or "counter(page)" in css


def test_heading_numbering():
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="Intro"),
            Heading(level=2, text="A"),
            Heading(level=2, text="B"),
            Heading(level=1, text="Next"),
        ]
    )
    numbered = apply_heading_numbers(model)
    texts = [n.plain_text() for n in numbered.nodes if isinstance(n, Heading)]
    assert texts[0].startswith("1 ")
    assert texts[1].startswith("1.1 ")
    assert texts[2].startswith("1.2 ")
    assert texts[3].startswith("2 ")


def test_heading_numbering_via_convert(monkeypatch):
    monkeypatch.setattr(settings, "heading_numbering", True)
    from aduport.core.cache import result_cache

    result_cache.clear()
    # md→md is identity passthrough; use html to exercise _export numbering
    src = b"# AlphaUnique\n\n## BetaUnique\n\nbody numbering test\n"
    out = convert(src, "md", "html").decode()
    assert "1 AlphaUnique" in out
    assert "1.1 BetaUnique" in out


def test_latex_and_csv_export():
    assert "latex" in SUPPORTED_TARGET_FORMATS and "csv" in SUPPORTED_TARGET_FORMATS
    src = b"# Title\n\nHello **world**.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    tex = convert(src, "md", "latex").decode()
    assert r"\documentclass" in tex and r"\section" in tex
    assert "Hello" in tex
    csv_out = convert(src, "md", "csv").decode()
    assert "a" in csv_out and "1" in csv_out

    model = DocumentModel(nodes=[Paragraph(text="only")])
    assert "only" in export_csv(model)
    assert r"\begin{document}" in export_latex(DocumentModel(title="X", nodes=[]))
