"""API integration / security tests."""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from main import aduport


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ADUPORT_API_KEY", "")
    monkeypatch.setenv("ADUPORT_CORS_ORIGINS", "local")
    # Reload settings is hard; middleware reads settings at request time for API key
    from aduport.config import settings

    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "cors_origins", "local")
    monkeypatch.setattr(settings, "cors_allow_credentials", False)
    return TestClient(app)


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_convert_md_to_md(client):
    content = b"# Hi\n\n**bold**\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
    r = client.post(
        "/api/convert",
        files={"file": ("t.md", content, "text/markdown")},
        data={"source_format": "md", "target_format": "md"},
    )
    assert r.status_code == 200
    body = r.content.decode()
    assert "bold" in body
    assert "| a" in body or "| a " in body


def test_batch_zip_slip_cleaned(client):
    files = [
        ("files", ("../../evil.md", b"# x\n", "text/markdown")),
        ("files", ("ok.md", b"# y\n", "text/markdown")),
    ]
    r = client.post(
        "/api/convert/batch",
        files=files,
        data={"source_format": "md", "target_format": "md"},
    )
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert all(".." not in n and "/" not in n.replace("errors.json", "") or n == "errors.json" for n in names)
    assert not any(n.startswith("..") or "\\" in n for n in names)


def test_cors_whitelist(client):
    r = client.options(
        "/api/convert",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette may return 400 or omit ACAO for disallowed origin
    acao = r.headers.get("access-control-allow-origin")
    assert acao != "https://evil.example.com"


def test_api_key_required(monkeypatch):
    from aduport.config import settings
    from aduport.core.tenants import clear_tenant_cache

    monkeypatch.setattr(settings, "api_key", "secret-key")
    monkeypatch.setattr(settings, "api_keys", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    clear_tenant_cache()
    c = TestClient(app)
    r = c.post(
        "/api/convert",
        files={"file": ("t.md", b"# a\n", "text/markdown")},
        data={"source_format": "md", "target_format": "md"},
    )
    assert r.status_code == 401
    r2 = c.post(
        "/api/convert",
        files={"file": ("t.md", b"# a\n", "text/markdown")},
        data={"source_format": "md", "target_format": "md"},
        headers={"X-API-Key": "secret-key"},
    )
    assert r2.status_code == 200
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "api_keys", "")
    clear_tenant_cache()
