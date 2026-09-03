"""XLSX DrawingML charts + DOCX theme font scheme (v3.15 / expansion T35)."""

from __future__ import annotations

import zipfile
from io import BytesIO

from app.core.document_model import Chart, DocumentModel, Paragraph, Table
from app.exporters import docx_exporter
from app.exporters.xlsx_exporter import export_xlsx, import_xlsx


def test_xlsx_embeds_drawingml_chart():
    model = DocumentModel(
        nodes=[
            Table(name="Sales", rows=[["Q", "Amt"], ["Q1", "10"], ["Q2", "20"]]),
            Chart(
                title="Revenue",
                chart_type="bar",
                categories=["Q1", "Q2"],
                series=[{"name": "Amt", "values": [10, 20]}],
            ),
        ]
    )
    blob = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        names = zf.namelist()
        assert "xl/charts/chart1.xml" in names
        assert "xl/drawings/drawing1.xml" in names
        assert "xl/worksheets/_rels/sheet1.xml.rels" in names
        sheet = zf.read("xl/worksheets/sheet1.xml").decode()
        assert "<drawing " in sheet
        chart = zf.read("xl/charts/chart1.xml").decode()
        assert "Revenue" in chart
        assert "<c:barChart" in chart
        assert "10" in chart and "20" in chart
        ct = zf.read("[Content_Types].xml").decode()
        assert "drawingml.chart+xml" in ct


def test_xlsx_chart_roundtrip_import():
    model = DocumentModel(
        nodes=[
            Paragraph(text="ctx"),
            Chart(
                title="PieMe",
                chart_type="pie",
                categories=["A", "B"],
                series=[{"name": "Share", "values": [30, 70]}],
            ),
        ]
    )
    blob = export_xlsx(model)
    back = import_xlsx(blob)
    charts = [n for n in back.nodes if isinstance(n, Chart)]
    assert charts, "expected Chart AST from xl/charts"
    assert any(c.title == "PieMe" or c.chart_type == "pie" for c in charts)


def test_docx_theme_rewrites_latin_font():
    model = DocumentModel(nodes=[Paragraph(text="Hello theme")])
    blob = docx_exporter.export_docx(model, theme="academic")
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        theme_path = next(n for n in zf.namelist() if n.endswith("theme1.xml"))
        theme = zf.read(theme_path).decode("utf-8", "ignore")
    assert "md-bridge-academic" in theme or "Times New Roman" in theme
    assert "Times New Roman" in theme
