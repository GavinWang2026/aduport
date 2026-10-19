"""Golden sample: full _AST meta roundtrip through xlsx export/import."""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from aduport.config import settings
from aduport.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    ListItem,
    MathBlock,
    Paragraph,
    PivotTable,
    Revision,
    Table,
    TableCell,
    TextBox,
)
from aduport.exporters.xlsx_exporter import export_xlsx, import_xlsx

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
    b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture(autouse=True)
def _enable_xlsx_ast_sheet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "xlsx_ast_sheet", True)


def _full_ast_model() -> DocumentModel:
    return DocumentModel(
        title="ASTGolden",
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
            ),
            Revision(change="insert", author="alice", anchor="p1", text="added"),
            ContentControl(tag="t1", alias="Title", control_type="plain", text="ctrl"),
            TextBox(text="box", source="docx:body", x_mm=1.0, y_mm=2.0),
            PivotTable(
                name="P1",
                source_range="A1:B2",
                location="D3",
                row_fields=["Region"],
                data_fields=["Sales"],
            ),
            Comment(author="bob", anchor="p2", text="review note"),
            Footnote(id="1", text="fn body"),
            MathBlock(content="E=mc^2", display=True),
            Image(data=_TINY_PNG, alt_text="pic", mime_type="image/png", wrap="left"),
            HtmlBlock(content="<p>Hello <b>AST</b></p>"),
            CodeBlock(lang="python", content='print("golden")'),
            Blockquote(paragraphs=["First line.", "Second line."]),
            Heading(level=3, text="AST Heading", anchor="h-ast"),
            Paragraph(text="AST paragraph body."),
            ListItem(text="Task done", ordered=False, checked=True),
            HorizontalRule(),
        ],
    )


def test_xlsx_ast_full_meta_roundtrip_golden():
    """All _AST node types survive export_xlsx → import_xlsx."""
    model = _full_ast_model()
    raw = export_xlsx(model)
    assert raw[:2] == b"PK"

    with zipfile.ZipFile(BytesIO(raw)) as zf:
        wb = zf.read("xl/workbook.xml").decode()
        assert "_AST" in wb
        ast_xml = ""
        for path in sorted(zf.namelist()):
            if not path.startswith("xl/worksheets/sheet"):
                continue
            chunk = zf.read(path).decode()
            if "node_type" in chunk and "revision" in chunk:
                ast_xml = chunk
                break
    assert ast_xml
    for token in (
        "revision",
        "content_control",
        "text_box",
        "pivot_table",
        "comment",
        "footnote",
        "math_block",
        "image",
        "html_block",
        "code_block",
        "blockquote",
        "heading",
        "paragraph",
        "list_item",
        "horizontal_rule",
        "table_cell",
    ):
        assert token in ast_xml, token

    back = import_xlsx(raw, "golden_ast.xlsx")
    meta = back.metadata or {}
    assert meta.get("revisions_imported") == 1
    assert meta.get("content_controls_imported") == 1
    assert meta.get("textboxes_imported") == 1
    assert meta.get("pivots_ast_imported") == 1
    assert meta.get("comments_imported") == 1
    assert meta.get("footnotes_imported") == 1
    assert meta.get("math_imported") == 1
    assert meta.get("images_imported") == 1
    assert meta.get("html_blocks_imported") == 1
    assert meta.get("code_blocks_imported") == 1
    assert meta.get("blockquotes_imported") == 1
    assert meta.get("headings_imported") == 1
    assert meta.get("paragraphs_imported") == 1
    assert meta.get("list_items_imported") == 1
    assert meta.get("horizontal_rules_imported") == 1
    assert meta.get("table_cells_imported") == 4

    assert sum(isinstance(n, Revision) for n in back.nodes) == 1
    assert sum(isinstance(n, ContentControl) for n in back.nodes) == 1
    assert sum(isinstance(n, TextBox) for n in back.nodes) == 1
    pivots = [n for n in back.nodes if isinstance(n, PivotTable)]
    assert len(pivots) == 1 and pivots[0].name == "P1"
    comments = [n for n in back.nodes if isinstance(n, Comment)]
    assert comments and comments[0].text == "review note"
    images = [n for n in back.nodes if isinstance(n, Image)]
    assert images and images[0].alt_text == "pic" and images[0].data == _TINY_PNG
    htmls = [n for n in back.nodes if isinstance(n, HtmlBlock)]
    assert htmls and "AST" in htmls[0].content
    codes = [n for n in back.nodes if isinstance(n, CodeBlock)]
    assert codes and codes[0].lang == "python" and 'print("golden")' in codes[0].content
    bqs = [n for n in back.nodes if isinstance(n, Blockquote)]
    assert bqs and "First line." in bqs[0].paragraphs and "Second line." in bqs[0].paragraphs
    heads = [n for n in back.nodes if isinstance(n, Heading)]
    assert heads and heads[0].level == 3 and heads[0].plain_text() == "AST Heading"
    paras = [n for n in back.nodes if isinstance(n, Paragraph)]
    assert paras and paras[0].plain_text() == "AST paragraph body."
    items = [n for n in back.nodes if isinstance(n, ListItem)]
    assert items and items[0].plain_text() == "Task done" and items[0].checked is True
    assert sum(isinstance(n, HorizontalRule) for n in back.nodes) == 1
    span_tbl = next(
        (n for n in back.nodes if isinstance(n, Table) and n.name == "SpanGrid"),
        None,
    )
    assert span_tbl and span_tbl.cells
    assert span_tbl.cells[0][0].col_span == 2
    assert span_tbl.cells[0][1].covered is True
