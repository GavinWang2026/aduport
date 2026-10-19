"""Baseline docs still present after delivery-report cleanup."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_enterprise_capability_docs_present():
    risk = (_ROOT / "enterprise" / "ANYDOC_RISK.md").read_text(encoding="utf-8")
    assert "xlsb" in risk.lower()
    assert "MIT" in risk
    assert (_ROOT / "enterprise" / "OCR.md").is_file()
    assert (_ROOT / "enterprise" / "PDF_TRISTATE.md").is_file()
    assert (_ROOT / "ENTERPRISE.md").is_file()
