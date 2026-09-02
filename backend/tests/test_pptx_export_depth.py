"""PPTX export depth: real tables + embedded images."""

from __future__ import annotations

from app.core.document_model import (
    DocumentModel,
    Heading,
    Image,
    Paragraph,
    RichText,
    Table,
)
from app.exporters.pptx_exporter import export_pptx


def test_pptx_export_embeds_table_and_image():
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f"
        b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    model = DocumentModel(
        title="Deck",
        nodes=[
            Heading(level=1, text=RichText(text="Overview")),
            Paragraph(text=RichText(text="Intro line")),
            Table(rows=[["A", "B"], ["1", "2"]], header_rows=1),
            Image(data=png, alt_text="pixel", mime_type="image/png"),
        ],
    )
    data = export_pptx(model)
    assert data[:2] == b"PK"
    assert len(data) > 2000


def test_strip_running_headers():
    from app.importers.pdf_text_fallback import _strip_running_headers_footers

    pages = [
        "公司页眉\n正文甲第一段\n1",
        "公司页眉\n正文乙第二段\n2",
        "公司页眉\n正文丙第三段\n3",
    ]
    out = _strip_running_headers_footers(pages)
    assert all("公司页眉" not in p for p in out)
    assert "正文甲" in out[0]
