"""T18–T24: async executor, memory charge, PDF single-open, health/license, zip/xml safety."""

from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core import xml_safe
from app.core.exceptions import FileTooLargeError, MemoryBudgetError
from app.core.jobs import job_store
from app.core.license import LicenseInfo
from app.core.safe_zip import ZipInflateBudget, read_zip_member
from main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "job_token_required", False)
    monkeypatch.setattr(settings, "convert_isolate", False)
    return TestClient(app)


def test_t23_health_liveness_status_and_detail(client):
    live = client.get("/api/health").json()
    assert live["status"] == "ok"
    assert "metrics" not in live
    assert "jobs" not in live

    status = client.get("/api/health/status").json()
    detail = client.get("/api/health/detail").json()
    assert "metrics" in status
    assert "jobs" in detail
    assert status.get("version") == detail.get("version")


def test_t23_license_public_dict_hides_machine_ids(client):
    body = client.get("/api/license").json()
    assert "machine_id" not in body
    lic = body.get("license")
    if lic is not None:
        assert "machine_id" not in lic
        assert "host_machine_id" not in lic

    info = LicenseInfo(
        machine_id="abc123deadbeef00",
        expires="2099-01-01",
        seats=1,
        customer="t",
        product="md-bridge",
        valid=True,
    )
    pub = info.public_dict()
    assert "machine_id" not in pub
    assert "host_machine_id" not in pub
    full = info.to_dict()
    assert full["machine_id"] == "abc123deadbeef00"


def test_t18_create_job_and_convert_still_work(client):
    r = client.post(
        "/api/convert",
        files={"file": ("t.md", b"# Hi\n\nbody\n", "text/markdown")},
        data={"source_format": "md", "target_format": "txt"},
    )
    assert r.status_code == 200, r.text
    assert b"Hi" in r.content or b"body" in r.content

    j = client.post(
        "/api/jobs",
        files={"file": ("t.md", b"# Job\n", "text/markdown")},
        data={"source_format": "md", "target_format": "txt"},
    )
    assert j.status_code == 202, j.text
    assert j.json().get("id")


def test_t19_reserve_bytes_and_queue_stats(monkeypatch):
    monkeypatch.setattr(settings, "memory_budget_mb", 1)
    with job_store._lock:
        prev = job_store._inflight_bytes
        job_store._inflight_bytes = 900_000
    try:
        with pytest.raises(MemoryBudgetError):
            job_store.reserve_bytes(200_000)
        token = job_store.reserve_bytes(50_000)
        assert token == 50_000
        job_store.release_bytes(token)
    finally:
        with job_store._lock:
            job_store._inflight_bytes = prev

    stats = job_store.stats()
    assert "queue_max" in stats
    assert stats["queue_max"] >= 1


def test_t20_pdf_auto_single_extract_sets_char_meta(monkeypatch):
    from app.importers import pdf_importer as pi

    calls = {"n": 0}

    class _Fake:
        metadata = {"pdf_text_chars": 5000}
        nodes = []

    def fake_extract(data, filename="x.pdf"):
        calls["n"] += 1
        return _Fake()

    monkeypatch.setattr(settings, "pdf_engine", "auto")
    monkeypatch.setattr(settings, "pdf_text_min_chars", 100)
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(pi, "extract_pdf_text_model", fake_extract)
    monkeypatch.setattr(pi, "docling_models_ready", lambda: False)

    out = pi.import_pdf(b"%PDF-1.4 fake", "t.pdf")
    assert out is _Fake() or getattr(out, "metadata", {}).get("pdf_text_chars") == 5000
    assert calls["n"] == 1


def test_t24_safe_zip_and_defusedxml():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.xml", b"<root><t>ok</t></root>")
        zf.writestr("big.txt", b"x" * 1000)
    data = buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        raw = read_zip_member(zf, "a.xml", max_bytes=100)
        assert b"<root>" in raw
        root = xml_safe.fromstring(raw)
        assert root.tag == "root"
        with pytest.raises(FileTooLargeError):
            read_zip_member(zf, "big.txt", max_bytes=100)

        budget = ZipInflateBudget(limit=500)
        budget.read(zf, "a.xml")
        with pytest.raises(FileTooLargeError):
            budget.read(zf, "big.txt")

    # Entity expansion path goes through defusedxml when installed
    try:
        import defusedxml  # noqa: F401
    except ImportError:
        pytest.skip("defusedxml not installed")
    with pytest.raises(Exception):
        xml_safe.fromstring(
            b"""<?xml version="1.0"?>
            <!DOCTYPE bomb [ <!ENTITY a "aaaaaaaaaaaaaaaa">
            <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;"> ]>
            <root>&b;</root>"""
        )
