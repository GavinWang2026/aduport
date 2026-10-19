"""Closure checks for assessment report v10 (P1–P3)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.document_model import DocumentModel
from aduport.core.exceptions import InvalidDocumentError
from aduport.core.license import _signing_secret
from aduport.core.metrics import metrics
from aduport.core.safe_fetch import fetch_bytes
from aduport.core.stream_convert import _html_head
from main import aduport


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "api_key", "")
    return TestClient(app)


def test_usage_does_not_leak_other_tenants(client):
    body = client.get("/api/usage").json()
    assert "metrics" not in body
    assert "conversions_by_tenant" not in body
    assert body["tenant_id"] == "default"


def test_prometheus_omits_tenant_series(client):
    metrics.record_success("md", "txt", 0.01, tenant_id="other-tenant")
    text = client.get("/api/metrics").text
    assert "aduport_conversions_total" in text
    assert "tenant_id=" not in text


def test_remote_fetch_rejects_nonstandard_port():
    with pytest.raises(InvalidDocumentError, match="端口"):
        fetch_bytes("http://example.com:8080/a.png")


def test_vectors_search_rejects_bad_top_k(client):
    r = client.post("/api/vectors/search", json={"query": "hello", "top_k": "nope"})
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_document"


def test_doctor_cli_runs(monkeypatch):
    from typer.testing import CliRunner

    from aduport.cli import aduport

    monkeypatch.setattr("aduport.config.settings.ocr_enabled", False)
    monkeypatch.setattr("aduport.config.settings.license_required", False)
    monkeypatch.setattr("aduport.config.settings.redis_url", "")
    runner = CliRunner()
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code in (0, 1)
    assert "weasyprint" in result.stdout
    assert "native_anydoc" in result.stdout


def test_install_native_wheel_script_help():
    import subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "install_native_wheel.sh"
    assert script.is_file()
    r = subprocess.run(
        ["bash", str(script), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "prebuilt" in r.stdout.lower() or "install_native_wheel" in r.stdout
    # P2-1: version grep must use POSIX [[:space:]] (not GNU \s)
    body = script.read_text(encoding="utf-8")
    assert r"[[:space:]]" in body
    assert r"^version\s*=" not in body


def test_session_ttl_expires(monkeypatch):
    import time

    from aduport.api import auth

    monkeypatch.setattr(auth, "_SESSION_TTL_SECONDS", 0.05)
    with auth._session_lock:
        auth._sessions.clear()
        auth._revoked_jti.clear()
    # Opaque path (no tenant registry): in-memory + TTL
    sid = auth.issue_session("k-test")
    assert not sid.startswith("s1.")
    assert auth.resolve_session(sid) == "k-test"
    time.sleep(0.08)
    assert auth.resolve_session(sid) == ""


def test_signed_session_survives_memory_clear(monkeypatch):
    from aduport.api import auth
    from aduport.core.tenants import TenantKey, clear_tenant_cache

    keys = (TenantKey(tenant_id="acme", api_key="secret-key-acme"),)
    monkeypatch.setenv("ADUPORT_SESSION_SECRET", "v13-test-session-secret-32b!!")
    monkeypatch.setattr(auth, "_SESSION_TTL_SECONDS", 3600)
    monkeypatch.setattr(auth, "load_tenant_keys", lambda: keys)
    monkeypatch.setattr("aduport.core.tenants.load_tenant_keys", lambda: keys)
    clear_tenant_cache()
    with auth._session_lock:
        auth._sessions.clear()
        auth._revoked_jti.clear()

    tok = auth.issue_session("secret-key-acme")
    assert tok.startswith("s1.")
    # Simulate another worker: empty in-memory maps, same HMAC secret.
    with auth._session_lock:
        auth._sessions.clear()
    assert auth.resolve_session(tok) == "secret-key-acme"

    auth.revoke_session(tok)
    assert auth.resolve_session(tok) == ""


def test_session_hmac_no_hardcoded_fallback(monkeypatch):
    """v13 P1-1: without strong secret, never sign with aduport-dev-session."""
    from aduport.api import auth
    from aduport.core.tenants import TenantKey, clear_tenant_cache

    monkeypatch.delenv("ADUPORT_SESSION_SECRET", raising=False)
    monkeypatch.setattr(settings, "session_secret", "")
    monkeypatch.setattr(settings, "license_secret", "")
    keys = (TenantKey(tenant_id="default", api_key="k-default"),)
    monkeypatch.setattr(auth, "load_tenant_keys", lambda: keys)
    monkeypatch.setattr("aduport.core.tenants.load_tenant_keys", lambda: keys)
    clear_tenant_cache()
    assert auth._session_hmac_secret() is None
    assert not auth.session_hmac_configured()
    tok = auth.issue_session("k-default")
    assert not tok.startswith("s1.")
    # Forged signed cookie with known historical fallback must not verify.
    assert auth._verify_signed("s1.forged.payload") is None


def test_stream_html_title_is_escaped():
    model = DocumentModel(title="</title><script>alert(1)</script>", nodes=[])
    head = _html_head(model)
    assert "</title><script>" not in head
    assert "&lt;/title&gt;" in head


def test_license_fallback_is_not_the_public_placeholder():
    secret = _signing_secret()
    assert b"AduPort-dev-license-secret-change-me" not in secret
    assert len(secret) >= 16
    from aduport.core.license import _secret_is_weak

    assert _secret_is_weak("AduPort-dev-license-secret-change-me")
    assert _secret_is_weak("change-me")
    assert not _secret_is_weak("a-real-customer-secret-value")


def test_mock_ocr_home_is_not_a_directory(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from aduport.importers.ocr_engine import configured_ocr_home

    monkeypatch.setattr(settings, "ocr_home", MagicMock())
    assert configured_ocr_home() == ""
    assert not (tmp_path / "MagicMock").exists()


def test_health_lists_optional_capabilities(client):
    body = client.get("/api/health/detail").json()
    caps = body["capabilities"]
    assert "ocr" in caps and "install_hint" in caps["ocr"]
    assert "virus_scan" in caps
    assert "native_office" in caps
    assert "预编译" in caps["native_office"]["install_hint"]
    assert "install_native_wheel" in caps["native_office"]["install_hint"]
    assert "weasyprint_available" in body["pdf_export"]
    assert "install_hint" in body["pdf_export"] or body["pdf_export"].get("weasyprint_available")
    assert body["rate_limit"]["backend"] in ("memory", "redis")
    sess = body["session"]
    assert "cookie_secure" in sess and "hmac_session" in sess
    assert sess["revoke_backend"] in ("memory", "redis")
    assert "multi_worker_ready" in sess


def test_cookie_secure_setting_respected(client, monkeypatch):
    from aduport.config import settings as st
    from aduport.core.tenants import clear_tenant_cache

    monkeypatch.setattr(st, "cookie_secure", False)
    monkeypatch.setattr(st, "require_auth", True)
    monkeypatch.setattr(st, "api_key", "test-key-cookie-secure")
    monkeypatch.setattr(st, "api_keys", "")
    monkeypatch.setattr(st, "basic_auth", "")
    clear_tenant_cache()
    r = client.post("/api/auth/session", headers={"X-API-Key": "test-key-cookie-secure"})
    assert r.status_code == 200, r.text
    set_cookie = r.headers.get("set-cookie") or ""
    assert "aduport_api_session" in set_cookie.lower()
    parts = [p.strip().lower() for p in set_cookie.split(";")]
    assert "secure" not in parts
    monkeypatch.setattr(st, "api_key", "")
    clear_tenant_cache()


def test_form_rejects_unknown_pdf_page_size(client):
    r = client.post(
        "/api/convert",
        files={"file": ("t.md", b"# a\n", "text/markdown")},
        data={"source_format": "md", "target_format": "md", "pdf_page_size": "banana"},
    )
    assert r.status_code == 422
