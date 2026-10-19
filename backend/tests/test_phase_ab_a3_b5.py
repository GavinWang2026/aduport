"""Phase A3/A4 + B1–B5 regression gates."""

from __future__ import annotations

import io
import zipfile

import pytest

from aduport.core.document_model import (
    DocumentModel,
    Image,
    MathBlock,
    OleObject,
    Paragraph,
    SmartArt,
)
from aduport.core.exceptions import OcrUnavailableError
from aduport.core.format_detect import detect_source_format
from aduport.exporters import latex_exporter, odt_exporter, pptx_exporter
from aduport.exporters.omml import extract_math_blocks_from_document_xml


TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_a3_pptx_smartart_ole_not_silent():
    model = DocumentModel(
        nodes=[
            Paragraph(text="body"),
            SmartArt(title="SENT_SA_X", layout="list", items=["a", "b"]),
            OleObject(prog_id="Package", label="SENT_OLE_Y", data=b"abc"),
        ]
    )
    blob = pptx_exporter.export_pptx(model)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        text = b"".join(zf.read(n) for n in zf.namelist() if n.endswith(".xml")).decode(
            "utf-8", "ignore"
        )
    assert "SENT_SA_X" in text
    assert "SENT_OLE_Y" in text
    warns = model.metadata.get("export_warnings") or []
    assert any("SmartArt" in w for w in warns)
    assert any("OleObject" in w for w in warns)


def test_a4_omml_uses_xml_safe_not_crash():
    # Malformed XML → empty list (no raise)
    assert extract_math_blocks_from_document_xml(b"<not-xml") == []
    xml = (
        b'<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
        b'wordprocessingml/2006/main" xmlns:m="http://schemas.openxmlformats.org/'
        b'officeDocument/2006/math"><w:body><m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>'
        b"</w:body></w:document>"
    )
    blocks = extract_math_blocks_from_document_xml(xml)
    assert blocks and "x" in blocks[0][0]


def test_b1_odt_embeds_image_and_mathml():
    model = DocumentModel(
        nodes=[
            Image(data=TINY_PNG, alt_text="SENT_IMG_ODT", mime_type="image/png"),
            MathBlock(content="SENT_MATH_ODT", display=True),
        ]
    )
    blob = odt_exporter.export_odt(model)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = zf.namelist()
        assert any(n.startswith("Pictures/") for n in names)
        content = zf.read("content.xml").decode("utf-8", "ignore")
    assert "draw:image" in content
    assert "SENT_IMG_ODT" in content
    assert "math:math" in content
    assert "SENT_MATH_ODT" in content
    assert 'encoding="application/x-tex"' in content


def test_b1_latex_includegraphics_and_assets():
    model = DocumentModel(
        nodes=[Image(data=TINY_PNG, alt_text="SENT_IMG_TEX", mime_type="image/png")]
    )
    tex = latex_exporter.export_latex(model)
    assert r"\includegraphics" in tex
    assert "aduport_img_0.png" in tex
    assets = model.metadata.get("latex_assets") or {}
    assert "aduport_img_0.png" in assets
    assert assets["aduport_img_0.png"]["bytes"] == len(TINY_PNG)


def test_b3_ocr_unavailable_dict_flags():
    err = OcrUnavailableError("need ocr")
    d = err.to_dict()
    assert d["ocr_required"] is True
    assert d["code"] == "ocr_unavailable"
    assert "install" in d and "ocr-" in str(d["install"])


def test_b4_health_exposes_pdf_export(client=None):
    from fastapi.testclient import TestClient

    from main import aduport

    with TestClient(app) as tc:
        r = tc.get("/api/health/detail")
        assert r.status_code == 200
        body = r.json()
        assert "pdf_export" in body
        assert "weasyprint_available" in body["pdf_export"]
        assert body["pdf_export"]["effective"] in ("weasyprint", "pdf_simple")


def test_b5_xlsb_magic_and_ext():
    assert detect_source_format("a.xlsb") == "xlsb"
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "native/anydoc/tests/fixtures/xlsb/handmade-sheet.xlsb"
    if not p.exists():
        pytest.skip("xlsb fixture missing")
    data = p.read_bytes()
    assert detect_source_format("blob.bin", data) == "xlsb"
    assert detect_source_format(None, data) == "xlsb"


def test_b5_frontend_labels_text_level():
    from pathlib import Path

    types = (Path(__file__).resolve().parents[2] / "frontend/src/types.ts").read_text(
        encoding="utf-8"
    )
    assert "文本级" in types
    assert "text-level" in types
