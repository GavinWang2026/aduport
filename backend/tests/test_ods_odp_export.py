"""ODS/ODP export + MISC scatter warn (report leftover closure)."""

from __future__ import annotations

import zipfile
from io import BytesIO

from aduport.core.document_model import Chart, DocumentModel, Heading, Paragraph, Table
from aduport.core.registry import ensure_builtin_plugins, get_exporter
from aduport.exporters.odp_exporter import export_odp
from aduport.exporters.ods_exporter import export_ods
from aduport.exporters.xlsx_exporter import export_xlsx


def test_ods_export_tables_round_mimetype():
    model = DocumentModel(
        nodes=[
            Table(name="Sales", rows=[["Q", "Amt"], ["Q1", "10"]]),
            Paragraph(text="note"),
        ]
    )
    blob = export_ods(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        assert zf.read("mimetype") == b"application/vnd.oasis.opendocument.spreadsheet"
        xml = zf.read("content.xml").decode("utf-8", "ignore")
    assert "office:spreadsheet" in xml
    assert "Sales" in xml
    assert "Q1" in xml


def test_odp_export_pages_and_heading_buckets():
    model = DocumentModel(
        title="Deck",
        nodes=[
            Heading(level=1, text="Intro"),
            Paragraph(text="SENT_ODP_BODY"),
            Heading(level=2, text="Next"),
            Paragraph(text="more"),
        ],
    )
    blob = export_odp(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        assert zf.read("mimetype") == b"application/vnd.oasis.opendocument.presentation"
        xml = zf.read("content.xml").decode("utf-8", "ignore")
    assert "office:presentation" in xml
    assert "SENT_ODP_BODY" in xml
    assert "draw:page" in xml
    visible = __import__("re").sub(r"<[^>]+>", " ", xml)
    assert "Intro" in visible
    assert "Next" in visible


def test_odp_table_is_native_and_round_trips():
    from aduport.importers.odp_importer import import_odp

    model = DocumentModel(
        nodes=[
            Heading(level=1, text="GRID"),
            Table(rows=[["区域", "Q1"], ["DELTA", "31415"]], header_rows=1),
        ]
    )
    blob = export_odp(model)
    xml = zipfile.ZipFile(BytesIO(blob)).read("content.xml").decode("utf-8")
    assert "<table:table>" in xml
    assert "<table:table-cell" in xml
    assert "| DELTA |" not in xml
    assert "DELTA" in xml
    assert "区域" in xml
    back = import_odp(blob, "grid.odp")
    tables = [node for node in back.nodes if getattr(node, "type", "") == "table"]
    assert tables
    flat = " ".join(cell for row in tables[0].rows for cell in row)
    assert "DELTA" in flat
    assert "31415" in flat
    assert "区域" in flat


def test_ods_odp_registered_exporters():
    ensure_builtin_plugins()
    assert get_exporter("ods") is not None
    assert get_exporter("odp") is not None


def test_xlsx_scatter_warn_export():
    model = DocumentModel(
        nodes=[
            Chart(
                title="XY",
                chart_type="scatter",
                categories=["a", "b"],
                series=[{"name": "s", "values": [1, 2]}],
            )
        ]
    )
    export_xlsx(model)
    warns = model.metadata.get("export_warnings") or []
    assert any("scatter" in w.lower() for w in warns)
