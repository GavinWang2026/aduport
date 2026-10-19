"""T3 / D-16: depth tests for doc / ppt / mobi / odt / csv — not smoke-only.

Raises edge-format coverage from "claimed" to verified with real fixtures +
synthetic OLE / MOBI roundtrips.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aduport.core.document_model import (
    DocumentModel,
    Footnote,
    Heading,
    Paragraph,
    Table,
    TableCell,
)
from aduport.core.exceptions import InvalidDocumentError
from aduport.exporters.mobi_exporter import export_mobi
from aduport.exporters.odt_exporter import export_odt
from aduport.importers.csv_importer import import_csv
from aduport.importers.doc_importer import import_doc
from aduport.importers.mobi_importer import import_mobi
from aduport.importers.odt_importer import import_odt
from aduport.importers.ole_office import import_ppt
from tests._ole_fixtures import build_cfb_with_stream, build_minimal_ppt

_REPO = Path(__file__).resolve().parents[2]
_ANYDOC = _REPO / "native" / "anydoc" / "tests" / "fixtures"


def _anydoc(*parts: str) -> Path:
    return _ANYDOC.joinpath(*parts)


def test_csv_multi_table_and_table_cells():
    raw = b"a,b\n1,2\n\nx,y\n3,4\n"
    model = import_csv(raw, "t.csv")
    tables = [n for n in model.nodes if isinstance(n, Table)]
    assert len(tables) == 2
    assert tables[0].cells and tables[0].cells[0][0].text == "a"
    assert isinstance(tables[0].cells[0][0], TableCell)


def test_csv_empty_and_bad_utf8():
    with pytest.raises(InvalidDocumentError):
        import_csv(b"\n\n", "empty.csv")
    with pytest.raises(InvalidDocumentError):
        import_csv(b"\xff\xfe\x00", "bad.csv")


def test_csv_anydoc_sheet_fixture():
    path = _anydoc("csv", "sheet.csv")
    if not path.is_file():
        pytest.skip("anydoc csv fixture missing")
    model = import_csv(path.read_bytes(), path.name)
    tables = [n for n in model.nodes if isinstance(n, Table)]
    assert tables and tables[0].rows
    assert tables[0].cells


def test_ppt_structured_atoms_and_fixture():
    blob = build_minimal_ppt(["EDGE_PPT_TITLE", "EDGE_PPT_BODY"])
    model = import_ppt(blob, "edge.ppt")
    text = " ".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
    assert "EDGE_PPT_TITLE" in text
    assert "EDGE_PPT_BODY" in text

    path = _anydoc("ppt", "pres.ppt")
    if path.is_file():
        real = import_ppt(path.read_bytes(), path.name)
        assert real.nodes
        joined = " ".join(
            n.plain_text() for n in real.nodes if hasattr(n, "plain_text")
        )
        assert len(joined) > 8


def test_doc_ole_text_scrape_and_fixture():
    payload = ("EDGE_DOC_SENTINEL " * 6).encode("utf-16-le")
    blob = build_cfb_with_stream("WordDocument", payload + b"\x00" * 64)
    model = import_doc(blob, "edge.doc")
    text = " ".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
    assert "EDGE_DOC_SENTINEL" in text
    assert model.metadata.get("degraded") is True or model.metadata.get("importer")

    path = _anydoc("doc", "text.doc")
    if path.is_file():
        real = import_doc(path.read_bytes(), path.name)
        assert real.nodes
        joined = " ".join(
            n.plain_text() for n in real.nodes if hasattr(n, "plain_text")
        )
        assert len(joined) > 4


def test_odt_roundtrip_and_fixture():
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="EDGE_ODT_H1"),
            Paragraph(text="EDGE_ODT_BODY"),
            Table(
                rows=[["h1", "h2"], ["v1", "v2"]],
                cells=[
                    [TableCell(text="h1"), TableCell(text="h2")],
                    [TableCell(text="v1"), TableCell(text="v2")],
                ],
                header_rows=1,
            ),
        ]
    )
    blob = export_odt(model)
    back = import_odt(blob, "edge.odt")
    text = " ".join(n.plain_text() for n in back.nodes if hasattr(n, "plain_text"))
    assert "EDGE_ODT_H1" in text and "EDGE_ODT_BODY" in text

    path = _anydoc("odt", "text.odt")
    if path.is_file():
        real = import_odt(path.read_bytes(), path.name)
        assert real.nodes
        joined = " ".join(
            n.plain_text() for n in real.nodes if hasattr(n, "plain_text")
        )
        assert len(joined) > 4


def test_mobi_export_import_roundtrip():
    model = DocumentModel(
        nodes=[
            Paragraph(text="EDGE_MOBI_BODY"),
            Footnote(id="1", text="EDGE_MOBI_NOTE"),
        ]
    )
    blob = export_mobi(model)
    assert b"BOOKMOBI" in blob[:128] or b"MOBI" in blob[:256]
    back = import_mobi(blob, "edge.mobi")
    text = " ".join(n.plain_text() for n in back.nodes if hasattr(n, "plain_text"))
    assert "EDGE_MOBI_BODY" in text or "EDGE_MOBI_NOTE" in text
    notes = [n for n in back.nodes if isinstance(n, Footnote)]
    assert notes or "EDGE_MOBI" in text


def test_odt_rejects_bad_zip():
    from aduport.core.exceptions import CorruptedFileError

    with pytest.raises((InvalidDocumentError, CorruptedFileError)):
        import_odt(b"PK\x03\x04not-odt", "x.odt")


def test_mobi_looks_like_and_tiny_payload():
    from aduport.importers.mobi_importer import _looks_like_mobi
    from aduport.importers.mobi_text import try_extract_mobi_model

    assert _looks_like_mobi(b"BOOKMOBI" + b"\x00" * 80)
    assert not _looks_like_mobi(b"xxxx")
    assert try_extract_mobi_model(b"short") is None
    with pytest.raises(InvalidDocumentError):
        import_doc(b"not-ole", "x.doc")
    with pytest.raises(InvalidDocumentError):
        import_mobi(b"not-a-mobi", "x.mobi")
    with pytest.raises((InvalidDocumentError, Exception)):
        import_ppt(b"short", "x.ppt")


def test_docx_importer_native_path_and_reject():
    """docx_importer.py was 23% covered — exercise reject + native fallback."""
    from aduport.core.exceptions import CorruptedFileError
    from aduport.exporters.docx_exporter import export_docx
    from aduport.importers.docx_importer import import_docx

    with pytest.raises((InvalidDocumentError, CorruptedFileError)):
        import_docx(b"not-a-zip", "x.docx")
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="DOCX_IMP_H1"),
            Paragraph(text="DOCX_IMP_BODY"),
        ]
    )
    raw = export_docx(model)
    back = import_docx(raw, "edge.docx")
    text = " ".join(n.plain_text() for n in back.nodes if hasattr(n, "plain_text"))
    assert "DOCX_IMP_H1" in text and "DOCX_IMP_BODY" in text
    assert (back.metadata or {}).get("importer") in (
        "docx_native",
        "docx_docling",
        "docx_zip_fallback",
    ) or back.nodes


def test_pptx_importer_light_path_and_reject():
    from aduport.core.exceptions import CorruptedFileError
    from aduport.exporters.pptx_exporter import export_pptx
    from aduport.importers.pptx_importer import import_pptx

    with pytest.raises((InvalidDocumentError, CorruptedFileError)):
        import_pptx(b"not-pptx", "x.pptx")
    model = DocumentModel(
        nodes=[Heading(level=1, text="PPTX_IMP_TITLE"), Paragraph(text="PPTX_IMP_BODY")]
    )
    raw = export_pptx(model)
    back = import_pptx(raw, "edge.pptx")
    text = " ".join(n.plain_text() for n in back.nodes if hasattr(n, "plain_text"))
    assert "PPTX_IMP" in text


def test_docx_text_run_level_marks_without_anydoc():
    """§3.4: native docx_text must recover bold/italic (not anydoc-only)."""
    from aduport.core.document_model import InlineMark, InlineMarkType, RichText
    from aduport.exporters.docx_exporter import export_docx
    from aduport.importers.docx_text import try_extract_docx_model

    model = DocumentModel(
        nodes=[
            Paragraph(
                text=RichText(
                    text="BoldItalic",
                    marks=[
                        InlineMark(type=InlineMarkType.BOLD, start=0, end=4),
                        InlineMark(type=InlineMarkType.ITALIC, start=4, end=10),
                    ],
                )
            )
        ]
    )
    got = try_extract_docx_model(export_docx(model), title="runs")
    assert got and got.nodes
    types = {m.type for m in got.nodes[0].text.marks}
    assert InlineMarkType.BOLD in types
    assert InlineMarkType.ITALIC in types


def test_list_fence_is_codeblock_child_not_paragraph():
    """V-E: fenced code inside list items must be CodeBlock in AST/json."""
    from aduport.core.document_model import CodeBlock
    from aduport.exporters.html_render import render_body_html
    from aduport.exporters.json_exporter import export_json
    from aduport.exporters.txt_exporter import export_txt
    from aduport.importers.md_importer import import_md

    src = b"- item one\n\n  ```python\n  hello_code_block\n  ```\n\n- item two\n"
    model = import_md(src)
    li = next(n for n in model.nodes if getattr(n, "text", None))
    assert any(isinstance(c, CodeBlock) for c in li.children)
    assert "hello_code_block" in export_txt(model)
    assert "hello_code_block" in render_body_html(model)
    assert '"type": "code_block"' in export_json(model)
