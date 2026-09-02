"""Deep golden: ODT / JSON / EPUB / RTF export → import roundtrip with span + meta."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

from app.core.document_model import (
    Comment,
    ContentControl,
    Footnote,
    Heading,
    PivotTable,
    Revision,
    Table,
    TextBox,
)
from app.exporters.epub_exporter import export_epub
from app.exporters.json_exporter import export_json, import_json
from app.exporters.odt_exporter import export_odt
from app.exporters.rtf_exporter import export_rtf
from app.importers.html_importer import import_html
from app.importers.odt_text import try_extract_odt_model
from app.importers.rtf_importer import import_rtf
from tests.golden_deep_fixture import golden_deep_model


def _epub_spine_text(data: bytes) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for name in zf.namelist():
            lower = name.lower()
            if not lower.endswith((".xhtml", ".html", ".htm")):
                continue
            if "nav.xhtml" in lower or "/nav." in lower:
                continue
            parts.append(zf.read(name).decode("utf-8", errors="replace"))
    return "\n".join(parts)


def test_json_deep_roundtrip_span_and_meta():
    model = golden_deep_model()
    raw = export_json(model).encode("utf-8")
    data = json.loads(raw)

    assert data["schema_version"] == "1.0"
    assert data["title"] == "DeepGolden"
    types = {b["type"] for b in data["blocks"]}
    assert types >= {
        "table",
        "heading",
        "paragraph",
        "comment",
        "footnote",
        "revision",
        "content_control",
        "text_box",
        "pivot_table",
    }

    span = next(b for b in data["blocks"] if b.get("name") == "SpanGrid")
    assert span["cells"][0][0]["col_span"] == 2
    assert span["cells"][0][1]["covered"] is True

    back = import_json(raw, "deep.json")
    assert back.title == "DeepGolden"
    assert any(isinstance(n, Heading) and n.plain_text() == "Regional Summary" for n in back.nodes)
    assert any(isinstance(n, Comment) and n.text == "check totals" for n in back.nodes)
    assert any(isinstance(n, PivotTable) and n.name == "SalesPivot" for n in back.nodes)

    span_tbl = next(n for n in back.nodes if isinstance(n, Table) and n.name == "SpanGrid")
    assert span_tbl.cells[0][0].col_span == 2
    assert span_tbl.cells[0][1].covered is True
    assert any(isinstance(n, TextBox) and n.text == "Sidebar note" for n in back.nodes)
    assert any(isinstance(n, Revision) and n.text == "new clause" for n in back.nodes)
    assert any(
        isinstance(n, ContentControl) and n.control_type == "dropdown" for n in back.nodes
    )


def test_odt_deep_export_span_and_meta():
    model = golden_deep_model()
    raw = export_odt(model)
    assert raw[:2] == b"PK"

    with zipfile.ZipFile(BytesIO(raw)) as zf:
        content = zf.read("content.xml").decode("utf-8", errors="replace")

    assert 'table:number-columns-spanned="2"' in content
    assert "Merged" in content and "Regional Summary" in content
    assert "check totals" in content
    assert "See appendix" in content
    assert "SalesPivot" in content
    assert "Sidebar note" in content
    assert "new clause" in content


def test_odt_deep_import_roundtrip_text():
    model = golden_deep_model()
    raw = export_odt(model)
    back = try_extract_odt_model(raw, title="DeepGolden")
    assert back is not None

    blob_parts: list[str] = []
    for n in back.nodes:
        if isinstance(n, Table):
            for row in n.rows:
                blob_parts.extend(str(c) for c in row)
        elif isinstance(n, Comment):
            blob_parts.append(n.text)
        elif isinstance(n, Footnote):
            blob_parts.append(n.text)
        elif isinstance(n, PivotTable):
            blob_parts.append(n.name)
        elif isinstance(n, TextBox):
            blob_parts.append(n.text)
        elif isinstance(n, Revision):
            blob_parts.append(n.text)
        elif isinstance(n, ContentControl):
            blob_parts.append(n.text)
        elif hasattr(n, "plain_text"):
            blob_parts.append(n.plain_text())
    blob = " ".join(blob_parts)
    for needle in ("Merged", "Regional Summary", "check totals", "SalesPivot", "Sidebar note"):
        assert needle in blob

    tables = [n for n in back.nodes if isinstance(n, Table)]
    assert len(tables) >= 2
    span_rows = next(t for t in tables if any("Merged" in c for row in t.rows for c in row))
    assert span_rows.rows[0][0] == "Merged"


def test_odt_deep_import_span_metadata():
    model = golden_deep_model()
    raw = export_odt(model)
    back = try_extract_odt_model(raw, title="DeepGolden")
    assert back is not None

    span_tbl = next(
        n
        for n in back.nodes
        if isinstance(n, Table) and n.name == "SpanGrid"
    )
    assert span_tbl.cells
    assert span_tbl.cells[0][0].col_span == 2
    assert span_tbl.cells[0][0].text == "Merged"
    assert span_tbl.cells[0][1].covered is True
    assert span_tbl.cells[0][1].origin_row == 0
    assert span_tbl.cells[0][1].origin_col == 0


def test_rtf_deep_export_span_and_meta():
    model = golden_deep_model()
    text = export_rtf(model).decode("utf-8", errors="replace")

    assert text.startswith("{\\rtf")
    assert "mdbridge-table:" in text or "clmgf" in text
    assert "Merged" in text and "Regional Summary" in text
    assert "check totals" in text
    assert "SalesPivot" in text
    assert "Sidebar note" in text


def test_rtf_deep_import_text_roundtrip():
    model = golden_deep_model()
    raw = export_rtf(model)
    back = import_rtf(raw, "deep.rtf")
    blob = " ".join(n.plain_text() for n in back.nodes if hasattr(n, "plain_text"))
    for node in back.nodes:
        if isinstance(node, Table) and node.cells:
            blob += " " + " ".join(
                c.text for row in node.cells for c in row if not c.covered and c.text
            )

    for needle in ("Merged", "Regional Summary", "check totals", "SalesPivot", "Sidebar note"):
        assert needle in blob


def test_epub_deep_export_span_and_meta():
    model = golden_deep_model()
    raw = export_epub(model)
    assert raw[:2] == b"PK"
    body = _epub_spine_text(raw)

    assert 'colspan="2"' in body
    assert "Regional Summary" in body
    assert "Merged" in body and "East" in body
    assert "check totals" in body
    assert "See appendix" in body
    assert "SalesPivot" in body or "Pivot" in body
    assert "Sidebar note" in body
    assert "class='comment'" in body or 'class="comment"' in body
    assert "content-control" in body
    assert "data-type='dropdown'" in body or 'data-type="dropdown"' in body


def test_html_importer_content_control_deep():
    """Direct html_importer golden: aside.content-control → ContentControl AST."""
    html = """<html><body>
