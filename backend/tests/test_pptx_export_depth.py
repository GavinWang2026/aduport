"""PPTX export depth: real tables + embedded images."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

from aduport.core.document_model import (
    DocumentModel,
    Heading,
    Image,
    Paragraph,
    RichText,
    Table,
)
from aduport.exporters.pptx_exporter import export_pptx


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


def _slide_texts(blob: bytes) -> list[list[str]]:
    slides: list[tuple[int, list[str]]] = []
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        for name in zf.namelist():
            match = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if not match:
                continue
            xml = zf.read(name).decode("utf-8")
            slides.append((int(match.group(1)), re.findall(r"<a:t[^>]*>([^<]*)</a:t>", xml)))
    return [texts for _, texts in sorted(slides)]


def test_title_slide_is_not_repeated_on_the_next_slide():
    from aduport.importers.md_importer import import_md

    seed = """# ALPHA 2026 报告

BRAVO 这是一个用于校验的段落，包含 31415 这个数字。

## CHARLIE 数据

正文还在。
"""
    slides = _slide_texts(export_pptx(import_md(seed.encode(), "seed.md")))
    assert slides[0] == ["ALPHA 2026 报告"] or slides[0][:1] == ["ALPHA 2026 报告"]
    second = "".join(slides[1])
    assert "ALPHA 2026 报告" not in second
    assert "BRAVO" in second
    assert any("CHARLIE" in "".join(texts) for texts in slides[2:])


def test_strip_running_headers():
    from aduport.importers.pdf_text_fallback import _strip_running_headers_footers

    pages = [
        "公司页眉\n正文甲第一段\n1",
        "公司页眉\n正文乙第二段\n2",
        "公司页眉\n正文丙第三段\n3",
    ]
    out = _strip_running_headers_footers(pages)
    assert all("公司页眉" not in p for p in out)
    assert "正文甲" in out[0]
