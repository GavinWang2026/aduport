"""DOCX alt-path OPC packages and outlineLvl → Heading (v5 calibration fixes)."""

from __future__ import annotations

from pathlib import Path

from aduport.core.document_model import Heading, Paragraph
from aduport.core.registry import ensure_builtin_plugins, get_importer
from aduport.exporters.md_exporter import export_md

FIX = Path(__file__).resolve().parents[2] / "native/anydoc/tests/fixtures/docx"


def test_altpath_docx_reads_office_document_rel():
    ensure_builtin_plugins()
    path = FIX / "handmade-altpath.docx"
    model = get_importer("docx")(path.read_bytes(), path.name)
    texts = [
        n.plain_text() for n in model.nodes if hasattr(n, "plain_text") and n.plain_text()
    ]
    assert "Relocated heading" in texts
    assert "Body found via rels" in texts
    heads = [n for n in model.nodes if isinstance(n, Heading)]
    assert heads and heads[0].plain_text() == "Relocated heading"
    assert (model.metadata or {}).get("document_part") == "content/main.xml"
    md = export_md(model)
    assert "Relocated heading" in md
    assert "Body found via rels" in md


def test_strict_ooxml_namespace_docx():
    ensure_builtin_plugins()
    path = FIX / "handmade-strict.docx"
    model = get_importer("docx")(path.read_bytes(), path.name)
    texts = [
        n.plain_text() for n in model.nodes if hasattr(n, "plain_text") and n.plain_text()
    ]
    assert "Strict heading" in texts
    assert any("Strict body" in t for t in texts)
    heads = [n for n in model.nodes if isinstance(n, Heading)]
    assert heads and heads[0].plain_text() == "Strict heading"


def test_outline_lvl_maps_to_headings():
    ensure_builtin_plugins()
    path = FIX / "handmade-outline.docx"
    model = get_importer("docx")(path.read_bytes(), path.name)
    by_text = {
        n.plain_text(): n
        for n in model.nodes
        if hasattr(n, "plain_text") and n.plain_text() and not n.plain_text().startswith("[")
    }
    stay = by_text["Style heading stays a heading"]
    override = by_text["Direct level overrides the style"]
    nine = by_text["Direct nine turns the style heading off"]
    direct = by_text["Direct outline without a style"]
    child = by_text["Child style nine stops inheritance"]
    assert isinstance(stay, Heading) and stay.level == 2
    assert isinstance(override, Heading) and override.level == 3
    assert isinstance(nine, Paragraph)
    assert isinstance(direct, Heading) and direct.level == 1
    assert isinstance(child, Paragraph)