<aside class="content-control" data-tag="status" data-type="dropdown">
<strong>Status</strong> Draft
</aside>
</body></html>"""
    model = import_html(html.encode("utf-8"), "control.html")
    cc = next(n for n in model.nodes if isinstance(n, ContentControl))
    assert cc.tag == "status"
    assert cc.alias == "Status"
    assert cc.control_type == "dropdown"
    assert cc.text == "Draft"


def test_epub_deep_import_roundtrip_meta_and_span():
    from app.importers.epub_text import try_extract_epub_model

    model = golden_deep_model()
    raw = export_epub(model)
    back = try_extract_epub_model(raw, title="DeepGolden")
    assert back is not None

    assert any(isinstance(n, Comment) and n.text == "check totals" for n in back.nodes)
    assert any(isinstance(n, Footnote) and n.text == "See appendix" for n in back.nodes)
    assert any(isinstance(n, Revision) and n.text == "new clause" for n in back.nodes)
    assert any(isinstance(n, TextBox) and n.text == "Sidebar note" for n in back.nodes)
    assert any(isinstance(n, PivotTable) and n.name == "SalesPivot" for n in back.nodes)
    cc = next(n for n in back.nodes if isinstance(n, ContentControl))
    assert cc.tag == "status"
    assert cc.alias == "Status"
    assert cc.control_type == "dropdown"
    assert cc.text == "Draft"

    span_tbl = next(
        n
        for n in back.nodes
        if isinstance(n, Table) and n.cells and any(c.text == "Merged" for row in n.cells for c in row)
    )
    assert span_tbl.cells[0][0].col_span == 2
    assert span_tbl.cells[0][1].covered is True


def test_epub_convert_json_roundtrip_native_light(monkeypatch):
    """convert() EPUB→JSON when native/light importer owns epub (no anydoc)."""
    from app.core.converter import convert
    from app.importers import anydoc_bridge

    monkeypatch.setattr(anydoc_bridge, "should_handle", lambda _fmt: False)

    raw = export_epub(golden_deep_model())
    json_text = convert(raw, "epub", "json", filename="deep.epub").decode()
    data = json.loads(json_text)

    types = {b.get("type") for b in data.get("blocks") or []}
    assert "content_control" in types
    assert "comment" in types
    assert "pivot_table" in types

    cc = next(b for b in data["blocks"] if b.get("type") == "content_control")
    assert cc.get("tag") == "status"
    assert cc.get("alias") == "Status"
    assert cc.get("control_type") == "dropdown"
    assert cc.get("text") == "Draft"

    span = next(
        b
        for b in data["blocks"]
        if b.get("type") == "table"
        and b.get("cells")
        and b["cells"][0][0].get("col_span") == 2
    )
    assert span["cells"][0][1]["covered"] is True


def test_epub_convert_md_native_light(monkeypatch):
    """convert() EPUB→MD when native/light importer owns epub (no anydoc fast path)."""
    from app.core.converter import convert
    from app.importers import anydoc_bridge

    monkeypatch.setattr(anydoc_bridge, "should_handle", lambda _fmt: False)

    raw = export_epub(golden_deep_model())
    md = convert(raw, "epub", "md", filename="deep.epub").decode()

    assert "Status" in md and "Draft" in md
    assert "content_control:dropdown" in md
    assert "check totals" in md
    assert "SalesPivot" in md or "Pivot:" in md


def test_epub_import_epub_light_roundtrip_json():
    """import_epub light extract → JSON export (CI backend path without docling)."""
    from app.importers.epub_importer import import_epub

    raw = export_epub(golden_deep_model())
    model = import_epub(raw, "deep.epub")
    data = json.loads(export_json(model))

    cc = next(b for b in data["blocks"] if b.get("type") == "content_control")
    assert cc.get("control_type") == "dropdown"
    assert cc.get("text") == "Draft"


def test_odt_deep_import_meta_structured():
    model = golden_deep_model()
    raw = export_odt(model)
    back = try_extract_odt_model(raw, title="DeepGolden")
    assert back is not None

    assert any(isinstance(n, Comment) and n.text == "check totals" and n.author == "reviewer" for n in back.nodes)
    assert any(isinstance(n, Footnote) and n.id == "1" for n in back.nodes)
    assert any(isinstance(n, Revision) and n.change == "insert" for n in back.nodes)
    assert any(
        isinstance(n, ContentControl) and n.control_type == "dropdown" and n.text == "Draft"
        for n in back.nodes
    )
    assert any(isinstance(n, TextBox) and n.text == "Sidebar note" for n in back.nodes)
    assert any(isinstance(n, PivotTable) and n.name == "SalesPivot" for n in back.nodes)
