"""Exporter span fidelity: native colspan/rowspan across DOCX/HTML/ODT/PPTX/MD/LaTeX/RTF/TXT."""

from __future__ import annotations

import io
import zipfile

from app.core.document_model import DocumentModel, Heading, Paragraph, Table, TableCell, TextBox
from app.exporters import docx_exporter, html_render, md_exporter, odt_exporter, pptx_exporter, rtf_exporter


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
    # First-row header (default): row0 Tall+Y are <th>; row1 Z is <td> (covered omitted)
    assert out.count("<th") == 2
    assert out.count("<td") == 1


def test_html_header_rows():
    out = html_render._render_table_html(_header_table())
    # header_rows=2 -> both rows are <th>; 2 cols x 2 rows = 4 <th>
    assert out.count("<th") == 4


def test_html_span_respects_first_row_header_off(monkeypatch):
    """v8 P1: origin_* alone must not force spans path past header switch."""
    from app.config import settings

    monkeypatch.setattr(settings, "table_first_row_header", False)
    # Plain cells that only carry origin self-coords (md import illusion)
    plain = Table(
        rows=[["a", "b"], ["1", "2"]],
        cells=[
            [
                TableCell(text="a", origin_row=0, origin_col=0),
                TableCell(text="b", origin_row=0, origin_col=1),
            ],
            [
                TableCell(text="1", origin_row=1, origin_col=0),
                TableCell(text="2", origin_row=1, origin_col=1),
            ],
        ],
        header_rows=1,
    )
    out = html_render._render_table_html(plain)
    assert "<th>" not in out
    assert out.count("<td>") == 4
    # Real spans still render colspan/rowspan, but never as <th> when switch off
    spanned = html_render._render_table_html(_col_span_table())
    assert 'colspan="2"' in spanned
    assert "<th>" not in spanned
    assert "<td" in spanned


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


# --------------------------------------------------------------------------
# MD — GFM flat grid + header_rows honour
# --------------------------------------------------------------------------


def test_md_col_span_native_html():
    out = md_exporter.export_md(DocumentModel(nodes=[_col_span_table()]))
    assert 'colspan="2"' in out
    assert "Merged" in out


def test_md_row_span_native_html():
    out = md_exporter.export_md(DocumentModel(nodes=[_row_span_table()]))
    assert 'rowspan="2"' in out
    assert "Tall" in out and "Y" in out and "Z" in out


def test_md_span_import_roundtrip():
    from app.importers.md_importer import import_md

    raw = md_exporter.export_md(DocumentModel(nodes=[_col_span_table()])).encode()
    model = import_md(raw)
    tbl = model.nodes[0]
    assert tbl.cells[0][0].col_span == 2
    assert tbl.cells[0][1].covered is True


def test_md_header_rows():
    out = md_exporter.export_md(DocumentModel(nodes=[_header_table()]))
    lines = [ln.strip() for ln in out.strip().splitlines() if ln.strip()]
    sep_idx = next(i for i, ln in enumerate(lines) if ln.startswith("|") and "---" in ln)
    assert sep_idx == 2
    assert "H1" in lines[0] and "H2" in lines[0]
    assert "a" in lines[1] and "b" in lines[1]


# --------------------------------------------------------------------------
# PPTX — python-pptx cell merge
# --------------------------------------------------------------------------


