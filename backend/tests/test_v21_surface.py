"""v21 surface gates: warning headers, isolate switch, engines, reproducibility."""

from __future__ import annotations

import hashlib
import io
import time
import zipfile

import pytest
from docx import Document
from fastapi.testclient import TestClient

from aduport.api.routes import _export_warning_headers
from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.document_model import DocumentModel, Paragraph
from aduport.core.exceptions import AduPortError
from aduport.core.jobs import Job, JobStatus, JobStore
from aduport.exporters.pdf_exporter import export_pdf
from aduport.importers.pdf_text_fallback import _join_wrapped_lines, _page_to_paragraphs
from main import app


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "require_auth", False)
    return TestClient(app)


def _header_docx() -> bytes:
    doc = Document()
    doc.add_paragraph("Body under a header")
    doc.sections[0].header.paragraphs[0].text = "页眉 → 中文"
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_warning_header_is_latin1_and_degraded_is_ascii():
    headers = _export_warning_headers(["页眉 → 中文告警"])
    for value in headers.values():
        value.encode("latin-1")
    assert "%" in headers["X-AduPort-Export-Warning-Sample"]
    degraded = _export_warning_headers(["PDF 已降级为 pdf_simple（请求指定 simple）"])
    assert degraded["X-AduPort-Export-Degraded"] == "true"
    assert '"degraded": true' in degraded["X-AduPort-Export-Warning-Json"]
    degraded["X-AduPort-Export-Warning-Json"].encode("latin-1")


