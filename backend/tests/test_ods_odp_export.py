"""ODS/ODP export + MISC scatter warn (report leftover closure)."""

from __future__ import annotations

import zipfile
from io import BytesIO

from app.core.document_model import Chart, DocumentModel, Heading, Paragraph, Table
from app.core.registry import ensure_builtin_plugins, get_exporter
from app.exporters.odp_exporter import export_odp
from app.exporters.ods_exporter import export_ods
from app.exporters.xlsx_exporter import export_xlsx


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
