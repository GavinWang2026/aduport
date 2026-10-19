"""P1 batch regression tests (correctness / ops hardening)."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.document_model import DocumentModel, Image, ListItem, Paragraph
from aduport.core.stream_convert import _SpillWriter
from aduport.core.tenants import clear_tenant_cache, find_tenant_by_key
from aduport.importers.docx_ooxml import enrich_docx_model
from aduport.importers.html_importer import import_html
from aduport.importers.md_importer import import_md
from aduport.importers.pdf_font_fix import normalize_pdf_text
from main import aduport


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "require_auth", False)
    monkeypatch.setattr(settings, "api_key", "")
    return TestClient(app)


def test_health_liveness_vs_detail(client):
    live = client.get("/api/health").json()
    assert live["status"] == "ok"
    assert "metrics" not in live
    detail = client.get("/api/health/detail").json()
    assert "metrics" in detail
    assert "conversions_by_tenant" not in detail["metrics"]


def test_license_hides_machine_id(client):
    body = client.get("/api/license").json()
    assert "machine_id" not in body
    assert "required" in body
    lic = body.get("license")
    if isinstance(lic, dict):
        assert "machine_id" not in lic
        assert "host_machine_id" not in lic


def test_pep_font_fix_does_not_mangle_normal_chinese():
    assert "犇腾牛排" == normalize_pdf_text("犇腾牛排")
    # Dense math-font garbage still remaps
    raw = "直线犾垂直平分线段犃犅，犘１在犾上"
    fixed = normalize_pdf_text(raw)
    assert "犃" not in fixed


def test_docx_header_stays_in_metadata_only():
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>hi</w:t></w:r></w:p></w:body></w:document>",
        )
    out = enrich_docx_model(DocumentModel(title="t", nodes=[]), buf.getvalue())
    assert not any(
        isinstance(n, Paragraph) and "页眉" in str(getattr(n, "text", ""))
        for n in out.nodes
    )


def test_spill_writer_append_is_linear(monkeypatch):
    monkeypatch.setattr(settings, "spill_threshold_mb", 1)
    w = _SpillWriter()
    chunk = b"x" * (512 * 1024)
    for _ in range(6):
        w.write(chunk)
    data = w.getvalue()
    assert len(data) == 6 * len(chunk)


def test_html_nested_list_keeps_parent_text():
    html = b"<ul><li>outer A<ul><li>inner B</li></ul></li></ul>"
    model = import_html(html, "t.html")
    texts = [
        n.text.text if hasattr(n.text, "text") else str(n.text)
        for n in model.nodes
        if isinstance(n, ListItem)
    ]
    assert any("outer A" in t for t in texts)
    assert any("inner B" in t for t in texts)


def test_html_img_extracted():
    html = b'<p>hi</p><img src="https://example.com/a.png" alt="pic"/>'
    model = import_html(html, "t.html")
    imgs = [n for n in model.nodes if isinstance(n, Image)]
    assert imgs
    assert imgs[0].url and "example.com" in imgs[0].url


def test_md_table_header_rows_and_inline():
    md = b"| **Name** | Age |\n| --- | --- |\n| *Ada* | 1 |\n"
    model = import_md(md)
    tables = [n for n in model.nodes if getattr(n, "type", None) == "table"]
    assert tables
    t = tables[0]
    assert t.header_rows >= 1
    flat = " ".join(c for row in t.rows for c in row)
    assert "**" not in flat
    assert "Name" in flat
    assert "Ada" in flat


def test_tenant_compare_digest(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "acme:secret-key-one")
    clear_tenant_cache()
    hit = find_tenant_by_key("secret-key-one")
    assert hit is not None and hit.tenant_id == "acme"
    assert find_tenant_by_key("wrong") is None
    clear_tenant_cache()


def test_job_persist_wal_singleton(tmp_path, monkeypatch):
    db = tmp_path / "jobs.sqlite3"
    monkeypatch.setattr(settings, "job_db_path", str(db))
    from aduport.core import job_persist as jp

    jp._conn = None
    jp._conn_path = None
    jp._migrated = False
    jp.save_job_snapshot("abc", {"id": "abc", "status": "done"})
    snap = jp.load_job_snapshot("abc")
    assert snap and snap["id"] == "abc"
    c1 = jp._conn
    jp.save_job_snapshot("abc", {"id": "abc", "status": "done", "n": 2})
    assert jp._conn is c1
