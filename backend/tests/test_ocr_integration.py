"""Optional live OCR integration — skipped when engines are not installed.

Run manually on GPU hosts after:
  pip install 'md-bridge[ocr-mineru]' and/or 'md-bridge[ocr-paddle]'
  MDBRIDGE_OCR_ENABLED=true
"""

from __future__ import annotations

import importlib.util

import pytest

from app.config import settings
from app.core.document_model import DocumentModel
from app.importers import ocr_engine as oe


def _minimal_pdf_bytes() -> bytes:
    """Tiny digital PDF (not a real scan) — used only when engines accept PDF path."""
    body = b"BT /F1 12 Tf 72 720 Td (ScanFixture Hello OCR Sample) Tj ET"
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
        b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
        b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources<< /Font<< /F1 5 0 R >> >> >>endobj\n"
        + f"4 0 obj<< /Length {len(body)} >>stream\n".encode()
        + body
        + b"\nendstream\nendobj\n"
        b"5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"trailer<< /Size 6 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )


@pytest.mark.integration
def test_mineru_live_if_installed(monkeypatch):
    if not oe.MinerUEngine().available():
        pytest.skip("mineru not installed")
    monkeypatch.setattr(settings, "ocr_min_chars", 1)
    monkeypatch.setattr(settings, "ocr_timeout_seconds", 180)
    monkeypatch.setattr(settings, "mineru_formula", False)
    # Prefer project-local caches (PaddleX/ModelScope write under HOME)
    from pathlib import Path

    home = Path(__file__).resolve().parents[2] / ".paddle_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "ocr_home", str(home))
    model = oe.import_pdf_with_ocr(_minimal_pdf_bytes(), "live.pdf", force_engine="mineru")
    assert isinstance(model, DocumentModel)
    text = " ".join(
        (n.plain_text() if hasattr(n, "plain_text") else str(getattr(n, "text", "") or ""))
        for n in model.nodes
    )
    assert len(text) >= 1


@pytest.mark.integration
def test_paddleocr_vl_live_if_installed(monkeypatch):
    if not oe.PaddleOcrVlEngine().available():
        pytest.skip("paddleocr/paddlex not installed")
    monkeypatch.setattr(settings, "ocr_min_chars", 1)
    monkeypatch.setattr(settings, "ocr_timeout_seconds", 300)
    from pathlib import Path

    home = Path(__file__).resolve().parents[2] / ".paddle_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "ocr_home", str(home))
    model = oe.import_pdf_with_ocr(
        _minimal_pdf_bytes(), "live.pdf", force_engine="paddleocr_vl"
    )
    assert isinstance(model, DocumentModel)
    text = " ".join(
        (n.plain_text() if hasattr(n, "plain_text") else str(getattr(n, "text", "") or ""))
        for n in model.nodes
    )
    assert len(text.strip()) >= 1


def test_ocr_no_engine_raises_clear_error(monkeypatch):
    class Dead(oe.OcrEngine):
        name = "x"

        def available(self) -> bool:
            return False

        def pdf_to_markdown(self, source_bytes: bytes, filename: str) -> str:
            raise RuntimeError("no")

    monkeypatch.setitem(oe._ENGINES, "mineru", Dead())
    monkeypatch.setitem(oe._ENGINES, "paddleocr_vl", Dead())
    monkeypatch.setitem(oe._ENGINES, "docling", Dead())
    from app.core.exceptions import InvalidDocumentError

    with pytest.raises(InvalidDocumentError, match="无可用引擎"):
        oe.import_pdf_with_ocr(b"%PDF", "x.pdf", force_engine="mineru")


def test_packages_probe_documented():
    """CI documents probe results; does not fail when extras missing."""
    status = {
        "mineru": importlib.util.find_spec("mineru") is not None,
        "paddleocr": importlib.util.find_spec("paddleocr") is not None,
        "paddlex": importlib.util.find_spec("paddlex") is not None,
    }
    avail = oe.list_ocr_engines()["available"]
    assert set(avail) >= {"mineru", "paddleocr_vl", "docling"}
    # Keep print-friendly for logs
    assert isinstance(status["mineru"], bool)
