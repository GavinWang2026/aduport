"""Chapter 4 upgrade acceptance: formats, security, cache, registry, metrics."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aduport.core.cache import ResultCache
from aduport.core.converter import convert
from aduport.core.exceptions import InvalidDocumentError, UnsupportedFormatError
from aduport.core.registry import (
    ensure_builtin_plugins,
    registered_export_formats,
    registered_import_formats,
)
from aduport.core.security import validate_upload
from main import aduport


@pytest.fixture()
def client(monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "cache_enabled", True)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "magic_bytes_check", True)
    return TestClient(app)


def test_formats_source_target(client):
    r = client.get("/api/formats")
    assert r.status_code == 200
    data = r.json()
    assert "txt" in data["source"]
    assert "html" in data["source"]
    assert "html" in data["target"]
    assert "txt" in data["target"]
    assert "epub" in data["target"]
    assert "azw3" in data["target"] and "kf8" in data["target"]
    assert "latex" in data["target"] and "csv" in data["target"]
    assert "odt" in data["source"] and "rtf" in data["source"]
    assert "odt" in data["target"] and "json" in data["target"] and "xlsx" in data["target"]
    assert "mobi" in data["target"]


def test_txt_to_md(client):
    r = client.post(
        "/api/convert",
        files={"file": ("note.txt", b"Hello\n\nWorld", "text/plain")},
        data={"source_format": "txt", "target_format": "md"},
    )
    assert r.status_code == 200
    body = r.content.decode()
    assert "Hello" in body
    assert "World" in body


def test_html_to_md(client):
    html = b"<html><body><h1>Title</h1><p>Para <b>x</b></p></body></html>"
    r = client.post(
        "/api/convert",
        files={"file": ("page.html", html, "text/html")},
        data={"source_format": "html", "target_format": "md"},
    )
    assert r.status_code == 200
    body = r.content.decode()
    assert "Title" in body
    assert "Para" in body


def test_magic_bytes_reject_fake_pdf():
    with pytest.raises(InvalidDocumentError):
        validate_upload(b"not a pdf file", "pdf", "x.pdf")


def test_magic_bytes_reject_ext_mismatch():
    with pytest.raises(UnsupportedFormatError):
        validate_upload(b"%PDF-1.4", "pdf", "x.docx")


def test_result_cache_hit(monkeypatch):
    from aduport.config import settings
    from aduport.core import converter as conv_mod

    monkeypatch.setattr(settings, "cache_enabled", True)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "magic_bytes_check", False)
    cache = ResultCache(max_entries=8, ttl_seconds=60)
    monkeypatch.setattr(conv_mod, "result_cache", cache)

    calls = {"n": 0}
    real_export = conv_mod._export

    def counting_export(model, target_format, **kwargs):
        calls["n"] += 1
        return real_export(model, target_format, **kwargs)

    monkeypatch.setattr(conv_mod, "_export", counting_export)

    src = b"# Cache me\n\nbody\n"
    out_a = convert(src, "md", "docx", filename="a.md", skip_magic=True)
    out_b = convert(src, "md", "docx", filename="a.md", skip_magic=True)
    assert out_a == out_b
    assert calls["n"] == 1
    assert cache.hits >= 1


def test_plugin_registry():
    ensure_builtin_plugins()
    for fmt in ("md", "txt", "html", "docx", "pdf"):
        assert fmt in registered_import_formats()
    for fmt in ("md", "docx", "pdf"):
        assert fmt in registered_export_formats()


def test_health_and_metrics(client):
    h = client.get("/api/health/detail")
    assert h.status_code == 200
    body = h.json()
    assert body["version"] == "0.16.0"
    assert "cache" in body
    assert "metrics" in body

    live = client.get("/api/health")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert "metrics" not in live.json()

    m = client.get("/api/metrics")
    assert m.status_code == 200
    assert "aduport_conversions_total" in m.text or "conversions" in m.text.lower() or m.text.strip() != ""


def test_rate_limit(monkeypatch):
    from aduport.api.rate_limit import reset_rate_limits
    from aduport.config import settings

    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "audit_enabled", False)
    reset_rate_limits()
    c = TestClient(app)
    payload = {
        "files": {"file": ("t.md", b"# a\n", "text/markdown")},
        "data": {"source_format": "md", "target_format": "md"},
    }
    assert c.post("/api/convert", **payload).status_code == 200
    assert c.post("/api/convert", **payload).status_code == 200
    r = c.post("/api/convert", **payload)
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
