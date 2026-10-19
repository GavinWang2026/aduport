"""v6 §08 next-step fixes: csv _CONTENT, stuck threads, PDF fonts, docx code shade,
claim demotions, business corpus intake scaffolding."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from docx import Document

from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.css_sanitize import compact_font_family
from aduport.core.document_model import CodeBlock, DocumentModel, Heading, Paragraph, RichText, Table
from aduport.core.jobs import JobStore
from aduport.core.registry import ensure_builtin_plugins, get_importer
from aduport.exporters.csv_exporter import export_csv
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.pdf_exporter import export_pdf

ROOT = Path(__file__).resolve().parents[2]
RULER = ROOT / "outputs" / "page_grade_ruler.py"


def test_csv_content_section_is_labeled_and_warns():
    ensure_builtin_plugins()
    model = DocumentModel(
        nodes=[
            Heading(level=1, text=RichText(text="ALPHA")),
            Paragraph(text=RichText(text="BRAVO")),
            Table(rows=[["区域", "Q1"], ["DELTA", "1"]], header_rows=1),
        ]
    )
    out = export_csv(model)
    assert "_CONTENT" in out
    assert "[AduPort] Markdown 正文区" in out
    warns = list((model.metadata or {}).get("export_warnings") or [])
    assert any("_CONTENT" in w for w in warns)
    assert any("csv_table_only" in w for w in warns)
    back = get_importer("csv")(out.encode(), "mixed.csv")
    kinds = {n.type for n in back.nodes}
    assert "table" in kinds
    assert "heading" in kinds


def test_csv_table_only_omits_content_section():
    model = DocumentModel(
        nodes=[
            Heading(level=1, text=RichText(text="ALPHA")),
            Table(rows=[["A", "B"], ["1", "2"]], header_rows=1),
        ]
    )
    out = export_csv(model, table_only=True)
    assert "_CONTENT" not in out
    assert "ALPHA" not in out
    assert "A" in out and "1" in out
    via_flag = convert(
        b"# ALPHA\n\n| A | B |\n|---|---|\n| 1 | 2 |\n",
        "md",
        "csv",
        "t.md",
        csv_table_only=True,
        cache_bypass=True,
    ).decode()
    assert "_CONTENT" not in via_flag


def test_compact_font_family_keeps_one_named_face():
    assert (
        compact_font_family(
            "Noto Sans CJK SC, Helvetica Neue, Arial, sans-serif"
        )
        == "Noto Sans CJK SC, sans-serif"
    )
    assert compact_font_family("PingFang SC") == "PingFang SC"


def test_chinese_pdf_is_smaller_than_v6_floor(monkeypatch):
    monkeypatch.setattr(
        settings,
        "pdf_font_family",
        "PingFang SC, Noto Sans CJK SC, Helvetica Neue, Arial, sans-serif",
    )
    monkeypatch.setattr(settings, "pdf_font_compact", True)
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="ALPHA 2026 报告"),
            Paragraph(text="BRAVO 这是一个用于校验的段落，包含 31415 这个数字。"),
            Heading(level=2, text="CHARLIE"),
            Paragraph(text="中文校验 结束段。"),
        ]
    )
    weasy = export_pdf(model, engine="weasyprint")
    simple = export_pdf(model, engine="simple")
    # v7: compact rewrites heavy CJK → Songti/STSong when available (~20KB).
    # Absolute floor stays under the old 250KB gate; relative cut is asserted in v7.
    assert len(weasy) < 250_000, len(weasy)
    # simple remains a size escape hatch; with Songti subset, weasy may be smaller.
    assert len(simple) < 80_000, len(simple)
    one = export_pdf(DocumentModel(nodes=[Paragraph(text="字")]), engine="weasyprint")
    assert len(one) < 40_000, len(one)


def test_docx_code_style_has_shading():
    model = DocumentModel(nodes=[CodeBlock(lang="py", content="print(1)")])
    blob = export_docx(model)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        styles = zf.read("word/styles.xml").decode()
    assert "AduPortCode" in styles or "AduPort Code" in styles
    assert 'w:fill="F4F4F5"' in styles or "F4F4F5" in styles
    doc = Document(io.BytesIO(blob))
    assert any(p.style and "Code" in (p.style.name or "") for p in doc.paragraphs)


def test_stuck_convert_thread_is_counted(monkeypatch):
    import threading
    import time

    store = JobStore()
    monkeypatch.setattr(settings, "convert_timeout_seconds", 1)
    monkeypatch.setattr(settings, "convert_isolate", False)
    started = threading.Event()
    release = threading.Event()

    def hang(*_args, **_kwargs):
        started.set()
        release.wait(5)
        return b"late"

    monkeypatch.setattr("aduport.core.jobs.convert_isolated", hang)
    before = store.stats().get("stuck_convert_threads", 0)
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
    assert store.stats()["stuck_convert_threads"] == before + 1
    from aduport.core.metrics import metrics

    assert "aduport_stuck_convert_threads" in metrics.prometheus_text()
    assert metrics.snapshot()["stuck_convert_threads"] >= 1


def test_pdf_uri_annotations_become_markdown_links():
    fixture = ROOT / "native/anydoc/tests/fixtures/pdf/text.pdf"
    if not fixture.is_file():
        pytest.skip("pdf fixture missing")
    ensure_builtin_plugins()
    from aduport.exporters.md_exporter import export_md

    raw = fixture.read_bytes()
    model = get_importer("pdf")(raw, "text.pdf")
    md = export_md(model)
    assert "[example](https://example.com/page)" in md
    assert "[a sibling file](sibling.odt)" in md
    assert int((model.metadata or {}).get("pdf_uri_links") or 0) >= 2


def test_init_business_worksheet_is_empty_scaffold(tmp_path, monkeypatch):
    """Intake tooling lists real files with empty grades; never flips CORPUS_READ."""
    monkeypatch.delenv("ADUPORT_PRIVATE_SAMPLES_DIR", raising=False)
    corpus = tmp_path / "docs"
    corpus.mkdir()
    (corpus / "memo.docx").write_bytes(b"PK\x03\x04fake")
    (corpus / "notes.md").write_text("# hello\n", encoding="utf-8")
    (corpus / "ignore.bin").write_bytes(b"\x00\x01")
    out = tmp_path / "worksheet.json"
    # Rewrite BUSINESS_OUT via cwd isolation: call module functions directly.
    sys.path.insert(0, str(ROOT / "outputs"))
    import page_grade_ruler as ruler  # noqa: E402

    monkeypatch.setattr(ruler, "BUSINESS_OUT", out)
    monkeypatch.setattr(ruler, "CORPUS_DIR", tmp_path / "empty_corpus")
    path = ruler.init_business(corpus_dir=corpus, target="md")
    assert path == out
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["kind"] == "business"
    assert payload["corpus_read"] is False
    assert payload["ready_for_business_claim"] is False
    assert "success_rate" not in payload
    assert payload["document_count"] == 2
    grades = [p.get("grade") for d in payload["documents"] for p in d["pages"]]
    assert grades == ["", ""]
    ids = {d["doc_id"] for d in payload["documents"]}
    assert "memo" in ids and "notes" in ids
    # Summarize must not invent rates; empty grades drop out of the summary.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    proc = subprocess.run(
        [sys.executable, str(RULER), "--summarize", str(out)],
        cwd=str(ROOT / "backend"),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["documents"] == 0
    assert summary["pages"] == 0
    assert summary["corpus_read"] is False
    assert summary["ready_for_business_claim"] is False
    assert "success_rate" not in summary
