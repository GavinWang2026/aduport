"""Independent deep golden model for ODT / JSON roundtrip tests."""

from __future__ import annotations

from aduport.core.document_model import (
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    Paragraph,
    PivotTable,
    Revision,
    Table,
    TableCell,
    TextBox,
)


def golden_deep_model() -> DocumentModel:
    return DocumentModel(
        title="DeepGolden",
        nodes=[
            Table(rows=[["Region", "Sales"], ["East", "100"]], name="Data"),
            Table(
                rows=[["Merged", ""], ["A", "B"]],
                cells=[
                    [
                        TableCell(text="Merged", col_span=2),
                        TableCell(covered=True, origin_row=0, origin_col=0),
                    ],
                    [TableCell(text="A"), TableCell(text="B")],
                ],
                name="SpanGrid",
                header_rows=0,
            ),
            Heading(level=2, text="Regional Summary"),
            Paragraph(text="Totals by region for the quarter."),
            Comment(author="reviewer", anchor="p1", text="check totals"),
            Footnote(id="1", text="See appendix"),
            Revision(change="insert", author="editor", text="new clause"),
            ContentControl(tag="status", alias="Status", control_type="dropdown", text="Draft"),
            TextBox(text="Sidebar note", source="docx:txbx"),
            PivotTable(
                name="SalesPivot",
                source_range="A1:B2",
                location="D3",
                row_fields=["Region"],
                data_fields=["Sales"],
            ),
        ],
    )
