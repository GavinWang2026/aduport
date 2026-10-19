"""T40: HTTP-surface E2E — health → formats → authed convert → async job result."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.tenants import clear_tenant_cache
from main import app

_PACK = Path(__file__).resolve().parent / "fixtures" / "sample_pack"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "job_token_required", True)
    monkeypatch.setattr(settings, "convert_isolate", False)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "api_keys", "")
    clear_tenant_cache()
    return TestClient(app)


def test_e2e_health_and_formats(client):
    h = client.get("/api/health")
    assert h.status_code == 200
    assert h.json()["status"] == "ok"

    f = client.get("/api/formats")
    assert f.status_code == 200
    body = f.json()
    sources = body.get("source") or body.get("sources") or body.get("source_formats")
    targets = body.get("target") or body.get("targets") or body.get("target_formats")
    # tolerate nested shapes
    if sources is None and isinstance(body.get("formats"), dict):
        sources = body["formats"].get("source")
        targets = body["formats"].get("target")
    assert sources, f"formats missing sources: {body.keys()}"
    assert targets, f"formats missing targets: {body.keys()}"


def test_e2e_sync_convert_sample(client):
    raw = (_PACK / "cjk_report.md").read_bytes()
    r = client.post(
        "/api/convert",
        files={"file": ("cjk_report.md", raw, "text/markdown")},
        data={"source_format": "md", "target_format": "html"},
    )
    assert r.status_code == 200, r.text
    html = r.content.decode("utf-8", errors="ignore")
    assert "季度业务报告" in html or "营收增长" in html


def test_e2e_authed_convert_and_job(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", "e2e-secret")
    monkeypatch.setattr(settings, "api_keys", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "job_token_required", True)
    monkeypatch.setattr(settings, "convert_isolate", False)
    clear_tenant_cache()
    c = TestClient(app)
    headers = {"X-API-Key": "e2e-secret"}
    raw = (_PACK / "office_table.md").read_bytes()

    denied = c.post(
        "/api/convert",
        files={"file": ("office_table.md", raw, "text/markdown")},
        data={"source_format": "md", "target_format": "csv"},
    )
    assert denied.status_code == 401

    ok = c.post(
        "/api/convert",
        files={"file": ("office_table.md", raw, "text/markdown")},
        data={"source_format": "md", "target_format": "csv"},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    csv_text = ok.content.decode("utf-8", errors="ignore")
    assert "SKU" in csv_text and "Widget-A" in csv_text

    job = c.post(
        "/api/jobs",
        files={"file": ("office_table.md", raw, "text/markdown")},
        data={"source_format": "md", "target_format": "txt"},
        headers=headers,
    )
    assert job.status_code == 202, job.text
    body = job.json()
    jid = body["id"]
    token = body["access_token"]
    assert token

    status = "queued"
    for _ in range(100):
        st = c.get(f"/api/jobs/{jid}", headers={**headers, "X-Job-Token": token})
        assert st.status_code == 200, st.text
        status = st.json().get("status")
        if status in ("done", "error"):
            break
        time.sleep(0.05)
    assert status == "done", f"job ended as {status}"

    res = c.get(f"/api/jobs/{jid}/result", headers={**headers, "X-Job-Token": token})
    assert res.status_code == 200, res.text
    text = res.content.decode("utf-8", errors="ignore")
    assert "Widget-A" in text or "SKU" in text

    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "api_key", "")
    clear_tenant_cache()
