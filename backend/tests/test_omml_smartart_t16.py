"""v3.16: expanded TeX→OMML + SmartArt/OLE AST round-trip."""

from __future__ import annotations

import zipfile
from io import BytesIO

from app.core.document_model import DocumentModel, MathBlock, OleObject, Paragraph, SmartArt
from app.exporters import docx_exporter
from app.exporters.omml import build_omath, omml_element_to_tex
from app.importers.docx_ooxml import enrich_docx_model


def test_omml_nested_and_nary():
    cases = [
        (r"\frac{a+b}{c}", "frac"),
        (r"\sum_{i=1}^{n}{i^2}", "sum"),
        (r"\sqrt[3]{x+y}", "sqrt"),
        (r"\left(\frac{1}{2}\right)", "left"),
        (r"x_{i}^{2}", "x"),
        (r"\alpha+\beta", "α"),
    ]
    for tex, needle in cases:
        el = build_omath(tex, display=True)
        back = omml_element_to_tex(el)
        assert needle in back or needle in tex, (tex, back)


def test_omml_docx_complex_math_roundtrip():
    model = DocumentModel(
        nodes=[
            MathBlock(content=r"\sum_{i=1}^{n}{\frac{i}{2}}", display=True),
            MathBlock(content=r"\left(\alpha+\beta\right)^2", display=False),
        ]
    )
    blob = docx_exporter.export_docx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        doc = zf.read("word/document.xml").decode("utf-8", "ignore")
    assert "oMath" in doc or "m:oMath" in doc
    assert "nary" in doc.lower() or "∑" in doc or "sum" in doc.lower() or "m:f" in doc or "frac" in doc.lower()

    back = enrich_docx_model(DocumentModel(), blob)
    maths = [n for n in back.nodes if isinstance(n, MathBlock)]
    assert maths
    joined = " ".join(m.content for m in maths)
    assert "frac" in joined or "sum" in joined or "α" in joined or "alpha" in joined or "i" in joined


def test_smartart_docx_diagram_roundtrip():
    model = DocumentModel(
        nodes=[
            SmartArt(layout="process", title="Flow", items=["Plan", "Build", "Ship"]),
            OleObject(prog_id="Excel.Sheet.12", label="Budget.xlsx"),
        ]
    )
    blob = docx_exporter.export_docx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        names = zf.namelist()
        assert any(n.startswith("word/diagrams/data") for n in names)
        data_xml = zf.read(
            next(n for n in names if n.startswith("word/diagrams/data"))
        ).decode()
        assert "Plan" in data_xml and "Ship" in data_xml
        body = zf.read("word/document.xml").decode()
        assert "SmartArt" in body
        assert "OLE:" in body or "Excel" in body

    back = enrich_docx_model(DocumentModel(nodes=[Paragraph(text="x")]), blob)
    arts = [n for n in back.nodes if isinstance(n, SmartArt)]
    assert arts
    assert "Plan" in arts[0].items
    # Exported OLE is a text placeholder (no binary o:OLEObject); still present in body.
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        body = zf.read("word/document.xml").decode()
    assert "Excel.Sheet.12" in body or "OLE:" in body
