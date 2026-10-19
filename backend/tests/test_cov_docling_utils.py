"""T3-#15: cover _docling_utils without requiring a real Docling install."""

from __future__ import annotations

from types import SimpleNamespace

from aduport.core.document_model import CodeBlock, Heading, Image, ListItem, Paragraph, Table
from aduport.importers import _docling_utils as du


class _FakeImg:
    def save(self, buf, format="PNG"):
        buf.write(b"\x89PNG\r\n\x1a\n")


def test_heading_level_and_list_code_flags():
    assert du._heading_level(SimpleNamespace(label="heading_3")) == 3
    assert du._heading_level(SimpleNamespace(label="title")) == 1
    assert du._heading_level(SimpleNamespace(label="section_header")) == 2
    assert du._heading_level(SimpleNamespace(label="x", level=5)) == 5
    assert du._heading_level(SimpleNamespace()) == 1
    assert du._is_list_item(SimpleNamespace(label="list_item"))
    assert du._is_code(SimpleNamespace(label="code_block"))


def test_table_from_grid_and_dataframe():
    grid_item = SimpleNamespace(
        data=SimpleNamespace(grid=[["A", "B"], ["1", "2"]], table_cells=None)
    )
    t = du._table_from_item(grid_item)
    assert t is not None and t.rows[0] == ["A", "B"]
    assert t.cells and t.cells[0][0].text == "A"

    class _DF:
        columns = SimpleNamespace(tolist=lambda: ["H1", "H2"])
        values = SimpleNamespace(tolist=lambda: [["v1", "v2"]])

    df_item = SimpleNamespace(
        data=SimpleNamespace(
            grid=["x"],
            table_cells=None,
            export_to_dataframe=lambda: _DF(),
        )
    )
    t2 = du._table_from_item(df_item)
    assert t2 is not None and t2.rows[0] == ["H1", "H2"]
    assert du._table_from_item(SimpleNamespace(data=None)) is None
    assert du._cell_text(None) == ""
    assert du._cell_text("  x  ") == "x"
    assert du._cell_text(SimpleNamespace(text="z")) == "z"


def test_image_from_item():
    item = SimpleNamespace(image=_FakeImg(), text="pic")
    img = du._image_from_item(item)
    assert isinstance(img, Image) and img.data.startswith(b"\x89PNG")
    assert du._image_from_item(SimpleNamespace()) is None
    assert du._image_from_item(SimpleNamespace(image=b"raw")) is not None


def test_docling_document_to_model_from_texts():
    items = [
        SimpleNamespace(label="heading_1", text="Title"),
        SimpleNamespace(label="paragraph", text="Body SENT"),
        SimpleNamespace(label="list_item", text="li", level=0),
        SimpleNamespace(label="code", text="print(1)", code_language="py"),
        SimpleNamespace(
            label="table",
            data=SimpleNamespace(grid=[["h"], ["v"]], table_cells=None),
        ),
        SimpleNamespace(label="picture", image=_FakeImg(), text="fig"),
        SimpleNamespace(label="footnote", text="fn-body", id="7"),
        SimpleNamespace(label="quote", text="quoted-line"),
        SimpleNamespace(label="formula", text="a^2"),
        SimpleNamespace(label="page_break"),
    ]
    doc = SimpleNamespace(texts=items)
    model = du.docling_document_to_model(doc, title="T")
    kinds = [type(n).__name__ for n in model.nodes]
    assert "Heading" in kinds and "Paragraph" in kinds
    assert "ListItem" in kinds and "CodeBlock" in kinds
    assert "Table" in kinds and "Image" in kinds
    assert "Footnote" in kinds and "Blockquote" in kinds
    assert "MathBlock" in kinds and "HorizontalRule" in kinds
    assert any(isinstance(n, Heading) and "Title" in n.plain_text() for n in model.nodes)


def test_docling_markdown_fallback():
    doc = SimpleNamespace(
        body=None,
        texts=[],
        export_to_markdown=lambda: "# H\n\npara\n\n- item\n\n```py\nx=1\n```\n\n|a|b|\n|--|--|\n|1|2|\n",
    )
    model = du.docling_document_to_model(doc, title="md")
    assert any(isinstance(n, Heading) for n in model.nodes)
    assert any(isinstance(n, ListItem) for n in model.nodes)
    assert any(isinstance(n, CodeBlock) for n in model.nodes)
    assert any(isinstance(n, Table) for n in model.nodes)
    tbl = next(n for n in model.nodes if isinstance(n, Table))
    assert tbl.cells


def test_docling_body_children_path():
    body = SimpleNamespace(
        children=[SimpleNamespace(label="paragraph", text="via-body", orig=None)]
    )
    model = du.docling_document_to_model(SimpleNamespace(body=body), title="b")
    assert any(isinstance(n, Paragraph) and "via-body" in n.plain_text() for n in model.nodes)


def test_extract_text_orig_fallback():
    assert du._extract_text(SimpleNamespace(orig="orig-text", text="")) == "orig-text"
    assert du._extract_text(SimpleNamespace()) == ""
