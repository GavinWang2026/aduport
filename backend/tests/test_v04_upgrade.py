"""v0.4.0: async jobs, basic auth, pptx/epub registration, memory budget."""

from __future__ import annotations

import base64
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from aduport.core.registry import ensure_builtin_plugins, registered_import_formats
from main import aduport


@pytest.fixture()
def client(monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "cache_enabled", True)
    monkeypatch.setattr(settings, "memory_budget_mb", 512)
    return TestClient(app)


def test_async_job_md_roundtrip(client):
    r = client.post(
        "/api/jobs",
        files={"file": ("t.md", b"# Hello\n\nworld\n", "text/markdown")},
        data={"source_format": "md", "target_format": "md"},
    )
    assert r.status_code == 202
    job_id = r.json()["id"]

    deadline = time.perf_counter() + 15
    body: dict = {}
    while time.perf_counter() < deadline:
        st = client.get(f"/api/jobs/{job_id}")
        assert st.status_code == 200
        body = st.json()
        if body["status"] in ("done", "error"):
            break
        time.sleep(0.05)
    assert body.get("status") == "done", body

    out = client.get(f"/api/jobs/{job_id}/result")
    assert out.status_code == 200
    assert b"Hello" in out.content


def test_job_not_found(client):
    r = client.get("/api/jobs/does-not-exist")
    assert r.status_code == 404


def test_pptx_epub_registered():
    ensure_builtin_plugins()
    assert "pptx" in registered_import_formats()
    assert "epub" in registered_import_formats()


def test_formats_include_pptx_epub(client):
    data = client.get("/api/formats").json()
    assert "pptx" in data["source"]
    assert "epub" in data["source"]


def test_pptx_invalid_zip(client):
    r = client.post(
        "/api/convert",
        files={"file": ("x.pptx", b"not-a-zip", "application/octet-stream")},
        data={"source_format": "pptx", "target_format": "md"},
    )
    # magic bytes fail first (not PK)
    assert r.status_code in (400, 422)


def test_epub_magic_ok_but_structure_bad(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no epub")
    data = buf.getvalue()
    r = client.post(
        "/api/convert",
        files={"file": ("x.epub", data, "application/epub+zip")},
        data={"source_format": "epub", "target_format": "md"},
    )
    assert r.status_code == 422


def test_basic_auth(monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "admin:secret")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    c = TestClient(app)
    r = c.post(
        "/api/convert",
        files={"file": ("t.md", b"# a\n", "text/markdown")},
        data={"source_format": "md", "target_format": "md"},
    )
    assert r.status_code == 401
    token = base64.b64encode(b"admin:secret").decode()
    r2 = c.post(
        "/api/convert",
        files={"file": ("t.md", b"# a\n", "text/markdown")},
        data={"source_format": "md", "target_format": "md"},
        headers={"Authorization": f"Basic {token}"},
    )
    assert r2.status_code == 200
    monkeypatch.setattr(settings, "basic_auth", "")


def test_memory_budget(monkeypatch):
    from aduport.config import settings
    from aduport.core.jobs import job_store

    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "memory_budget_mb", 0)  # 0 = unlimited in our design
    # With 0 unlimited — set tiny budget instead
    monkeypatch.setattr(settings, "memory_budget_mb", 1)  # 1 MB
    # Force inflight high
    with job_store._lock:
        job_store._inflight_bytes = 2 * 1024 * 1024
    c = TestClient(app)
    try:
        r = c.post(
            "/api/jobs",
            files={"file": ("t.md", b"# a\n" * 100, "text/markdown")},
            data={"source_format": "md", "target_format": "md"},
        )
        assert r.status_code == 429
        assert r.json()["code"] == "memory_budget_exceeded"
    finally:
        with job_store._lock:
            job_store._inflight_bytes = 0


def test_health_v04(client):
    h = client.get("/api/health/detail").json()
    assert h["version"] == "0.16.0"
    assert "jobs" in h
    assert "memory_budget_mb" in h
