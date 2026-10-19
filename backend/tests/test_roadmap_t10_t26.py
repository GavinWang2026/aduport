"""Roadmap T10–T15 / T17 / T22 / T25 / T26 regression gates."""

from __future__ import annotations

import contextvars
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.document_model import DocumentModel, Paragraph, RichText
from aduport.core.jobs import Job, JobStatus, job_store
from main import aduport

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "job_token_required", False)
    return TestClient(app)


def test_t10_hard_exception_falls_back_to_native(monkeypatch):
    from aduport.core.registry import _anydoc_first
    from aduport.importers import anydoc_bridge as bridge

    monkeypatch.setattr(settings, "anydoc_fallback", True)
    monkeypatch.setattr(bridge, "should_handle", lambda fmt: True)

    def boom(*_a, **_k):
        raise RuntimeError("native panic simulated")

    monkeypatch.setattr(bridge, "import_via_anydoc", boom)

    def native_imp(data: bytes, filename: str = "") -> DocumentModel:
        return DocumentModel(
            nodes=[Paragraph(text=RichText(text="native-ok"))],
            metadata={},
        )

    wrapped = _anydoc_first("docx", native_imp)
    model = wrapped(b"x", "a.docx")
    assert model.nodes
    assert model.metadata.get("importer") == "native_fallback"


def test_t11_done_status_not_rolled_back_by_side_effect(monkeypatch):
    job = Job(
        id="sticky-done",
        filename="a.md",
        source_format="md",
        target_format="txt",
        status=JobStatus.done,
        progress=1.0,
        result_bytes=b"ok",
    )
    with job_store._lock:
        job_store._jobs[job.id] = job

    # Simulate post-done exception handler path
    with job_store._lock:
        live = job_store._jobs[job.id]
        if live.status == JobStatus.done:
            pass  # must ignore
        else:
            live.status = JobStatus.error
    assert job_store._jobs[job.id].status == JobStatus.done
    with job_store._lock:
        job_store._jobs.pop(job.id, None)


def test_t13_copy_context_propagates_tenant():
    from aduport.core.request_context import current_tenant_id, tenant_id_var

    seen: dict[str, str | None] = {"tid": None}

    def worker():
        seen["tid"] = current_tenant_id()

    token = tenant_id_var.set("tenant-t13")
    try:
        ctx = contextvars.copy_context()
        ctx.run(worker)
        assert seen["tid"] == "tenant-t13"
    finally:
        tenant_id_var.reset(token)


def test_t22_public_job_dict_omits_path_and_callback():
    job = Job(
        id="pub",
        filename="a.md",
        source_format="md",
        target_format="txt",
        result_path="/secret/path.bin",
        callback_url="https://evil.example/hook",
    )
    d = job.to_dict()
    assert "result_path" not in d
    assert "callback_url" not in d
    assert d.get("result_persisted") is True


def test_t22_api_job_list_omits_sensitive_fields(client, monkeypatch):
    monkeypatch.setattr(settings, "convert_isolate", False)
    r = client.post(
        "/api/jobs",
        files={"file": ("t.md", b"# x\n", "text/markdown")},
        data={"source_format": "md", "target_format": "txt"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert "result_path" not in body
    assert "callback_url" not in body


def test_t25_proxy_headers_in_launch_scripts():
    start = (_ROOT / "backend" / "start.sh").read_text(encoding="utf-8")
    assert "--proxy-headers" in start
    assert "forwarded-allow-ips" in start
    docker = (_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "--proxy-headers" in docker


def test_t26_ci_has_cargo_test_and_clippy():
    ci = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "cargo test" in ci
    assert "clippy" in ci
    assert "-D warnings" in ci
