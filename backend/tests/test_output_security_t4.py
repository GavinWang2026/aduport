"""Output security regression: LaTeX primitives + XLSX merge/cap (T4 / expansion T32)."""

from __future__ import annotations

import zipfile
from io import BytesIO

from aduport.core.document_model import (
    CodeBlock,
    DocumentModel,
    MathBlock,
    Paragraph,
    Table,
    TableCell,
)
from aduport.exporters.latex_exporter import export_latex
from aduport.exporters.xlsx_exporter import export_xlsx


def test_latex_math_blocks_dangerous_commands():
    model = DocumentModel(
        nodes=[
            MathBlock(content=r"\write18{rm -rf /} x", display=True),
            MathBlock(content=r"\input{/etc/passwd}", display=False),
            CodeBlock(content="ok\n\\end{verbatim}\n\\write18{id}\nbad"),
        ]
    )
    tex = export_latex(model)
    assert r"\write18" not in tex
    assert r"\input{" not in tex
    assert tex.count(r"\end{verbatim}") == 1
    assert "[blocked]" in tex
    assert "[end-verbatim]" in tex


def test_latex_paragraph_still_escapes_specials():
    model = DocumentModel(nodes=[Paragraph(text=r"price is $5 & 10%")])
    tex = export_latex(model)
    assert r"\$" in tex
    assert r"\&" in tex
    assert r"\%" in tex


def test_xlsx_merge_cols_and_number_types():
    cells = [
        [
            TableCell(text="A", row_span=1, col_span=2),
            TableCell(text="", covered=True, origin_row=0, origin_col=0),
        ],
        [
            TableCell(text="42"),
            TableCell(text="2024-01-15"),
        ],
    ]
    model = DocumentModel(
        nodes=[
            Table(
                name="Merged",
                rows=[["A", ""], ["42", "2024-01-15"]],
                cells=cells,
            )
        ]
    )
    blob = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        xml = zf.read("xl/worksheets/sheet1.xml").decode()
    assert "<mergeCells" in xml
    assert 'ref="A1:B1"' in xml
    assert "<cols>" in xml
    assert 'customWidth="1"' in xml
    # number cell without inlineStr
    assert '<c r="A2"><v>42</v></c>' in xml or 'r="A2"' in xml and ">42<" in xml


def test_xlsx_sheet_cap_above_eight(monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "xlsx_max_sheets", 64)
    tables = [Table(name=f"S{i}", rows=[[str(i)]]) for i in range(12)]
    model = DocumentModel(nodes=tables)
    blob = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        sheets = [n for n in zf.namelist() if n.startswith("xl/worksheets/sheet")]
    assert len(sheets) >= 12
