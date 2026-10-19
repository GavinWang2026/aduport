"""v8 P1–P3: PDF code CJK font, HTML table grid width, bool 422, font default."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.document_model import CodeBlock, DocumentModel, Paragraph
from aduport.core.registry import ensure_builtin_plugins, get_importer
from aduport.exporters.html_render import _render_table_html
from aduport.exporters.md_exporter import export_md
from aduport.exporters.pdf_exporter import export_pdf
from aduport.importers.docx_text import try_extract_docx_model
from main import app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "require_auth", False)
    return TestClient(app)


def test_pdf_code_block_with_han_stays_compact(monkeypatch):
    from io import BytesIO

    from pypdf import PdfReader

    monkeypatch.setattr(settings, "pdf_font_compact", True)
    monkeypatch.setattr(
        settings,
        "pdf_font_family",
        "Songti SC, STSong, Noto Sans CJK SC, sans-serif",
    )
    model = DocumentModel(
        nodes=[
            Paragraph(text="正文一句。"),
            CodeBlock(lang="text", content="print('你好世界')"),
        ]
    )
    out = export_pdf(model)
    # Escaped &#x27; in <style> used to pull Nanum/Hiragino (~30–118KB).
    assert len(out) < 40_000, f"code-block PDF still oversized: {len(out)}B"
    fonts: set[str] = set()
    for page in PdfReader(BytesIO(out)).pages:
        res = page.get("/Resources") or {}
        font = res.get("/Font") or {}
        if hasattr(font, "items"):
            for _, ref in font.items():
                try:
                    fonts.add(str(ref.get("/BaseFont") or ""))
                except Exception:
                    fonts.add(str(ref))
    joined = " ".join(fonts).lower()
    assert "nanum" not in joined and "pingfang" not in joined, fonts
    assert "songti" in joined or "stsong" in joined, fonts


def test_docx_span_table_keeps_full_grid_width():
    fixture = ROOT / "native/anydoc/tests/fixtures/docx/handmade-tables.docx"
    if not fixture.is_file():
        pytest.skip("docx tables fixture missing")
    model = try_extract_docx_model(fixture.read_bytes())
    table = next(n for n in model.nodes if n.type == "table")
    assert [len(r) for r in table.cells] == [3, 3, 3]
    # Middle row: empty cells under A/B, covered continuation under C.
    assert table.cells[1][0].covered is False
    assert table.cells[1][1].covered is False
    assert table.cells[1][2].covered is True
    html = _render_table_html(table)
    assert html.count("<tr>") == 3
    assert 'rowspan="2"' in html
    assert 'colspan="2"' in html
    # Empty cells under the rowspan still occupy columns (not a blank <tr>).
    assert "<tr>\n</tr>" not in html
    ensure_builtin_plugins()
    back = get_importer("md")(export_md(model).encode(), "t.md")
    round_table = next(n for n in back.nodes if n.type == "table")
    widths = [len(r) for r in (round_table.cells or round_table.rows)]
    assert widths == [3, 3, 3]


def test_http_illegal_bool_is_422(client):
    response = client.post(
        "/api/convert",
        files={"file": ("t.md", b"# Hi\n", "text/markdown")},
        data={
            "source_format": "md",
            "target_format": "csv",
            "csv_table_only": "bogus",
        },
    )
    assert response.status_code == 422, response.text[:400]
    assert "布尔参数" in response.text
    assert "bogus" in response.text


def test_http_bool_false_token_still_works(client):
    response = client.post(
        "/api/convert",
        files={"file": ("t.md", b"# ALPHA\n\n| A | B |\n|---|---|\n| 1 | 2 |\n", "text/markdown")},
        data={
            "source_format": "md",
            "target_format": "csv",
            "csv_table_only": "false",
        },
    )
    assert response.status_code == 200, response.text[:300]
    assert "_CONTENT" in response.content.decode()


def test_http_illegal_int_param_is_422(client):
    response = client.post(
        "/api/convert",
        files={"file": ("t.md", b"# Hi\n", "text/markdown")},
        data={
            "source_format": "md",
            "target_format": "json",
            "rag_max_chars": "not-a-number",
        },
    )
    assert response.status_code == 422, response.text[:400]
    assert "rag_max_chars" in response.text


def test_default_pdf_font_family_matches_changelog():
    # config.py default (ignore local .env override for the assertion target).
    from aduport.config import Settings

    plain = Settings.model_fields["pdf_font_family"].default
    assert plain.startswith("Songti SC")
    assert "STSong" in plain
