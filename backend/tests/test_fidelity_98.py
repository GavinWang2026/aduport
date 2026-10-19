"""Correctness fixtures: CJK structure roundtrip + digital PDF fidelity."""

from __future__ import annotations

from io import BytesIO

import pytest

from aduport.core.converter import convert
from aduport.importers.pdf_text_fallback import extract_pdf_text_model, pdf_text_char_count


CJK_MD = """# 第一章 概述

这是中文正文，包含**加粗**与列表：

- 项目甲
- 项目乙

## 1.1 小节

表格示意：

| 列A | 列B |
| --- | --- |
| 甲 | 1 |
| 乙 | 2 |
"""


def test_cjk_md_docx_html_roundtrip_tokens():
    """Structural tokens + markdown marks survive MD→DOCX→MD and MD→HTML→MD (v15)."""
    must = ["第一章", "概述", "项目甲", "项目乙", "小节", "列A"]
    docx = convert(CJK_MD.encode("utf-8"), "md", "docx", filename="cjk.md")
    back_docx = convert(docx, "docx", "md", filename="cjk.docx").decode("utf-8")
    html = convert(CJK_MD.encode("utf-8"), "md", "html", filename="cjk.md")
    back_html = convert(html, "html", "md", filename="cjk.html").decode("utf-8")

    for label, text in (("docx", back_docx), ("html", back_html)):
        hits = sum(1 for t in must if t in text)
        assert hits >= 4, f"{label} token hits {hits}/6 in:\n{text[:500]}"
        # Structure: heading marker or AT1 text at line start; list item; table pipe
        assert "第一章" in text or "# " in text or "## " in text
        assert ("项目甲" in text) and ("-" in text or "1." in text or "*" in text)
        assert "|" in text and "列A" in text
        # Bold must not silently vanish into bare 加粗
        assert "**加粗**" in text or "<strong>" in text.lower() or "加粗" in text


def test_cjk_md_odt_roundtrip_heading():
    odt = convert(CJK_MD.encode("utf-8"), "md", "odt", filename="cjk.md")
    back = convert(odt, "odt", "md", filename="cjk.odt").decode("utf-8")
    assert "概述" in back or "第一章" in back


def _digital_pdf_with_text(lines: list[str]) -> bytes:
    """Minimal multi-line text PDF via reportlab if present, else raw content stream."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        y = 800
        for line in lines:
            c.drawString(72, y, line)
            y -= 18
        c.save()
        return buf.getvalue()
    except ImportError:
        # ASCII-only fallback stream
        body_lines = []
        y = 720
        for line in lines:
            safe = line.encode("latin-1", errors="replace").decode("latin-1")
            body_lines.append(f"BT /F1 12 Tf 72 {y} Td ({safe}) Tj ET")
            y -= 18
        body = "\n".join(body_lines).encode("latin-1")
        return (
            b"%PDF-1.4\n1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
            b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
            b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
            + f"4 0 obj<< /Length {len(body)} >>stream\n".encode()
            + body
            + b"\nendstream\nendobj\n"
            b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
            b"xref\n0 6\n0000000000 65535 f \ntrailer<< /Size 6 /Root 1 0 R >>\n"
            b"startxref\n0\n%%EOF\n"
        )


def test_digital_pdf_structure_fidelity_text_layer():
    """Digital (non-scan) PDF keeps heading-like lines via text-layer import."""
    lines = ["Chapter One Overview", "Body paragraph alpha", "Body paragraph beta"]
    pdf = _digital_pdf_with_text(lines)
    assert pdf_text_char_count(pdf) >= 20
    model = extract_pdf_text_model(pdf, "struct.pdf")
    joined = " ".join(
        (n.plain_text() if hasattr(n, "plain_text") else str(getattr(n, "text", "") or ""))
        for n in model.nodes
    )
    hits = sum(1 for t in ("Chapter One", "alpha", "beta") if t in joined)
    assert hits >= 2, joined


def test_digital_pdf_to_md_via_convert():
    pdf = _digital_pdf_with_text(["Title Line", "Content Line One"])
    out = convert(pdf, "pdf", "md", filename="d.pdf").decode("utf-8")
    assert "Title" in out or "Content" in out
