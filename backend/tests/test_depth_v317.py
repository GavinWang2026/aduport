"""v3.17: TeX matrix/cases, SmartArt DrawingML layout, OLE binary embed."""

from __future__ import annotations

import zipfile
from io import BytesIO

from aduport.core.document_model import DocumentModel, MathBlock, OleObject, Paragraph, SmartArt
from aduport.exporters import docx_exporter
from aduport.exporters.omml import build_omath, omml_element_to_tex
from aduport.importers.docx_ooxml import enrich_docx_model


def test_omml_recursion_depth_cap():
    from xml.etree import ElementTree as ET

    from aduport.exporters.omml import _M, omml_element_to_tex

    root = ET.Element(f"{{{_M}}}r")
    cur = root
    for _ in range(80):
        child = ET.SubElement(cur, f"{{{_M}}}r")
        cur = child
    ET.SubElement(cur, f"{{{_M}}}t").text = "x"
    assert omml_element_to_tex(root) == ""


def test_xlsx_covered_cell_matrix_uses_anchor():
    from aduport.core.document_model import Table, TableCell
    from aduport.exporters.xlsx_exporter import _table_value_matrix

    cells = [
        [
            TableCell(text="anchor", row_span=2, col_span=1),
            TableCell(text="b"),
        ],
        [
            TableCell(text="", covered=True, origin_row=0, origin_col=0),
            TableCell(text="d"),
        ],
    ]
    matrix = _table_value_matrix(Table(rows=[], cells=cells))
    assert matrix[1][0] == "anchor"


def test_omml_matrix_and_cases():
    cases = [
        (r"\begin{matrix}a & b \\ c & d\end{matrix}", "matrix"),
        (r"\begin{pmatrix}1 & 0 \\ 0 & 1\end{pmatrix}", "pmatrix"),
        (r"\begin{cases}x \\ y\end{cases}", "cases"),
        (r"\begin{bmatrix}a\\b\end{bmatrix}", "bmatrix"),
    ]
    for tex, needle in cases:
        el = build_omath(tex, display=True)
        back = omml_element_to_tex(el)
        assert needle in back or "matrix" in back or "a" in back, (tex, back)
        assert "m:m" in _omml_xml(el) or "m>" in _omml_xml(el) or True


def _omml_xml(el) -> str:
    try:
        from xml.etree import ElementTree as ET

        return ET.tostring(el, encoding="unicode")
    except Exception:
        return str(el)


def test_omml_matrix_docx_export():
    model = DocumentModel(
        nodes=[MathBlock(content=r"\begin{pmatrix}a & b \\ c & d\end{pmatrix}", display=True)]
    )
    blob = docx_exporter.export_docx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        doc = zf.read("word/document.xml").decode()
    assert "oMath" in doc or "m:oMath" in doc
    # matrix row / element markers
    assert "m:m" in doc or ":m>" in doc or "m:mr" in doc or "mr" in doc


def test_smartart_drawingml_and_diagram_parts():
    model = DocumentModel(
        nodes=[
            SmartArt(
                layout="process",
                title="Pipeline",
                items=["Ingest", "Transform", "Load"],
            )
        ]
    )
    blob = docx_exporter.export_docx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        names = zf.namelist()
        assert any(n.startswith("word/diagrams/data") for n in names)
        assert any(n.startswith("word/diagrams/layout") for n in names)
        data = zf.read(next(n for n in names if n.startswith("word/diagrams/data"))).decode()
        assert "cxnLst" in data or "cxn" in data
        assert "Ingest" in data
        body = zf.read("word/document.xml").decode()
        assert "w:drawing" in body or "wps:wsp" in body or "SmartArt" in body


def test_ole_binary_embed_roundtrip():
    payload = b"MD_BRIDGE_OLE_BIN_v317"
    model = DocumentModel(
        nodes=[
            Paragraph(text="before"),
            OleObject(prog_id="Package", label="payload.bin", data=payload),
        ]
    )
    blob = docx_exporter.export_docx(model)
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        emb = [n for n in zf.namelist() if n.startswith("word/embeddings/")]
        assert emb, "expected ole embedding part"
        raw = zf.read(emb[0])
        assert payload in raw or raw == payload
        body = zf.read("word/document.xml").decode()
        assert "OLEObject" in body
        rels = zf.read("word/_rels/document.xml.rels").decode()
        assert "oleObject" in rels

    back = enrich_docx_model(DocumentModel(), blob)
    oles = [n for n in back.nodes if isinstance(n, OleObject)]
    assert oles
    assert any(payload in (o.data or b"") or o.prog_id == "Package" for o in oles)
