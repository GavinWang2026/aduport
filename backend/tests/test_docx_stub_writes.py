"""§2.2 DOCX write for priority AST stubs (sectPr / header / field / bookmark / …)."""

from __future__ import annotations

import io
import re
import zipfile

from app.core.document_model import (
    Bookmark,
    DocumentModel,
    FieldCode,
    HeaderFooter,
    Numbering,
    PageSetup,
    Paragraph,
    RunStyle,
    Section,
)
from app.exporters.docx_exporter import export_docx


def _parts(blob: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in zf.namelist():
            if name.startswith("word/") and name.endswith(".xml"):
                out[name] = zf.read(name).decode("utf-8", "ignore")
    return out


def test_docx_writes_section_pagesetup_header_field_bookmark_style_numbering():
    model = DocumentModel(
        title="Contract",
        nodes=[
            Section(title="Article I", props={"cols": 2}),
            PageSetup(
                page_size="Letter",
                orientation="landscape",
                margins_mm={"top": 20, "bottom": 20, "left": 25, "right": 25},
            ),
            HeaderFooter(kind="header", text="HDR_ALPHA"),
            HeaderFooter(kind="footer", text="FTR_BETA"),
            FieldCode(field_type="PAGE", instruction="PAGE", result_text="1"),
            Bookmark(name="bm_clause", anchor="clause-1"),
            RunStyle(
                style_id="EmphasisCustom",
                name="Emphasis Custom",
                props={"bold": True, "italic": True},
            ),
            Numbering(
                num_id="9",
                abstract_num_id="8",
                levels=[{"ilvl": 0, "fmt": "decimal", "text": "%1."}],
            ),
            Paragraph(text="body"),
        ],
    )
    parts = _parts(export_docx(model))
    doc = parts["word/document.xml"]
    sect = re.search(r"<w:sectPr[\s\S]*?</w:sectPr>", doc)
    assert sect, "missing w:sectPr"
    s = sect.group(0)
    assert 'w:orient="landscape"' in s
    assert 'w:num="2"' in s  # columns
    assert 'w:top="1134"' in s  # ~20mm

    assert "HDR_ALPHA" in parts["word/header1.xml"]
    assert "FTR_BETA" in parts["word/footer1.xml"]
    assert "PAGE" in parts["word/footer1.xml"] or "PAGE" in doc

    assert 'w:name="bm_clause"' in doc
    assert "w:bookmarkStart" in doc and "w:bookmarkEnd" in doc
    assert "Article I" in doc
    assert "EmphasisCustom" in parts["word/styles.xml"]
    assert 'w:numId="9"' in parts["word/numbering.xml"]
    assert 'w:abstractNumId="8"' in parts["word/numbering.xml"]


def test_docx_pagesetup_a4_portrait_default_path():
    model = DocumentModel(
        nodes=[
            PageSetup(page_size="A4", orientation="portrait"),
            Paragraph(text="p"),
        ]
    )
    parts = _parts(export_docx(model))
    sect = re.search(r"<w:sectPr[\s\S]*?</w:sectPr>", parts["word/document.xml"]).group(0)
    # A4 portrait ≈ 11906 x 16838 twips
    assert 'w:w="11906"' in sect or "11905" in sect
    assert "landscape" not in sect
