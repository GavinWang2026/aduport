"""T21: per-job access token for status/result endpoints."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.jobs import Job
from main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_create_dict_includes_token_once():
    job = Job(
        id="abc",
        filename="a.md",
        source_format="md",
        target_format="txt",
        access_token="secret-token-value",
    )
    public = job.to_dict()
    assert "access_token" not in public
    once = job.to_dict(include_token=True)
    assert once["access_token"] == "secret-token-value"


def test_token_ok_constant_time():
    job = Job(
        id="x",
        filename="a.md",
        source_format="md",
        target_format="txt",
        access_token="tok-aaa",
    )
    assert job.token_ok("tok-aaa")
    assert not job.token_ok("tok-bbb")
    assert not job.token_ok("")
    assert not job.token_ok(None)


def test_job_endpoints_require_token(monkeypatch, client):
    monkeypatch.setattr(settings, "job_token_required", True)
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "convert_isolate", False)

    files = {"file": ("t.md", b"# Hi\n", "text/markdown")}
    data = {"target_format": "txt"}
    r = client.post("/api/jobs", files=files, data=data)
    assert r.status_code == 202, r.text
    body = r.json()
    jid = body["id"]
    token = body["access_token"]
    assert token

    listed = client.get("/api/jobs").json()["jobs"]
    hit = next(j for j in listed if j["id"] == jid)
    assert "access_token" not in hit

    denied = client.get(f"/api/jobs/{jid}")
    assert denied.status_code == 403

    ok = client.get(f"/api/jobs/{jid}", headers={"X-Job-Token": token})
    assert ok.status_code == 200
    assert ok.json()["id"] == jid
    assert "access_token" not in ok.json()

    for _ in range(80):
        st = client.get(f"/api/jobs/{jid}", params={"token": token})
        if st.json().get("status") in ("done", "error"):
            break
        time.sleep(0.05)
    res = client.get(f"/api/jobs/{jid}/result", headers={"X-Job-Token": token})
    assert res.status_code in (200, 422)


def test_health_ready_native_gate(monkeypatch, client):
    monkeypatch.setattr(settings, "native_required", True)
    import app.importers.anydoc_bridge as ab

    monkeypatch.setattr(ab, "anydoc_available", lambda: False)
    r = client.get("/api/health/ready")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"

    monkeypatch.setattr(ab, "anydoc_available", lambda: True)
    monkeypatch.setattr(ab, "anydoc_version", lambda: "test")
    r2 = client.get("/api/health/ready")
    assert r2.status_code == 200
    assert r2.json()["anydoc_available"] is True


def test_security_headers_present(client):
    r = client.get("/api/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
