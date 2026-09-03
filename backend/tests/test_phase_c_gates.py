"""Phase C gate: sample pack v1.2 + Playwright deepen + cov floor."""

from __future__ import annotations

import json
from pathlib import Path


def test_sample_pack_v12_covers_ods_odp_txt():
    manifest = json.loads(
        (Path(__file__).resolve().parent / "fixtures/sample_pack/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert str(manifest.get("version", "")).startswith("1.2")
    assert float(manifest.get("audit_proxy_min_pass_rate") or 0) >= 0.9
    targets: set[str] = set()
    sources: set[str] = set()
    for s in manifest["samples"]:
        targets.update(s["targets"])
        sources.add(s.get("source") or "md")
    assert "ods" in targets and "odp" in targets and "odt" in targets
    assert "txt" in sources
    ids = {s["id"] for s in manifest["samples"]}
    assert "plain_ops" in ids


def test_playwright_e2e_scaffold_present():
    root = Path(__file__).resolve().parents[2] / "frontend"
    assert (root / "playwright.config.mjs").is_file()
    spec = (root / "e2e" / "auth-convert.spec.mjs").read_text(encoding="utf-8")
    assert "btn-convert" in spec
    assert "target-format" in spec
    assert "btn-download-job" in spec
    assert "mockJobConvertApi" in spec or "SENT_E2E_DOWNLOAD" in spec
    pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
    assert "test:e2e" in pkg["scripts"]
    assert "@playwright/test" in pkg.get("devDependencies", {})


def test_private_samples_hard_gate_documented():
    root = Path(__file__).resolve().parents[2]
    readme = (root / "enterprise" / "samples" / "README.md").read_text(encoding="utf-8")
    assert "MDBRIDGE_PRIVATE_SAMPLES_DIR" in readme
    assert "MDBRIDGE_PRIVATE_SAMPLES_REQUIRED" in readme
    assert (root / "backend" / "tests" / "test_private_samples_optional.py").is_file()
    src = (
        root / "backend" / "tests" / "test_private_samples_optional.py"
    ).read_text(encoding="utf-8")
    assert "PRIVATE_SAMPLES_REQUIRED" in src
    assert "pytest.fail" in src
