"""Target format breadth: span tables use native renderers on all text channels."""

from __future__ import annotations

from aduport.core.converter import SUPPORTED_TARGET_FORMATS
from aduport.core.document_model import DocumentModel, Table, TableCell
from aduport.core.registry import ensure_builtin_plugins, get_exporter
from aduport.exporters.latex_exporter import export_latex
from aduport.exporters.md_exporter import export_md
from aduport.exporters.rtf_exporter import export_rtf
from aduport.exporters.txt_exporter import export_txt


def _span_table() -> Table:
    return Table(
        rows=[["Merged", ""], ["A", "B"]],
        cells=[
            [
                TableCell(text="Merged", col_span=2),
                TableCell(covered=True, origin_row=0, origin_col=0),
            ],
            [TableCell(text="A"), TableCell(text="B")],
        ],
        name="SpanGrid",
    )


def test_all_sixteen_targets_registered():
    ensure_builtin_plugins()
    for fmt in sorted(SUPPORTED_TARGET_FORMATS):
        assert get_exporter(fmt) is not None, fmt


def test_text_targets_emit_native_span_markers():
    model = DocumentModel(nodes=[_span_table()])
    md = export_md(model)
    txt = export_txt(model)
    latex = export_latex(model)
    rtf = export_rtf(model).decode()
    assert 'colspan="2"' in md
    assert 'colspan="2"' in txt
    assert r"\multicolumn{2}" in latex
    assert "clmgf" in rtf
