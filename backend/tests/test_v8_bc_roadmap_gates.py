"""v8 §B/C gates: PDF tri-state, trusted isolate, Weasy curve doc, docx style-map."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest

from aduport.config import settings
from aduport.core import convert_isolate
from aduport.core.document_model import Blockquote, Heading, ListItem, Paragraph
from aduport.importers import pdf_importer
from aduport.importers.docx_text import try_extract_docx_model


_ROOT = Path(__file__).resolve().parents[2]


def test_pdf_tristate_summary_contract():
    summary = pdf_importer.pdf_tristate_summary()
    assert set(summary) >= {"text", "docling", "ocr", "auto", "active"}
    assert "pypdfium2" in summary["text"].lower() or "text" in summary["text"].lower()


def test_pdf_engine_ocr_alias_normalizes():
    assert pdf_importer._normalize_pdf_engine("OCR") == "ocr"
    assert pdf_importer._normalize_pdf_engine("scan") == "ocr"
    assert pdf_importer._normalize_pdf_engine("docling") == "docling"


def test_trusted_client_skips_isolate_pool(monkeypatch):
    monkeypatch.setattr(settings, "convert_isolate", True)
    monkeypatch.setattr(settings, "convert_trusted_inprocess", True)
    monkeypatch.setattr(settings, "convert_trusted_clients", "warmup,ci")
    called = {}

    def _fake_convert(data, src, tgt, **kwargs):
        called["inprocess"] = True
        return b"ok"

    monkeypatch.setattr(convert_isolate, "convert", _fake_convert)
    out = convert_isolate.convert_isolated(
        b"# hi\n", "md", "txt", filename="t.md", client="ci"
    )
    assert out == b"ok"
    assert called.get("inprocess") is True


def test_untrusted_client_not_auto_trusted(monkeypatch):
    monkeypatch.setattr(settings, "convert_trusted_clients", "warmup")
    monkeypatch.setattr(settings, "convert_trusted_inprocess", True)
    assert convert_isolate._client_is_trusted("public") is False
    assert convert_isolate._client_is_trusted("warmup") is True


def test_docx_style_map_heading_and_quote_without_anydoc():
    """Minimal OOXML: Heading 1 + Quote → Heading/Blockquote via mammoth-lite map."""
    # Build via python-docx if available
    pytest.importorskip("docx")
    from docx import Document

    buf = BytesIO()
    doc = Document()
    doc.add_heading("Title Line", level=1)
    p = doc.add_paragraph("Quoted body")
    try:
        p.style = "Quote"
    except Exception:
        p.style = doc.styles["Normal"]
    doc.add_paragraph("Plain para")
    doc.save(buf)
    model = try_extract_docx_model(buf.getvalue(), title="t")
    assert model is not None
    assert (model.metadata or {}).get("style_map") == "mammoth-lite"
    kinds = [type(n).__name__ for n in model.nodes]
    assert "Heading" in kinds
    assert any(isinstance(n, (Paragraph, Blockquote, ListItem, Heading)) for n in model.nodes)
