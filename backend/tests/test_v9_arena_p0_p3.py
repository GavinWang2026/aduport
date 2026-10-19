"""v9 arena P0–P3 gates: docx images, native docx, span HTML, escape, llm_mode."""

from __future__ import annotations

import base64
from io import BytesIO

import pytest
from docx import Document
from docx.shared import Inches

from aduport.config import settings
from aduport.core.document_model import (
    CodeBlock,
    DocumentModel,
    Heading,
    Image,
    ListItem,
    Paragraph,
    Table,
    TableCell,
)
from aduport.core.registry import ensure_builtin_plugins
from aduport.exporters.md_escape import collapse_blank_lines, escape_md_text
from aduport.exporters.md_exporter import export_md
from aduport.importers.anydoc_bridge import should_handle
from aduport.importers.docx_importer import import_docx


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_p0_docx_images_roundtrip_md():
    doc = Document()
    doc.add_heading("Pic", level=1)
    p = doc.add_paragraph("before")
    p.add_run().add_picture(BytesIO(_PNG), width=Inches(0.3))
    buf = BytesIO()
    doc.save(buf)
    model = import_docx(buf.getvalue(), filename="p0.docx")
    assert any(isinstance(n, Image) and n.data for n in model.nodes)
    md = export_md(model)
    assert "data:image/png;base64," in md


def test_p1_docx_defaults_to_native():
    assert should_handle("docx") is False
    assert "docx" not in settings.anydoc_format_list()


def test_p1_native_md_list_markers_and_blank_collapse():
    model = DocumentModel(
        title="",
        nodes=[
            ListItem(text="alpha", ordered=False, level=0),
            ListItem(text="beta", ordered=False, level=0),
            ListItem(text="one", ordered=True, number=1, level=0),
            ListItem(text="two", ordered=True, number=2, marker_label="2.", level=0),
            Paragraph(text="after"),
        ],
    )
    md = export_md(model)
    assert "- alpha" in md and "- beta" in md
    assert "1. one" in md and "2. two" in md
    # Tight list: no blank between consecutive ListItems
    assert "- alpha\n- beta" in md
    assert "\n\n\n" not in md


def test_p1_docx_list_bullet_style_maps_to_listitem():
    doc = Document()
    try:
        p = doc.add_paragraph("bullet item", style="List Bullet")
    except Exception:
        pytest.skip("List Bullet style unavailable")
    buf = BytesIO()
    doc.save(buf)
    model = import_docx(buf.getvalue(), filename="list.docx")
    assert any(isinstance(n, ListItem) for n in model.nodes)


def test_p2_html_table_for_span_toggle(monkeypatch):
    table = Table(
        rows=[["Merged", ""], ["a", "b"]],
        cells=[
            [TableCell(text="Merged", col_span=2), TableCell(covered=True, origin_row=0, origin_col=0)],
            [TableCell(text="a"), TableCell(text="b")],
        ],
        header_rows=1,
    )
    model = DocumentModel(nodes=[table])
    monkeypatch.setattr(settings, "html_table_for_span", True)
    assert "colspan=" in export_md(model)
    monkeypatch.setattr(settings, "html_table_for_span", False)
    flat = export_md(model)
    assert "colspan=" not in flat
    assert "|" in flat


def test_p2_context_aware_escape_preserves_code_and_blocks():
    # Line-start # in prose → escaped; code content stays literal
    assert escape_md_text("# not a heading", at_line_start=True).startswith("\\#")
    assert escape_md_text("# keep", at_line_start=False).startswith("#")
    assert escape_md_text("# code", in_code=True) == "# code"
    model = DocumentModel(
        nodes=[
            Paragraph(text="# looks like heading"),
            CodeBlock(lang="py", content="# real comment\nprint(1)"),
        ]
    )
    md = export_md(model)
    assert "\\# looks like heading" in md
    assert "```py\n# real comment\nprint(1)\n```" in md


def test_p3_llm_mode_strips_base64_and_adds_front_matter(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", True)
    monkeypatch.setattr(settings, "llm_mode_strip_images", True)
    monkeypatch.setattr(settings, "llm_mode_front_matter", True)
    model = DocumentModel(
        title="LLM Doc",
        metadata={"importer": "docx_native"},
        nodes=[
            Heading(level=1, text="Hi"),
            Image(data=_PNG, alt_text="logo", mime_type="image/png"),
            Paragraph(text="body"),
        ],
    )
    md = export_md(model)
    assert md.startswith("---")
    assert 'title: "LLM Doc"' in md
    assert "mode: \"llm\"" in md
    assert "data:image" not in md
    assert "omitted:png" in md or "](omitted:" in md


def test_p2_fast_md_skipped_for_docx_when_span_html(monkeypatch):
    ensure_builtin_plugins()
    from aduport.core import converter as conv

    monkeypatch.setattr(settings, "html_table_for_span", True)
    monkeypatch.setattr(settings, "anydoc_fast_markdown", True)
    assert conv._try_anydoc_fast_markdown(b"PK", "docx", "md", "a.docx") is None


def test_docx_importer_source_mentions_drawing_pipeline():
    """v9 evidence grep: importer module must reference drawing/blip path."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "aduport" / "importers" / "docx_importer.py"
    text = src.read_text(encoding="utf-8")
    assert "drawing" in text and "blip" in text
    assert "Image" in text


def test_native_md_no_run_style_placeholder_flood():
    """OOXML style catalogs must not inflate md with degraded placeholders."""
    from io import BytesIO

    from docx import Document

    from aduport.core.converter import convert

    doc = Document()
    doc.add_heading("H", level=1)
    doc.add_paragraph("body")
    try:
        doc.add_paragraph("li", style="List Bullet")
    except Exception:
        pass
    buf = BytesIO()
    doc.save(buf)
    out = convert(buf.getvalue(), "docx", "md", filename="x.docx", skip_magic=True).decode()
    assert "run_style|degraded" not in out
    assert "numbering|degraded" not in out
    assert "# H" in out or "## H" in out or "H" in out


def test_collapse_blank_lines_helper():
    assert collapse_blank_lines("a\n\n\n\nb\n") == "a\n\nb\n"