def test_header_docx_http_pdf_and_html(client):
    blob = _header_docx()
    for target in ("pdf", "html"):
        response = client.post(
            "/api/convert",
            files={"file": ("header.docx", blob, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"source_format": "docx", "target_format": target},
        )
        assert response.status_code == 200, response.text[:300]
        for value in response.headers.values():
            value.encode("latin-1")
        if target == "pdf":
            assert response.content[:5] == b"%PDF-"


@pytest.mark.parametrize("isolate", [True, False])
def test_isolate_switch_keeps_three_endpoints(client, monkeypatch, isolate):
    monkeypatch.setattr(settings, "convert_isolate", isolate)
    payload = b"# Isolate\n\nHello endpoints.\n"
    converted = client.post(
        "/api/convert",
        files={"file": ("a.md", payload, "text/markdown")},
        data={"source_format": "md", "target_format": "html"},
    )
    assert converted.status_code == 200, converted.text[:300]

    job = client.post(
        "/api/jobs",
        files={"file": ("a.md", payload, "text/markdown")},
        data={"source_format": "md", "target_format": "html"},
    )
    assert job.status_code == 202, job.text[:300]
    assert job.json()["status"] in {"queued", "running", "done"}

    batch = client.post(
        "/api/convert/batch",
        files=[("files", ("a.md", payload, "text/markdown"))],
        data={"source_format": "md", "target_format": "html"},
    )
    assert batch.status_code == 200, batch.text[:300]
    assert batch.content[:2] == b"PK"


def test_pdf_engine_simple_differs_from_weasyprint():
    simple_model = DocumentModel(nodes=[Paragraph(text="Hello engine switch")])
    weasy_model = DocumentModel(nodes=[Paragraph(text="Hello engine switch")])
    simple = export_pdf(simple_model, engine="simple")
    weasy = export_pdf(weasy_model, engine="weasyprint")
    assert simple[:5] == b"%PDF-"
    assert weasy[:5] == b"%PDF-"
    assert hashlib.sha256(simple).digest() != hashlib.sha256(weasy).digest()
    assert simple_model.metadata["pdf_engine"] == "pdf_simple"
    assert weasy_model.metadata["pdf_engine"] == "weasyprint"


def test_bogus_pdf_engine_rejected(client):
    response = client.post(
        "/api/convert",
        files={"file": ("a.md", b"# A\n", "text/markdown")},
        data={"source_format": "md", "target_format": "pdf", "pdf_engine": "bogus"},
    )
    assert response.status_code == 422


def test_docx_and_epub_are_reproducible():
    source = b"# Stable\n\nSame bytes twice.\n"
    docx_a = convert(source, "md", "docx", filename="stable.md")
    docx_b = convert(source, "md", "docx", filename="stable.md")
    epub_a = convert(source, "md", "epub", filename="stable.md")
    epub_b = convert(source, "md", "epub", filename="stable.md")
    assert hashlib.sha256(docx_a).digest() == hashlib.sha256(docx_b).digest()
    assert hashlib.sha256(epub_a).digest() == hashlib.sha256(epub_b).digest()
    with zipfile.ZipFile(io.BytesIO(epub_a)) as zf:
        opf = zf.read("EPUB/content.opf")
    assert b"urn:uuid:" in opf


def test_pdf_list_paragraph_boundary_is_kept():
    joined = _join_wrapped_lines(["- bullet two", "Closing paragraph."])
    assert "\n" in joined
    assert "- bullet two Closing paragraph." not in joined
    paragraphs = _page_to_paragraphs("- bullet two\nClosing paragraph.")
    assert paragraphs == ["- bullet two", "Closing paragraph."]


def test_submit_after_shutdown_marks_job_error(monkeypatch):
    store = JobStore()

    def boom(*_args, **_kwargs):
        raise RuntimeError("cannot schedule new futures after shutdown")

    monkeypatch.setattr(store._executor, "submit", boom)
    with pytest.raises(AduPortError) as caught:
        store.submit(
            content=b"# a\n",
            filename="shutdown.md",
            source_format="md",
            target_format="md",
        )
    assert caught.value.code == "job_schedule_failed"
    failed = [job for job in store._jobs.values() if job.filename == "shutdown.md"]
    assert failed[-1].status == JobStatus.error
    assert failed[-1].code == "job_schedule_failed"
    assert failed[-1].budget_settled is True
    assert failed[-1].memory_charge == 0


def test_queued_job_past_deadline_becomes_job_timeout(monkeypatch):
    store = JobStore()
    monkeypatch.setattr(settings, "convert_timeout_seconds", 1)
    job = Job(
        id="deadline-queued",
        filename="late.md",
        source_format="md",
        target_format="md",
        status=JobStatus.queued,
        created_at=time.time() - 30,
        memory_charge=12,
    )
    with store._lock:
        store._jobs[job.id] = job
        store._inflight_bytes += 12
    got = store.get(job.id, enforce_tenant=False)
    assert got is not None
    assert got.code == "job_timeout"
    assert got.status == JobStatus.error
    assert got.memory_charge == 0
    assert got.budget_settled is True
    body = got.to_dict()
    assert body["degraded"] is False
    assert body["code"] == "job_timeout"


def test_code_block_plain_text_is_its_content():
    from aduport.core.document_model import CodeBlock

    block = CodeBlock(lang="python", content="print(1)\nx = 2")
    assert block.plain_text() == "print(1)\nx = 2"
    assert CodeBlock(lang="python", content="").plain_text() == ""


def test_pdf_engine_values_do_not_share_one_file():
    source = b"# Hello\n\nA paragraph for the engine switch.\n"
    weasy = convert(source, "md", "pdf", filename="eng.md", pdf_engine="weasyprint", cache_bypass=True)
    simple = convert(source, "md", "pdf", filename="eng.md", pdf_engine="simple", cache_bypass=True)
    auto = convert(source, "md", "pdf", filename="eng.md", pdf_engine="auto", cache_bypass=True)
    assert weasy[:5] == b"%PDF-" and simple[:5] == b"%PDF-" and auto[:5] == b"%PDF-"
    assert hashlib.sha256(weasy).digest() != hashlib.sha256(simple).digest()
    assert hashlib.sha256(auto).digest() == hashlib.sha256(weasy).digest()
    with pytest.raises(AduPortError) as caught:
        convert(source, "md", "pdf", filename="eng.md", pdf_engine="bogus", cache_bypass=True)
    assert caught.value.code == "invalid_document"
    assert "取值无效" in caught.value.message


def test_stuck_running_job_expires_without_a_reader(monkeypatch):
    store = JobStore()
    monkeypatch.setattr(settings, "convert_timeout_seconds", 1)
    job = Job(
        id="deadline-running",
        filename="stuck.md",
        source_format="md",
        target_format="md",
        status=JobStatus.running,
        created_at=time.time() - 30,
        heartbeat_at=time.time() - 30,
        memory_charge=12,
    )
    with store._lock:
        store._jobs[job.id] = job
        store._inflight_bytes += 12
    expired = store.sweep_deadlines()
    assert expired == ["deadline-running"]
    assert job.status == JobStatus.error
    assert job.code == "job_timeout"
    assert job.memory_charge == 0
    assert job.budget_settled is True


def test_page_grades_are_not_a_success_rate():
    from aduport.core.fidelity_page_grade import (
        CORPUS_MAX,
        CORPUS_MIN,
        CORPUS_READ,
        PAGE_GRADES,
        DocumentGradeSheet,
        PageGradeRow,
        assert_grade_list,
        sheets_from_payload,
        summarize_sheets,
    )

    assert CORPUS_READ is False
    assert (CORPUS_MIN, CORPUS_MAX) == (20, 30)
    assert set(PAGE_GRADES) == {"A", "B", "C", "D"}
    assert_grade_list([])
    with pytest.raises(ValueError, match="no business-document page read"):
        assert_grade_list(["A"])
    with pytest.raises(ValueError, match="success_rate"):
        sheets_from_payload({"success_rate": 0.99, "documents": []})
    summary = summarize_sheets(
        [
            DocumentGradeSheet(
                doc_id="a",
                source_path="a.docx",
                source_format="docx",
                target_format="md",
                kind="calibration",
                pages=[
                    PageGradeRow(page=1, grade="A"),
                    PageGradeRow(page=2, grade="C", checks_failed=["tables"]),
                ],
            )
        ]
    )
    body = summary.to_dict()
    assert body["by_grade"] == {"A": 1, "B": 0, "C": 1, "D": 0}
    assert body["worst"] == "C"
    assert body["ready_for_business_claim"] is False
    assert "success_rate" not in body
    assert "pass_rate" not in body


def test_worker_deadline_fires_without_polling(monkeypatch):
    import threading

    store = JobStore()
    monkeypatch.setattr(settings, "convert_timeout_seconds", 1)
    # 这条用例测的是「进程内线程 + 墙钟截止」这条路径。默认 convert_isolate=True 时
    # 截止由 convert_isolated 自己负责，本用例已把 convert_isolated 换成会挂住的桩，
    # 于是永远等不到超时（曾实测：job.code 恒为 None）。必须显式关掉 isolate。
    monkeypatch.setattr(settings, "convert_isolate", False)
    started = threading.Event()
    release = threading.Event()

    def hang(*_args, **_kwargs):
        started.set()
        release.wait(5)
        return b"late"

    monkeypatch.setattr("aduport.core.jobs.convert_isolated", hang)
    job = store.submit(
        content=b"# a\n",
        filename="hang.md",
        source_format="md",
        target_format="md",
    )
    assert started.wait(2)
    deadline = time.time() + 3
    while time.time() < deadline and job.code != "job_timeout":
        time.sleep(0.05)
    release.set()
    assert job.code == "job_timeout"
    assert job.status == JobStatus.error