def _pptx_slide_xml(blob: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        parts: list[str] = []
        for name in sorted(z.namelist()):
            if name.startswith("ppt/slides/slide") and name.endswith(".xml"):
                parts.append(z.read(name).decode("utf-8", "ignore"))
        return "\n".join(parts)
    return ""


def test_pptx_col_span_merge():
    model = DocumentModel(title="t", nodes=[_col_span_table()])
    xml = _pptx_slide_xml(pptx_exporter.export_pptx(model))
    assert "gridSpan" in xml


def test_pptx_row_span_merge():
    model = DocumentModel(title="t", nodes=[_row_span_table()])
    xml = _pptx_slide_xml(pptx_exporter.export_pptx(model))
    assert 'rowSpan="2"' in xml or 'vMerge="1"' in xml


# --------------------------------------------------------------------------
# LaTeX — multicolumn / multirow
# --------------------------------------------------------------------------


def test_latex_col_span():
    from app.exporters.latex_exporter import export_latex

    out = export_latex(DocumentModel(nodes=[_col_span_table()]))
    assert r"\multicolumn{2}" in out
    assert "Merged" in out


def test_latex_row_span():
    from app.exporters.latex_exporter import export_latex

    out = export_latex(DocumentModel(nodes=[_row_span_table()]))
    assert r"\multirow{2}" in out


def test_latex_col_span_import_roundtrip():
    from app.exporters.latex_exporter import export_latex
    from app.importers.latex_importer import import_latex

    raw = export_latex(DocumentModel(nodes=[_col_span_table()]))
    model = import_latex(raw.encode(), "span.tex")
    tbl = next(n for n in model.nodes if isinstance(n, Table))
    assert tbl.cells[0][0].col_span == 2
    assert tbl.cells[0][0].text == "Merged"
    assert tbl.cells[0][1].covered is True


def test_latex_row_span_import_roundtrip():
    from app.exporters.latex_exporter import export_latex
    from app.importers.latex_importer import import_latex

    raw = export_latex(DocumentModel(nodes=[_row_span_table()]))
    model = import_latex(raw.encode(), "span.tex")
    tbl = next(n for n in model.nodes if isinstance(n, Table))
    assert tbl.cells[0][0].row_span == 2
    assert tbl.cells[0][0].text == "Tall"
    assert tbl.cells[1][0].covered is True


# --------------------------------------------------------------------------
# TXT — HTML table for span
# --------------------------------------------------------------------------


def test_txt_col_span_native_html():
    from app.exporters.txt_exporter import export_txt

    out = export_txt(DocumentModel(nodes=[_col_span_table()]))
    assert 'colspan="2"' in out


# --------------------------------------------------------------------------
# RTF — clmgf merge
# --------------------------------------------------------------------------


def test_rtf_col_span_native():
    out = rtf_exporter.export_rtf(DocumentModel(nodes=[_col_span_table()])).decode(
        "utf-8", errors="replace"
    )
    assert "clmgf" in out
    assert "Merged" in out and "A" in out and "B" in out


def test_rtf_col_span_import_roundtrip():
    from app.importers.rtf_importer import import_rtf

    raw = rtf_exporter.export_rtf(DocumentModel(nodes=[_col_span_table()]))
    model = import_rtf(raw, "span.rtf")
    tbl = next(n for n in model.nodes if isinstance(n, Table))
    assert tbl.cells[0][0].col_span == 2
    assert tbl.cells[0][0].text == "Merged"
    assert tbl.cells[0][1].covered is True
    assert tbl.cells[1][0].text == "A"
    assert tbl.cells[1][1].text == "B"


# --------------------------------------------------------------------------
# PPTX — meta nodes only on dedicated slides
# --------------------------------------------------------------------------


def test_pptx_meta_nodes_not_duplicated_in_content():
    from app.core.document_model import Comment, Footnote

    model = DocumentModel(
        title="Meta",
        nodes=[
            Heading(level=2, text="Body"),
            Paragraph(text="Main content."),
            Comment(author="reviewer", anchor="p1", text="check totals"),
            Footnote(id="1", text="See appendix"),
        ],
    )
    xml = _pptx_slide_xml(pptx_exporter.export_pptx(model))
    assert xml.count("check totals") == 1
    assert xml.count("See appendix") == 1
    assert "Main content" in xml


def test_pptx_textbox_inline_on_content_slide():
    model = DocumentModel(
        title="Deck",
        nodes=[
            Heading(level=2, text="Section"),
            TextBox(text="Floating note", source="docx:txbx"),
        ],
    )
    xml = _pptx_slide_xml(pptx_exporter.export_pptx(model))
    assert xml.count("Floating note") >= 1
    assert "Text boxes" in xml or "Floating note" in xml
