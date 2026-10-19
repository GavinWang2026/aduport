"""v7 P1–P3: csv_table_only surfaces, banner skip, PDF subsettable fonts, doc_id."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aduport.cli import aduport as cli_app
from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.css_sanitize import prefer_subsettable_cjk
from aduport.core.document_model import DocumentModel, Heading, Paragraph, Table
from aduport.core.registry import ensure_builtin_plugins, get_importer
from aduport.exporters.csv_exporter import export_csv
from aduport.exporters.pdf_exporter import export_pdf
from main import aduport

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "require_auth", False)
    return TestClient(app)


def test_http_csv_table_only_omits_content(client):
    response = client.post(
        "/api/convert",
        files={"file": ("t.md", b"# ALPHA\n\n| A | B |\n|---|---|\n| 1 | 2 |\n", "text/markdown")},
        data={
            "source_format": "md",
            "target_format": "csv",
            "csv_table_only": "true",
        },
    )
    assert response.status_code == 200, response.text[:300]
    body = response.content.decode()
    assert "_CONTENT" not in body
    assert "A" in body and "1" in body


def test_http_unknown_convert_param_is_422(client):
    response = client.post(
        "/api/convert",
        files={"file": ("t.md", b"# Hi\n", "text/markdown")},
        data={
            "source_format": "md",
            "target_format": "html",
            "not_a_real_knob": "1",
        },
    )
    assert response.status_code == 422, response.text[:400]
    assert "未知转换参数" in response.text
    assert "not_a_real_knob" in response.text


def test_cli_has_csv_table_only_flag():
    result = CliRunner().invoke(cli_app, ["convert", "--help"])
    assert result.exit_code == 0
    assert "--csv-table-only" in result.stdout


def test_csv_banner_is_not_reimported_as_heading():
    ensure_builtin_plugins()
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="ALPHA 2026 报告"),
            Paragraph(text="BRAVO"),
            Heading(level=2, text="CHARLIE 数据"),
            Table(rows=[["区域", "Q1"], ["DELTA", "1"]], header_rows=1),
        ]
    )
    out = export_csv(model)
    assert "# [AduPort]" in out
    back = get_importer("csv")(out.encode(), "mixed.csv")
    headings = [
        n.plain_text() if hasattr(n, "plain_text") else str(getattr(n, "text", ""))
        for n in back.nodes
        if getattr(n, "type", None) == "heading"
    ]
    assert headings == ["ALPHA 2026 报告", "CHARLIE 数据"]
    assert not any("AduPort" in h for h in headings)


def test_prefer_subsettable_cjk_rewrites_pingfang():
    out = prefer_subsettable_cjk("PingFang SC, sans-serif")
    assert out.startswith("Songti SC")
    assert "PingFang SC" in out


def test_chinese_pdf_compact_beats_pingfang_baseline(monkeypatch):
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="ALPHA 2026 报告"),
            Paragraph(text="BRAVO 这是一个用于校验的段落，包含 31415 这个数字。"),
            Heading(level=2, text="CHARLIE"),
            Paragraph(text="中文校验 结束段。"),
        ]
    )
    monkeypatch.setattr(settings, "pdf_font_compact", False)
    heavy = export_pdf(model, font_family="PingFang SC", engine="weasyprint")
    monkeypatch.setattr(settings, "pdf_font_compact", True)
    # Compact + prefer_subsettable rewrites PingFang → Songti/STSong when present.
    compact = export_pdf(model, font_family="PingFang SC", engine="weasyprint")
    songti = export_pdf(model, font_family="Songti SC", engine="weasyprint")
    if len(songti) < len(heavy) * 0.5:
        assert len(compact) < len(heavy) * 0.5, (len(compact), len(heavy))
        assert len(compact) < 50_000, len(compact)
    else:
        # No subsettable face on this host — compact must not grow vs loose stack.
        monkeypatch.setattr(settings, "pdf_font_compact", False)
        loose = export_pdf(
            model,
            font_family="Noto Sans CJK SC, Helvetica Neue, Arial, sans-serif",
            engine="weasyprint",
        )
        monkeypatch.setattr(settings, "pdf_font_compact", True)
        tight = export_pdf(
            model,
            font_family="Noto Sans CJK SC, Helvetica Neue, Arial, sans-serif",
            engine="weasyprint",
        )
        assert len(tight) <= len(loose)


def test_business_doc_id_collision_uses_counter(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "outputs"))
    import page_grade_ruler as ruler  # noqa: E402

    corpus = tmp_path / "docs"
    corpus.mkdir()
    (corpus / "text.docx").write_bytes(b"PK\x03\x04a")
    (corpus / "text.md").write_text("# a\n", encoding="utf-8")
    (corpus / "handmade-tables.xlsx").write_bytes(b"PK\x03\x04b")
    out = tmp_path / "ws.json"
    monkeypatch.setattr(ruler, "BUSINESS_OUT", out)
    monkeypatch.setattr(ruler, "CORPUS_DIR", tmp_path / "empty")
    monkeypatch.delenv("ADUPORT_PRIVATE_SAMPLES_DIR", raising=False)
    ruler.init_business(corpus_dir=corpus, target="md")
    payload = json.loads(out.read_text(encoding="utf-8"))
    ids = [d["doc_id"] for d in payload["documents"]]
    assert "handmade-tables" in ids
    assert "text" in ids
    assert "text-2" in ids
    assert "text-4" not in ids


def test_odt_formula_objects_become_math_blocks():
    fixture = ROOT / "native/anydoc/tests/fixtures/odt/handmade-math.odt"
    if not fixture.is_file():
        pytest.skip("odt math fixture missing")
    ensure_builtin_plugins()
    model = get_importer("odt")(fixture.read_bytes(), fixture.name)
    formulas = [
        n.content
        for n in model.nodes
        if getattr(n, "type", "") == "math_block"
    ]
    assert any("mc" in f and "2" in f for f in formulas)
    assert any("frac" in f and "sqrt" in f for f in formulas)


def test_docx_legacy_table_keeps_rowspan_and_colspan():
    fixture = ROOT / "native/anydoc/tests/fixtures/docx/handmade-tables.docx"
    if not fixture.is_file():
        pytest.skip("docx tables fixture missing")
    from aduport.importers.docx_text import try_extract_docx_model
    from aduport.exporters.md_exporter import export_md

    model = try_extract_docx_model(fixture.read_bytes())
    table = next(n for n in model.nodes if n.type == "table")
    head_c = table.cells[0][2]
    assert head_c.row_span == 2
    assert "under C" in head_c.text
    legacy = table.cells[2][0]
    assert legacy.col_span == 2
    md = export_md(model)
    assert 'rowspan="2"' in md
    assert 'colspan="2"' in md


def test_isolate_job_timeout_does_not_abandon_a_daemon(monkeypatch):
    """Isolate-on path returns on this thread; a slow convert is not a stuck daemon."""
    import threading
    import time

    from aduport.core.jobs import JobStore

    store = JobStore()
    monkeypatch.setattr(settings, "convert_timeout_seconds", 1)
    monkeypatch.setattr(settings, "convert_isolate", True)
    started = threading.Event()

    def slow(*_args, **_kwargs):
        started.set()
        time.sleep(1.6)
        return b"late"

    monkeypatch.setattr("aduport.core.jobs.convert_isolated", slow)
    before = store.stats().get("stuck_convert_threads", 0)
    job = store.submit(
        content=b"# a\n",
        filename="slow.md",
        source_format="md",
        target_format="md",
    )
    assert started.wait(2)
    deadline = time.time() + 4
    while time.time() < deadline and job.status == "running":
        time.sleep(0.05)
    assert store.stats()["stuck_convert_threads"] == before


def test_docx_ole_object_not_duplicated():
    fixture = ROOT / "native/anydoc/tests/fixtures/docx/handmade-rich.docx"
    if not fixture.is_file():
        pytest.skip("docx rich fixture missing")
    ensure_builtin_plugins()
    model = get_importer("docx")(fixture.read_bytes(), fixture.name)
    oles = [n for n in model.nodes if n.type == "ole_object"]
    assert len(oles) == 1
    assert oles[0].prog_id == "Excel.Sheet.12"
    assert oles[0].data


def test_docx_chart_values_reach_markdown():
    fixture = ROOT / "native/anydoc/tests/fixtures/docx/handmade-rich.docx"
    if not fixture.is_file():
        pytest.skip("docx rich fixture missing")
    from aduport.exporters.md_exporter import export_md

    ensure_builtin_plugins()
    model = get_importer("docx")(fixture.read_bytes(), fixture.name)
    md = export_md(model)
    assert "| Q1 | 10 |" in md
    assert "| Q2 | 14 |" in md
    assert "Plan" in md and "Ship" in md
    assert "![tiny dot image]" in md or "tiny dot" in md


def test_pptx_ole_is_not_a_fake_image():
    fixture = ROOT / "native/anydoc/tests/fixtures/pptx/handmade-order.pptx"
    if not fixture.is_file():
        pytest.skip("pptx order fixture missing")
    from aduport.exporters.md_exporter import export_md

    ensure_builtin_plugins()
    model = get_importer("pptx")(fixture.read_bytes(), fixture.name)
    assert any(n.type == "ole_object" for n in model.nodes)
    assert not any(
        n.type == "image" and "ole" in (getattr(n, "mime_type", "") or "")
        for n in model.nodes
    )
    md = export_md(model)
    assert "Title placed second" in md
    assert "data:application/vnd.ms-ole-object" not in md
    assert "Excel.Sheet.12" in md


def test_python_convert_csv_table_only_still_works():
    out = convert(
        b"# ALPHA\n\n| A | B |\n|---|---|\n| 1 | 2 |\n",
        "md",
        "csv",
        "t.md",
        csv_table_only=True,
        cache_bypass=True,
    ).decode()
    assert "_CONTENT" not in out
