"""v8 §B/C/D roadmap: xlsx depth, RAG elements, LibreOffice hook, stream gate."""

from __future__ import annotations

import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from app.config import settings
from app.core.document_model import (
    Chart,
    DocumentModel,
    Heading,
    Paragraph,
    Table,
    TableCell,
)
from app.core.stream_convert import convert_streaming
from app.exporters.libreoffice_bridge import (
    LibreOfficeUnavailable,
    convert_via_libreoffice,
    libreoffice_enabled,
)
from app.exporters.rag_elements import export_rag_elements
from app.exporters.xlsx_exporter import export_xlsx, import_xlsx
from app.importers import _docling_utils as du


_ROOT = Path(__file__).resolve().parents[2]


def test_xlsx_col_width_row_height_roundtrip():
    model = DocumentModel(
        nodes=[
            Table(
                name="Sized",
                rows=[["A", "B"], ["1", "2"]],
                cells=[
                    [TableCell(text="A"), TableCell(text="B")],
                    [TableCell(text="1"), TableCell(text="2")],
                ],
                column_widths=[18.5, 22.0],
                row_heights=[20.0, 15.5],
                header_rows=1,
            )
        ]
    )
    raw = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        xml = zf.read("xl/worksheets/sheet1.xml").decode()
    assert 'width="18.50"' in xml or 'width="18.5"' in xml
    assert 'ht="20.00"' in xml or 'ht="20.0"' in xml
    back = import_xlsx(raw, "sized.xlsx")
    tables = [n for n in back.nodes if isinstance(n, Table) and n.name == "Sized"]
    assert tables
    assert tables[0].column_widths[0] == pytest.approx(18.5, abs=0.05)
    assert tables[0].row_heights[0] == pytest.approx(20.0, abs=0.05)


def test_xlsx_cross_sheet_formula_preserved():
    model = DocumentModel(
        nodes=[
            Table(name="Data", rows=[["v"], ["10"]], header_rows=0),
            Table(
                name="Calc",
                rows=[["ref"], [""]],
                cells=[
                    [TableCell(text="ref")],
                    [TableCell(text="", formula="Data!A2*2", cell_kind="formula")],
                ],
                header_rows=0,
            ),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "xsheet.xlsx")
    calc = next(n for n in back.nodes if isinstance(n, Table) and n.name == "Calc")
    assert calc.cells
    assert "Data!A2" in (calc.cells[1][0].formula or "")


def test_xlsx_chart_axis_titles_roundtrip():
    model = DocumentModel(
        nodes=[
            Table(name="Data", rows=[["c", "v"], ["a", "1"]], header_rows=1),
            Chart(
                title="Sales",
                chart_type="bar",
                categories=["a"],
                series=[{"name": "S1", "values": [1.0]}],
                x_axis_title="Region",
                y_axis_title="Amount",
            ),
        ]
    )
    raw = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        chart_xml = zf.read("xl/charts/chart1.xml").decode()
    assert "Region" in chart_xml and "Amount" in chart_xml
    back = import_xlsx(raw, "chart.xlsx")
    charts = [n for n in back.nodes if isinstance(n, Chart)]
    assert charts
    assert charts[0].x_axis_title == "Region"
    assert charts[0].y_axis_title == "Amount"


def test_rag_elements_contract():
    model = DocumentModel(
        title="rag-demo",
        nodes=[
            Heading(level=1, text="Hello"),
            Paragraph(text="Body " * 40),
            Table(rows=[["h"], ["1"]], header_rows=1),
        ],
    )
    els = export_rag_elements(model, max_chars=80)
    types = {e["type"] for e in els}
    assert "Title" in types or "SectionHeader" in types
    assert "NarrativeText" in types
    assert "Table" in types
    assert all("metadata" in e and "source_node" in e["metadata"] for e in els)


def test_rag_registered_export_roundtrip():
    from app.core.converter import convert
    from app.core.registry import ensure_builtin_plugins

    ensure_builtin_plugins()
    out = convert(b"# Title\n\nHello RAG.\n", "md", "rag", filename="t.md", skip_magic=True)
    import json

    payload = json.loads(out.decode())
    assert payload.get("schema") == "mdbridge.rag_elements.v1"
    assert isinstance(payload.get("elements"), list) and payload["elements"]
    # alias
    out2 = convert(b"# T\n", "md", "elements", filename="t.md", skip_magic=True)
    assert b"mdbridge.rag_elements" in out2


def test_libreoffice_bridge_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "libreoffice_enabled", False)
    assert libreoffice_enabled() is False
    with pytest.raises(LibreOfficeUnavailable):
        convert_via_libreoffice(b"%PDF", filename="x.pdf", target_ext="docx")


