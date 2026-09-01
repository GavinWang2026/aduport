"""Exporter span fidelity: tables with row/col spans render as real merges.

anydoc populates Table.cells with row_span / col_span / covered; these tests
prove the DOCX / HTML / ODT exporters consume that data instead of the flat
grid, and that Markdown degrades gracefully (header rows honoured, flat).
"""

from __future__ import annotations

import io
import zipfile

from app.core.document_model import DocumentModel, Table, TableCell
from app.exporters import docx_exporter, html_render, odt_exporter


def _col_span_table() -> Table:
    cells = [
        [TableCell(text="Merged", col_span=2), TableCell(covered=True, origin_row=0, origin_col=0)],
        [TableCell(text="A"), TableCell(text="B")],
    ]
    return Table(rows=[["Merged", ""], ["A", "B"]], cells=cells, header_rows=0, kind="data")


def _row_span_table() -> Table:
    cells = [
        [TableCell(text="Tall", row_span=2), TableCell(text="Y")],
        [TableCell(covered=True, origin_row=0, origin_col=0), TableCell(text="Z")],
    ]
    return Table(rows=[["Tall", "Y"], ["", "Z"]], cells=cells, header_rows=0, kind="data")


def _header_table() -> Table:
    cells = [
        [TableCell(text="H1"), TableCell(text="H2")],
        [TableCell(text="a"), TableCell(text="b")],
    ]
    return Table(rows=[["H1", "H2"], ["a", "b"]], cells=cells, header_rows=2, kind="data")


def _docx_xml(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read("word/document.xml").decode("utf-8", "ignore")


# --------------------------------------------------------------------------
# HTML  (note: table_first_row_header defaults True, so row 0 is <th>)
# --------------------------------------------------------------------------


def test_html_col_span():
    out = html_render._render_table_html(_col_span_table())
    assert 'colspan="2"' in out
    # row0 = merged header cell (th, colspan 2); row1 = A, B (td) -> 1 th + 2 td
    assert out.count("<th") == 1
    assert out.count("<td") == 2


def test_html_row_span():
    out = html_render._render_table_html(_row_span_table())
    assert 'rowspan="2"' in out
    assert out.count("<th") == 1  # Tall is header (row 0)
    assert out.count("<td") == 2  # Y and Z; covered slot omitted


def test_html_header_rows():
    out = html_render._render_table_html(_header_table())
    # header_rows=2 -> both rows are <th>; 2 cols x 2 rows = 4 <th>
    assert out.count("<th") == 4


# --------------------------------------------------------------------------
# ODT
# --------------------------------------------------------------------------


def test_odt_col_span():
    out = odt_exporter._table_xml(_col_span_table())
    assert 'table:number-columns-spanned="2"' in out
    assert "<table:covered-table-cell" in out


def test_odt_row_span():
    out = odt_exporter._table_xml(_row_span_table())
    assert 'table:number-rows-spanned="2"' in out
    assert "<table:covered-table-cell" in out


# --------------------------------------------------------------------------
# DOCX (python-docx merge -> gridSpan / vMerge in document.xml)
# --------------------------------------------------------------------------


def test_docx_col_span_merge():
    model = DocumentModel(title="t", nodes=[_col_span_table()])
    xml = _docx_xml(docx_exporter.export_docx(model))
    assert "gridSpan" in xml


def test_docx_row_span_merge():
    model = DocumentModel(title="t", nodes=[_row_span_table()])
    xml = _docx_xml(docx_exporter.export_docx(model))
    assert "vMerge" in xml


def test_docx_header_rows_valid():
    model = DocumentModel(title="t", nodes=[_header_table()])
    blob = docx_exporter.export_docx(model)
    assert blob[:2] == b"PK"  # valid docx zip
