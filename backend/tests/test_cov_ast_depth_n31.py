"""§3.1 red-box: real Chart (docx/pptx), Footnote import, zombie AST already live."""

from __future__ import annotations

import zipfile
from io import BytesIO

from aduport.core.document_model import (
    Chart,
    DocumentModel,
    Footnote,
    Paragraph,
)
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.pptx_exporter import export_pptx
from aduport.importers.docx_ooxml import enrich_docx_model
from aduport.importers.docx_text import try_extract_docx_model


def test_docx_embeds_drawingml_chart_and_roundtrips():
    model = DocumentModel(
        nodes=[
            Paragraph(text="ctx"),
            Chart(
                title="RevenueDocx",
                chart_type="bar",
                categories=["Q1", "Q2"],
                series=[{"name": "Amt", "values": [10, 20]}],
            ),
        ]
    )
    blob = export_docx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        assert "word/charts/chart1.xml" in zf.namelist()
        chart = zf.read("word/charts/chart1.xml").decode()
        assert "RevenueDocx" in chart or "<c:barChart" in chart
        assert b"c:chart" in zf.read("word/document.xml") or b"charts/chart1" in zf.read(
            "word/_rels/document.xml.rels"
        )
    back = try_extract_docx_model(blob, title="rt")
    assert back is not None
    enriched = enrich_docx_model(back, blob)
    charts = [n for n in enriched.nodes if isinstance(n, Chart)]
    assert charts and any(c.title == "RevenueDocx" or c.chart_type == "bar" for c in charts)


def test_pptx_embeds_native_chart():
    model = DocumentModel(
        nodes=[
            Chart(
                title="PiePptx",
                chart_type="pie",
                categories=["A", "B"],
                series=[{"name": "Share", "values": [30, 70]}],
            )
        ]
    )
    blob = export_pptx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        names = zf.namelist()
        assert any(n.startswith("ppt/charts/") for n in names), names
        assert any("chart" in n.lower() for n in names)


def test_docx_footnotes_xml_roundtrip_import():
    model = DocumentModel(
        nodes=[
            Paragraph(text="body"),
            Footnote(id="1", text="FN_ROUNDTRIP_BODY"),
        ]
    )
    blob = export_docx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        assert "word/footnotes.xml" in zf.namelist()
        assert b"FN_ROUNDTRIP_BODY" in zf.read("word/footnotes.xml")
    base = try_extract_docx_model(blob, title="fn") or DocumentModel(title="fn", nodes=[])
    enriched = enrich_docx_model(base, blob)
    fns = [n for n in enriched.nodes if isinstance(n, Footnote)]
    assert fns and any("FN_ROUNDTRIP_BODY" in n.text for n in fns)
