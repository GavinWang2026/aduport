"""Markdown importer / exporter roundtrip and security unit tests."""

from __future__ import annotations

import base64

import pytest

from app.api.routes import _safe_output_filename
from app.core.css_sanitize import sanitize_custom_css, sanitize_font_family
from app.core.document_model import Image, InlineMarkType, Table
from app.core.converter import convert
from app.exporters.md_exporter import export_md
from app.importers.md_importer import import_md


def test_import_table_preserved():
    md = b"| a | b | c |\n|---|---|---|\n| 1 | 2 | 3 |\n"
    model = import_md(md)
    tables = [n for n in model.nodes if isinstance(n, Table)]
    assert len(tables) == 1
    assert tables[0].rows[0] == ["a", "b", "c"]
    assert tables[0].rows[1] == ["1", "2", "3"]


def test_bold_italic_link_no_duplication():
    md = "这是一个 **Markdown** 与 *斜体* 和 [链接](https://example.com) 示例\n".encode("utf-8")
    model = import_md(md)
    paras = [n for n in model.nodes if hasattr(n, "plain_text")]
    assert paras
    text = paras[0].plain_text()
    assert text.count("Markdown") == 1
    assert "斜体" in text
    assert "链接" in text
    from app.core.document_model import Paragraph, ensure_rich_text

    rich = ensure_rich_text(paras[0].text)  # type: ignore[attr-defined]
    types = {m.type for m in rich.marks}
    assert InlineMarkType.BOLD in types
    assert InlineMarkType.ITALIC in types
    assert InlineMarkType.LINK in types
    link = next(m for m in rich.marks if m.type == InlineMarkType.LINK)
    assert link.href == "https://example.com"


def test_inline_code_and_strikethrough():
    md = b"use `code` and ~~gone~~\n"
    model = import_md(md)
    from app.core.document_model import Paragraph, ensure_rich_text

    para = next(n for n in model.nodes if isinstance(n, Paragraph))
    rich = ensure_rich_text(para.text)
    types = {m.type for m in rich.marks}
    assert InlineMarkType.CODE in types
    assert InlineMarkType.STRIKETHROUGH in types
    assert "code" in rich.text
    assert "gone" in rich.text


def test_image_data_uri_and_remote_placeholder():
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()
    md = (
        f"before ![a](data:image/png;base64,{png}) mid "
        f"![b](https://example.com/x.png) after\n"
    ).encode()
    model = import_md(md)
    images = [n for n in model.nodes if isinstance(n, Image)]
    assert len(images) >= 2
    assert images[0].data.startswith(b"\x89PNG")
    assert "example.com" in images[1].alt_text


def test_nested_list_levels():
    md = b"- item1\n  - nested1\n- item2\n"
    model = import_md(md)
    from app.core.document_model import ListItem

    items = [n for n in model.nodes if isinstance(n, ListItem)]
    assert len(items) >= 2
    levels = {i.level for i in items}
    assert 0 in levels
    assert 1 in levels


def test_md_roundtrip_table_and_bold():
    src = (
        "# Title\n\n"
        "Hello **world** and [site](https://a.example) then [other](https://b.example).\n\n"
        "| Name | Age |\n| --- | --- |\n| Alice | 30 |\n"
    ).encode()
    out = convert(src, "md", "md")
    text = out.decode("utf-8")
    assert "| Name" in text or "| Name " in text
    assert "Alice" in text
    assert "**world**" in text or "world" in text
    assert "https://a.example" in text
    assert "https://b.example" in text


def test_zip_slip_sanitized():
    assert ".." not in _safe_output_filename("../../evil.md", "md")
    assert "\\" not in _safe_output_filename("..\\..\\evil.md", "md")
    name = _safe_output_filename("../../evil.md", "md")
    assert "/" not in name
    assert name.endswith(".md")


def test_css_ssrf_stripped():
    dirty = "} @import url('http://169.254.169.254/latest'); body{color:red}"
    clean = sanitize_custom_css(dirty)
    assert "@import" not in clean.lower() or "blocked" in clean.lower()
    assert "169.254" not in clean
    assert sanitize_font_family("Arial; } @import url(x)", "Arial") == "Arial"


def test_circular_import_safe():
    # Must not raise ImportError
    from app.importers.md_importer import import_md as im
    from app.core.converter import convert as cv
    from app.exporters.md_exporter import export_md as em

    assert callable(im) and callable(cv) and callable(em)
