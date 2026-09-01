"""P0-1 / P0-2 / P0-3 regression tests from commercialization report."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from app.core.converter import convert
from app.core.exceptions import ExportError, MdBridgeError
from app.core.tenants import clear_tenant_cache, load_tenant_keys
from app.exporters.pdf_simple import export_pdf_simple
from app.core.document_model import DocumentModel, Heading, Paragraph
from app.importers.docling_ready import clear_docling_ready_cache, docling_models_ready
from app.importers.pdf_importer import import_pdf


def test_p0_1_cjk_pdf_embeds_identity_h():
    """P0-1 方案 A: Chinese PDF must embed Identity-H subset, not Helvetica ???."""
    md = "# 中文标题测试\n\n这是中文段落内容。\n"
    out = convert(md.encode("utf-8"), "md", "pdf", filename="d.md")
    assert len(out) > 2000
    assert b"Identity-H" in out or b"/ToUnicode" in out
    assert b"Helvetica" not in out or b"MDBridgeCJK" in out
    assert out.count(b"?????") == 0

    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip("pypdf not installed")
    text = (PdfReader(io.BytesIO(out)).pages[0].extract_text() or "")
    assert "中文" in text or "标题" in text


def test_p0_1_raises_when_forced_ascii_fallback(monkeypatch):
    from app.config import settings
    from app.exporters import pdf_simple as ps

    monkeypatch.setattr(settings, "pdf_cjk_fallback", "error")
    monkeypatch.setattr(ps, "_find_cjk_font", lambda: None)
    model = DocumentModel(title="t", nodes=[Heading(level=1, text="中文"), Paragraph(text="测试")])
    with pytest.raises((ExportError, MdBridgeError)):
        export_pdf_simple(model)


def test_p0_2_missing_models_fast_fail(monkeypatch, tmp_path):
    """Without local models, import must not hang on Hub — text path or quick error."""
    from app.config import settings
    import app.importers.docling_ready as dr

    monkeypatch.setattr(settings, "pdf_engine", "auto")
    monkeypatch.setattr(settings, "docling_offline", True)
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(dr, "_candidate_model_dirs", lambda: [tmp_path / "none"])
    clear_docling_ready_cache()
    assert docling_models_ready() is False

    # Minimal digital PDF with extractable text (no reportlab dependency)
    body = b"BT /F1 12 Tf 72 720 Td (Hello PDF text layer sample content xx) Tj ET"
    pdf = (
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

    import time

    t0 = time.perf_counter()
    model = import_pdf(pdf, "fast.pdf")
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, f"P0-2 still slow: {elapsed:.2f}s"
    assert model.nodes
    clear_docling_ready_cache()


def test_p0_3_multi_tenant_keys(monkeypatch, tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps(
            [
                {"tenant_id": "acme", "api_key": "sk-acme-1", "rate_limit": 10},
                {"tenant_id": "beta", "api_key": "sk-beta-2"},
            ]
        ),
        encoding="utf-8",
    )
    from app.config import settings

    monkeypatch.setattr(settings, "api_keys", str(keys_file))
    monkeypatch.setattr(settings, "api_key", "")
    clear_tenant_cache()
    loaded = load_tenant_keys()
    assert len(loaded) == 2
    assert {k.tenant_id for k in loaded} == {"acme", "beta"}
    clear_tenant_cache()
