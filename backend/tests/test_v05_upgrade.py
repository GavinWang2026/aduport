"""v0.5: MOBI, virus scan hook, OCR metadata."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.registry import ensure_builtin_plugins, registered_import_formats
from app.core.virus_scan import VirusDetectedError, scan_bytes
from main import app


@pytest.fixture()
def client(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "virus_scan", False)
    return TestClient(app)


def test_mobi_registered(client):
    ensure_builtin_plugins()
    assert "mobi" in registered_import_formats()
    assert "mobi" in client.get("/api/formats").json()["source"]


def test_mobi_invalid(client):
    r = client.post(
        "/api/convert",
        files={"file": ("x.mobi", b"not-a-mobi-file", "application/octet-stream")},
        data={"source_format": "mobi", "target_format": "md"},
    )
    assert r.status_code == 422


def test_virus_scan_disabled_noop(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "virus_scan", False)
    scan_bytes(b"hello", "a.txt")  # should not raise


def test_virus_scan_infected(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "virus_scan", True)
    monkeypatch.setattr(settings, "virus_scan_fail_closed", True)

    class FakeProc:
        returncode = 1
        stdout = "upload.bin: Eicar-Test-Signature FOUND"
        stderr = ""

    with patch("app.core.virus_scan.shutil.which", return_value="/usr/bin/clamscan"), patch(
        "app.core.virus_scan.subprocess.run", return_value=FakeProc()
    ):
        with pytest.raises(VirusDetectedError):
            scan_bytes(b"eicar", "upload.bin")


def test_virus_scan_missing_binary_open(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "virus_scan", True)
    monkeypatch.setattr(settings, "virus_scan_fail_closed", False)
    with patch("app.core.virus_scan.shutil.which", return_value=None):
        scan_bytes(b"x", "a.bin")  # warn only


def test_health_ocr_and_version(client):
    h = client.get("/api/health/detail").json()
    assert h["version"] == "0.16.0"
    assert h["ocr"]["feature"] == "scanned_pdf_to_structured_markdown"
    assert "virus_scan" in h
