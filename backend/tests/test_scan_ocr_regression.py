"""Real scan-like PDF regression (image-only fixtures).

Requires OCR extras. Skip when engines missing:

  pytest -q -m integration tests/test_scan_ocr_regression.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import settings
from app.importers import ocr_engine as oe

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "scan"


def _plain(model) -> str:
    parts: list[str] = []
    for n in model.nodes:
        if hasattr(n, "plain_text"):
            parts.append(n.plain_text())
        else:
            parts.append(str(getattr(n, "text", "") or ""))
    return "\n".join(parts)


def _expect_tokens(name: str) -> list[str]:
    path = FIXTURE_DIR / f"{name}.expect.txt"
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _assert_expect_hits(text: str, expect: list[str], *, label: str) -> None:
    """Require majority of expect tokens (at least ceil(n/2), min 1)."""
    assert expect, f"{label}: empty expect list"
    hits = [tok for tok in expect if tok in text]
    need = max(1, (len(expect) + 1) // 2)
    assert len(hits) >= need, (
        f"{label}: hit {len(hits)}/{len(expect)} (need ≥{need}); "
        f"hits={hits!r} expect={expect!r} got={text[:400]!r}"
    )


def _ocr_home(monkeypatch) -> None:
    home = Path(__file__).resolve().parents[2] / ".paddle_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "ocr_home", str(home))
    monkeypatch.setattr(settings, "ocr_min_chars", 1)
    monkeypatch.setattr(settings, "mineru_formula", False)


def _fixture_names() -> list[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.pdf"))


@pytest.mark.integration
@pytest.mark.parametrize("fixture", _fixture_names() or ["__missing__"])
def test_scan_fixture_mineru(monkeypatch, fixture: str):
    if fixture == "__missing__":
        pytest.skip("no scan fixtures under tests/fixtures/scan")
    if not oe.MinerUEngine().available():
        pytest.skip("mineru not installed")
    _ocr_home(monkeypatch)
    monkeypatch.setattr(settings, "ocr_timeout_seconds", 300)
    pdf = (FIXTURE_DIR / f"{fixture}.pdf").read_bytes()
    # Guard: fixture must be image-only (no useful text layer)
    try:
        from io import BytesIO

        from pypdf import PdfReader

        layer = "".join((p.extract_text() or "") for p in PdfReader(BytesIO(pdf)).pages)
        assert len(layer.strip()) < 8, "fixture unexpectedly has a text layer"
    except ImportError:
        pass

    model = oe.import_pdf_with_ocr(pdf, f"{fixture}.pdf", force_engine="mineru")
    text = _plain(model)
    _assert_expect_hits(text, _expect_tokens(fixture), label=f"MinerU:{fixture}")


@pytest.mark.integration
@pytest.mark.parametrize("fixture", _fixture_names() or ["__missing__"])
def test_scan_fixture_paddleocr_vl(monkeypatch, fixture: str):
    if fixture == "__missing__":
        pytest.skip("no scan fixtures under tests/fixtures/scan")
    monkeypatch.setattr(settings, "ocr_allow_paddle", "true")
    if not oe.PaddleOcrVlEngine().available():
        pytest.skip("paddleocr/paddlex not installed")
    _ocr_home(monkeypatch)
    monkeypatch.setattr(settings, "ocr_timeout_seconds", 420)
    pdf = (FIXTURE_DIR / f"{fixture}.pdf").read_bytes()
    model = oe.import_pdf_with_ocr(pdf, f"{fixture}.pdf", force_engine="paddleocr_vl")
    text = _plain(model)
    _assert_expect_hits(text, _expect_tokens(fixture), label=f"Paddle:{fixture}")


def test_scan_fixtures_present():
    pdfs = list(FIXTURE_DIR.glob("*.pdf"))
    assert len(pdfs) >= 2, "expected generated scan fixtures"
    for pdf in pdfs:
        assert (FIXTURE_DIR / f"{pdf.stem}.expect.txt").is_file()
        assert pdf.stat().st_size > 1000