def test_libreoffice_pdf_path_skipped_when_disabled(monkeypatch):
    from app.core import converter as conv

    monkeypatch.setattr(settings, "libreoffice_enabled", False)
    assert conv._try_libreoffice_pdf(b"PK", "docx", "pdf", "a.docx") is None


def test_docling_semantic_labels_map_to_ast_nodes():
    from app.core.document_model import Blockquote, Footnote, HorizontalRule, MathBlock
    from types import SimpleNamespace

    items = [
        SimpleNamespace(label="footnote", text="note body", id="1"),
        SimpleNamespace(label="blockquote", text="quoted"),
        SimpleNamespace(label="formula", text="E=mc^2"),
        SimpleNamespace(label="page_break"),
        SimpleNamespace(label="checkbox_selected", text="done", checked=True),
        SimpleNamespace(label="unknown_widget", text="kept"),
    ]
    doc = SimpleNamespace(texts=items)
    model = du.docling_document_to_model(doc, title="sem")
    kinds = {type(n).__name__ for n in model.nodes}
    assert "Footnote" in kinds
    assert "Blockquote" in kinds
    assert "MathBlock" in kinds
    assert "HorizontalRule" in kinds
    assert any(isinstance(n, Footnote) and n.text == "note body" for n in model.nodes)
    assert any(isinstance(n, Blockquote) for n in model.nodes)
    assert any(isinstance(n, MathBlock) and "E=mc" in n.content for n in model.nodes)
    assert any(isinstance(n, HorizontalRule) for n in model.nodes)


def test_docx_text_table_gridspan_without_anydoc():
    pytest.importorskip("docx")
    from docx import Document
    from io import BytesIO

    from app.importers.docx_text import try_extract_docx_model

    buf = BytesIO()
    doc = Document()
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).merge(t.cell(0, 1))
    t.cell(0, 0).text = "Merged"
    t.cell(1, 0).text = "a"
    t.cell(1, 1).text = "b"
    doc.save(buf)
    model = try_extract_docx_model(buf.getvalue(), title="span")
    assert model is not None
    tables = [n for n in model.nodes if isinstance(n, Table)]
    assert tables and tables[0].cells
    assert tables[0].cells[0][0].col_span == 2
    assert tables[0].cells[0][1].covered is True


def test_docling_table_without_origin_illusion():
    class Cell:
        def __init__(self, text, row_span=1, col_span=1, covered=False):
            self.text = text
            self.row_span = row_span
            self.col_span = col_span
            self.covered = covered

    class Data:
        grid = [[Cell("Merged", col_span=2), Cell("", covered=True)], [Cell("a"), Cell("b")]]

    class Item:
        label = "table"
        data = Data()

    tbl = du._table_from_item(Item())
    assert tbl is not None
    assert tbl.cells  # real spans keep cells
    assert tbl.cells[0][0].col_span == 2
    assert tbl.cells[0][1].covered is True


def test_stream_dual_path_above_threshold(monkeypatch):
    """v3 once lost 74% via stream; keep dual-path regression above min bytes."""
    monkeypatch.setattr(settings, "stream_convert", True)
    monkeypatch.setattr(settings, "stream_min_bytes", 64)
    monkeypatch.setattr(settings, "stream_chunk_chars", 80)
    body = ("Paragraph block about streaming.\n\n" * 40).encode()
    out = convert_streaming(body, "md", "md", filename="big.md")
    assert b"Paragraph" in out
    assert len(out) > 100


def test_contributing_and_ecosystem_docs_exist():
    assert (_ROOT / "CONTRIBUTING.md").is_file()
    eco = (_ROOT / "enterprise" / "ECOSYSTEM_STRATEGY.md").read_text(encoding="utf-8")
    assert "66.2" in eco
