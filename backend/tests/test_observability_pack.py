"""Enterprise observability stack: compose profile + Prometheus rules wiring."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_enterprise_observability_compose_profile():
    compose = yaml.safe_load((REPO_ROOT / "enterprise" / "docker-compose.yml").read_text())
    services = compose.get("services") or {}
    prom = services.get("prometheus") or {}
    am = services.get("alertmanager") or {}
    assert "observability" in (prom.get("profiles") or [])
    assert "observability" in (am.get("profiles") or [])
    assert (REPO_ROOT / "enterprise" / "prometheus.yml").is_file()
    assert (REPO_ROOT / "enterprise" / "alertmanager.yml").is_file()
    assert (REPO_ROOT / "docs" / "ops" / "prometheus" / "alert_rules.yml").is_file()


def test_prometheus_scrape_and_alertmanager_targets():
    prom = yaml.safe_load((REPO_ROOT / "enterprise" / "prometheus.yml").read_text())
    jobs = {j["job_name"] for j in prom.get("scrape_configs") or []}
    assert "mdbridge" in jobs
    am_targets = (
        (prom.get("alerting") or {})
        .get("alertmanagers", [{}])[0]
        .get("static_configs", [{}])[0]
        .get("targets", [])
    )
    assert "alertmanager:9093" in am_targets


def test_alert_rules_reference_mdbridge_metrics():
    rules_doc = yaml.safe_load(
        (REPO_ROOT / "docs" / "ops" / "prometheus" / "alert_rules.yml").read_text()
    )
    text = yaml.dump(rules_doc)
    assert "mdbridge_anydoc_channel_share" in text or "anydoc" in text
    assert "ocr" in text.lower()
