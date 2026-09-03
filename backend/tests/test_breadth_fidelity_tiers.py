"""Breadth honesty: /api/formats fidelity_tiers + text-level labels."""

from __future__ import annotations

from fastapi.testclient import TestClient

from main import app


def test_formats_exposes_fidelity_tiers():
    with TestClient(app) as tc:
        r = tc.get("/api/formats")
        assert r.status_code == 200
        body = r.json()
        tiers = body.get("fidelity_tiers") or {}
        assert "doc" in tiers.get("text_level", [])
        assert "xlsb" in tiers.get("text_level", [])
        assert "azw3" in tiers.get("text_level", [])
        assert "docx" in tiers.get("full", [])
        assert "ods" in tiers.get("structured_partial", [])
        assert "odp" in tiers.get("structured_partial", [])
        assert "ods" not in tiers.get("full", [])
        assert "odp" not in tiers.get("full", [])
        assert "ods" not in tiers.get("export_shallow", [])
        assert "odp" not in tiers.get("export_shallow", [])
        notes = body.get("notes") or {}
        assert "ole_legacy" in notes
        assert "pdf" in notes
        assert "ocr_required" in notes.get("pdf", "") or "ocr_required" in notes.get("ocr", "")
        assert body.get("builtin_coverage", {}).get("xlsb")
        assert len(body.get("source") or []) == 29
        assert len(body.get("target") or []) == 18
