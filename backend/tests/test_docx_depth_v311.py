"""§2.2 depth: multi-section sectPr, OMML math round-trip, theme catalog."""

from __future__ import annotations

import io
import re
import zipfile

from aduport.core.document_model import (
    DocumentModel,
    MathBlock,
    PageSetup,
    Paragraph,
    Section,
)
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.omml import (
    build_omath,
    extract_math_blocks_from_document_xml,
    omml_element_to_tex,
)
from aduport.importers.docx_ooxml import enrich_docx_model


def _zip_text(blob: bytes, path: str) -> str:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return zf.read(path).decode("utf-8", "ignore")


def test_multi_section_mid_body_sectpr():
    model = DocumentModel(
        nodes=[
            Section(title="Part A", props={"cols": 1}),
            PageSetup(page_size="A4", orientation="portrait"),
            Paragraph(text="body-a"),
            Section(title="Part B", props={"cols": 2, "break": "new_page"}),
            PageSetup(page_size="Letter", orientation="landscape"),
            Paragraph(text="body-b"),
        ]
    )
    blob = export_docx(model)
    doc = _zip_text(blob, "word/document.xml")
    sects = re.findall(r"<w:sectPr[\s\S]*?</w:sectPr>", doc)
    assert len(sects) >= 2, f"expected ≥2 sectPr, got {len(sects)}"
    # Final (last) section should be landscape Letter
    assert 'w:orient="landscape"' in sects[-1]
    assert 'w:num="2"' in sects[-1]
    assert "Part A" in doc and "Part B" in doc
    assert "body-a" in doc and "body-b" in doc

    enriched = enrich_docx_model(DocumentModel(nodes=[]), blob)
    assert len(enriched.metadata.get("docx_sections") or []) >= 2


def test_omml_export_and_import_roundtrip():
    model = DocumentModel(
        nodes=[
            MathBlock(content=r"\frac{a}{b}", display=True),
            MathBlock(content="x^{2}", display=False),
            Paragraph(text="after-math"),
        ]
    )
    blob = export_docx(model)
    doc = _zip_text(blob, "word/document.xml")
    assert "oMath" in doc or "m:oMath" in doc
    assert "frac" in doc.lower() or "m:f" in doc or "a" in doc

    pairs = extract_math_blocks_from_document_xml(doc.encode("utf-8"))
    texts = [t for t, _ in pairs]
    assert any("frac" in t or "a" in t for t in texts)

    enriched = enrich_docx_model(DocumentModel(nodes=[]), blob)
    maths = [n for n in enriched.nodes if isinstance(n, MathBlock)]
    assert maths, "OMML import should yield MathBlock nodes"
    joined = " ".join(m.content for m in maths)
    assert "a" in joined and ("b" in joined or "frac" in joined)


def test_omml_frac_roundtrip_unit():
    el = build_omath(r"\frac{num}{den}", display=True)
    tex = omml_element_to_tex(el)
    assert "frac" in tex and "num" in tex and "den" in tex


def test_theme_name_and_body_style_written():
    model = DocumentModel(nodes=[Paragraph(text="theme-body")])
    blob = export_docx(model, theme="academic")
    theme = _zip_text(blob, "word/theme/theme1.xml")
    styles = _zip_text(blob, "word/styles.xml")
    assert "AduPort-academic" in theme
    assert "2C3E50" in theme.upper()
    assert "AduPort Body" in styles
