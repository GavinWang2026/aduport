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
        # v6 claim honesty: csv/txt are markers, not full layout.
        assert "csv" not in tiers.get("full", [])
        assert "txt" not in tiers.get("full", [])
        assert "csv" in tiers.get("text_markers", [])
        assert "txt" in tiers.get("text_markers", [])
        notes = body.get("notes") or {}
        assert "ole_legacy" in notes
        assert "pdf" in notes
        assert "ocr_required" in notes.get("pdf", "") or "ocr_required" in notes.get("ocr", "")
        assert "只承诺文字与结构标记" in notes.get("csv", "")
        assert "只承诺文字与结构标记" in notes.get("txt", "")
        assert "不计入内容级成功率" in notes.get("png_jpg", "")
        claim = body.get("claim_policy") or {}
        assert set(claim.get("text_markers_only_targets") or []) == {"csv", "txt"}
        assert set(claim.get("content_success_excludes_sources") or []) == {"jpg", "png"}
        assert set(claim.get("image_carry_only_sources") or []) == {"jpg", "png"}
        assert len(claim.get("layout_targets") or []) == 16
        assert "csv" not in (claim.get("layout_targets") or [])
        assert "txt" not in (claim.get("layout_targets") or [])
        assert body.get("builtin_coverage", {}).get("xlsb")
        assert body.get("builtin_coverage", {}).get("msg")
        assert "msg" in tiers.get("text_level", [])
        assert "ipynb" in tiers.get("full", [])
        assert "rss" in tiers.get("full", [])
        assert "atom" in tiers.get("full", [])
        assert "zip" in tiers.get("structured_partial", [])
        assert "pages" not in tiers.get("text_level", [])
        assert "numbers" not in tiers.get("text_level", [])
        assert "keynote" not in tiers.get("text_level", [])
        assert "pages" not in (body.get("source") or [])
        assert body.get("export_modes") == ["rag"]
        assert "rag" not in (body.get("target") or [])
        assert "iwork" in (body.get("notes") or {})
        assert len(body.get("source") or []) == 36
        assert len(body.get("target") or []) == 18
        limits = body.get("structure_limits") or {}
        assert limits == {}
        assert "structure_limits" in notes
        assert "ADUPORT-IMG" in notes.get("pdf", "")
        assert "_CONTENT" in notes.get("csv", "")
        page_grade = body.get("page_grade") or {}
        assert page_grade.get("scale") == "page_grade"
        assert page_grade.get("corpus_read") is False
        assert page_grade.get("not_a_success_rate") is True
        assert set(page_grade.get("grades") or {}) == {"A", "B", "C", "D"}
        assert "success_rate" not in page_grade
    from aduport import TARGET_STRUCTURE_LIMITS
    from aduport.cli import app as cli_app
    from typer.testing import CliRunner

    assert TARGET_STRUCTURE_LIMITS == {}
    listed = CliRunner().invoke(cli_app, ["formats"])
    assert listed.exit_code == 0
    assert "structure_limits: none" in listed.stdout
