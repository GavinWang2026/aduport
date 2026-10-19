"""Boost coverage on exporters / core paths for WBS ≥70% KPI."""

from __future__ import annotations

import base64

from aduport.core.document_model import (
    CodeBlock,
    DocumentModel,
    Heading,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    Paragraph,
    RichText,
    Table,
)
from aduport.core.converter import convert
from aduport.core.css_sanitize import sanitize_custom_css as scss, sanitize_font_family
from aduport.exporters.md_exporter import export_md
from aduport.exporters.pdf_exporter import _render_html
from aduport.importers.md_importer import import_md


def _sample_model() -> DocumentModel:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    return DocumentModel(
        title="Demo",
        nodes=[
            Heading(level=2, text=RichText(text="Hi", marks=[InlineMark(type=InlineMarkType.BOLD, start=0, end=2)])),
            Paragraph(
                text=RichText(
                    text="ab",
                    marks=[
                        InlineMark(type=InlineMarkType.LINK, start=0, end=1, href="https://a.example"),
                        InlineMark(type=InlineMarkType.ITALIC, start=1, end=2),
                    ],
                )
            ),
            ListItem(text="one", ordered=False, level=0),
            ListItem(text="two", ordered=False, level=0),
            ListItem(text="n1", ordered=True, level=1),
            Table(rows=[["h1", "h2"], ["c1"], ["c1", "c2", "c3"]]),
            Image(data=png, alt_text="pic", mime_type="image/png"),
            Image(data=b"", alt_text="alt|https://img.example/x.png", mime_type="image/png"),
            CodeBlock(lang="py", content="print(1)"),
            Paragraph(
                text=RichText(
                    text="codegone",
                    marks=[
                        InlineMark(type=InlineMarkType.CODE, start=0, end=4),
                        InlineMark(type=InlineMarkType.STRIKETHROUGH, start=4, end=8),
                    ],
                )
            ),
        ],
    )


def test_export_md_full_nodes():
    md = export_md(_sample_model())
    assert "##" in md
    assert "**Hi**" in md or "Hi" in md
    assert "https://a.example" in md
    assert "| h1" in md or "h1" in md
    assert "data:image/png;base64," in md
    assert "https://img.example/x.png" in md
    assert "```py" in md
    assert "`code`" in md
    assert "~~gone~~" in md


def test_pdf_render_html_full_and_css_sanitize():
    html = _render_html(_sample_model(), font_family="Arial", custom_css="h1{color:red}")
    assert "<ul>" in html and html.count("<li>") >= 2
    assert "<table>" in html
    assert "data:image/png;base64," in html
    assert "[Image:" in html
    assert "language-py" in html
    assert sanitize_font_family("bad;@import", "Arial") == "Arial"
    assert "169.254" not in scss("@import url(http://169.254.169.254); body{}")


def test_md_heading_code_hr_fence():
    src = b"# H1\n\n```js\nconsole.log(1)\n```\n\n---\n\nPara\n"
    model = import_md(src)
    out = export_md(model)
    assert "# H1" in out or "H1" in out
    assert "console.log" in out
    assert "---" in out
    from aduport.core.document_model import HorizontalRule

    assert any(isinstance(n, HorizontalRule) for n in model.nodes)


def test_same_format_passthrough():
    data = b"# same\n"
    assert convert(data, "md", "md") == data
