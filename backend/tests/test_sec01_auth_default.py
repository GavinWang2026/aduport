"""SEC-01: require_auth fail-closed + OPTIONS/CORS ordering."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aduport.config import settings
from main import app


def test_require_auth_blocks_anonymous(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "api_keys", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.delenv("ADUPORT_DEV_OK", raising=False)
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200
    r = c.post(
        "/api/convert",
        files={"file": ("a.md", b"# hi\n", "text/markdown")},
        data={"target_format": "txt"},
    )
    assert r.status_code == 401
    assert r.json()["code"] == "unauthorized"


def test_options_preflight_not_blocked(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "api_keys", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    c = TestClient(app)
    r = c.options(
        "/api/convert",
        headers={
            "Origin": "http://localhost:7173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert r.status_code in (200, 204)
    assert "access-control-allow-origin" in {k.lower() for k in r.headers.keys()}


def test_dev_ok_allows_anonymous(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "api_keys", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setenv("ADUPORT_DEV_OK", "1")
    c = TestClient(app)
    r = c.post(
        "/api/convert",
        files={"file": ("a.md", b"# hi\n", "text/markdown")},
        data={"target_format": "txt"},
    )
    assert r.status_code == 200


def test_dev_ok_ignored_without_debug(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", True)
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "api_keys", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setenv("ADUPORT_DEV_OK", "1")
    c = TestClient(app)
    r = c.post(
        "/api/convert",
        files={"file": ("a.md", b"# hi\n", "text/markdown")},
        data={"target_format": "txt"},
    )
    assert r.status_code == 401
