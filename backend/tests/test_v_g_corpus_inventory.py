"""V-G: audit-day 20-docx corpus inventory + sample_pack proxy gate linkage."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_ANYDOC_FIX = _REPO / "native" / "anydoc" / "tests" / "fixtures"
_PACK = Path(__file__).resolve().parent / "fixtures" / "sample_pack"


def test_v_g_anydoc_docx_corpus_has_20_fixtures():
    """Report V-G: 20 docx under anydoc fixtures (incl. malformed/abuse)."""
    if not _ANYDOC_FIX.is_dir():
        pytest.skip("anydoc fixtures not vendored in this checkout")
    docx = sorted(_ANYDOC_FIX.rglob("*.docx"))
    assert len(docx) == 20, f"expected 20 docx fixtures, got {len(docx)}: {[p.name for p in docx]}"
    names = {p.name for p in docx}
    for must in (
        "empty--errors.docx",
        "truncated--errors.docx",
        "deepxml--errors.docx",
        "mismatched--recovers.docx",
        "unclosed--recovers.docx",
    ):
        assert must in names, must


def test_v_g_sample_pack_manifest_proxy_rate():
    """Engineering proxy for customer抽检 (≥90%) used by CI / private volume."""
    import json

    man = json.loads((_PACK / "manifest.json").read_text(encoding="utf-8"))
    assert float(man.get("audit_proxy_min_pass_rate") or 0) >= 0.9
    assert len(man.get("samples") or []) >= 4
    for s in man["samples"]:
        assert (_PACK / s["file"]).is_file(), s["file"]
        assert s.get("targets"), s["id"]
