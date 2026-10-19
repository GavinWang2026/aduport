"""P1-4: spreadsheet value-export boundary (cells→rows, round-trip)."""

from __future__ import annotations

from aduport.core.document_model import DocumentModel, Table, TableCell
from aduport.exporters.xlsx_exporter import export_xlsx, import_xlsx


def test_export_xlsx_from_cells_when_rows_empty():
    model = DocumentModel(
        title="cells-only",
        nodes=[
            Table(
                rows=[],
                cells=[
                    [TableCell(text="A"), TableCell(text="B")],
                    [TableCell(text="1"), TableCell(text="2")],
                ],
            )
        ],
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    assert back.metadata.get("export_policy") == "values_only"
    assert isinstance(back.nodes[0], Table)
    assert back.nodes[0].rows == [["A", "B"], ["1", "2"]]


def test_xlsx_roundtrip_values():
    model = DocumentModel(
        nodes=[Table(rows=[["Name", "Qty"], ["widget", "3"], ["gizmo", "7"]])]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw)
    assert back.nodes[0].rows == [["Name", "Qty"], ["widget", "3"], ["gizmo", "7"]]
    assert "公式" in (back.metadata.get("note") or "")
