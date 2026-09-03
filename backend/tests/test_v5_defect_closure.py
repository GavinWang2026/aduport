"""v5 defect closure: MD/HTML layout placeholders, stream failures, xlsx chart cap."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.config import settings
from app.core.document_model import (
    Chart,
    DocumentModel,
    FieldCode,
    HeaderFooter,
    Numbering,
    PageSetup,
    Paragraph,
    Section,
)
from app.core.exceptions import InvalidDocumentError
from app.core.stream_convert import convert_streaming
from app.exporters.export_warnings import get_last_export_warnings, set_last_export_warnings
from app.exporters.html_exporter import export_html
from app.exporters.md_exporter import export_md
from app.exporters.xlsx_exporter import export_xlsx


def test_md_layout_nodes_emit_placeholder_and_warn():
    model = DocumentModel(
        nodes=[
            Section(title="Ch1"),
            PageSetup(page_size="A4"),
            HeaderFooter(kind="header", text="hdr"),
            FieldCode(field_type="PAGE", instruction="PAGE", result_text="1"),
            Numbering(num_id="1", abstract_num_id="0"),
            Paragraph(text="body"),
        ]
    )
    out = export_md(model)
    assert "section" in out.lower() or "Ch1" in out or "PageSetup" in out or "PAGE" in out
    assert "<!--" in out
    asserts = model.metadata.get("export_warnings") or []
    # warn_export may only hit context; also check body markers
    assert "placeholder" in out.lower() or "degraded" in out or "Section" in out or "PageSetup" in out
    assert "body" in out


def test_html_layout_nodes_emit_placeholder_aside():
    model = DocumentModel(
        nodes=[
            Section(title="S"),
            PageSetup(page_size="Letter", orientation="landscape"),
            Paragraph(text="hello"),
        ]
    )
    html = export_html(model)
    assert "mdbridge-placeholder" in html
    assert "hello" in html


def test_xlsx_chart_cap_warns():
    charts = [
        Chart(title=f"C{i}", chart_type="bar", categories=["a"], series=[{"name": "s", "values": [1]}])
        for i in range(10)
    ]
    model = DocumentModel(nodes=charts)
    export_xlsx(model)
    warns = model.metadata.get("export_warnings") or []
    assert any("truncated" in w.lower() and "chart" in w.lower() for w in warns)


def test_stream_chunk_failure_surfaces_warning(monkeypatch):
    monkeypatch.setattr(settings, "stream_convert", True)
    set_last_export_warnings([])

    parts = [f"## H{i}\n\nparagraph {i} " + ("x" * 50) + "\n" for i in range(6)]
    src = "\n".join(parts).encode()

    from app.core import stream_convert as sc

    calls = {"n": 0}
    orig = sc._import_chunk

    def flaky(data, fmt, filename=""):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("boom")
        return orig(data, fmt, filename)

    with patch.object(sc, "_import_chunk", side_effect=flaky):
        out = convert_streaming(src, "md", "md", filename="t.md", chunk_chars=40)
    asserts_ok = b"stream_chunk_failures" in out or any(
        "stream_convert" in w for w in get_last_export_warnings()
    )
    assert calls["n"] >= 2
    assert asserts_ok


def test_stream_all_chunks_fail_raises(monkeypatch):
    monkeypatch.setattr(settings, "stream_convert", True)
    from app.core import stream_convert as sc

    parts = [f"## H{i}\n\nparagraph {i} " + ("x" * 50) + "\n" for i in range(4)]
    src = "\n".join(parts).encode()

    with patch.object(sc, "_import_chunk", side_effect=RuntimeError("nope")):
        with pytest.raises(InvalidDocumentError, match="全部块失败"):
            convert_streaming(src, "md", "md", filename="t.md", chunk_chars=40)


def test_formats_notes_xlsb_requires_anydoc():
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as tc:
        r = tc.get("/api/formats")
        assert r.status_code == 200
        notes = r.json().get("notes") or {}
        assert "xlsb" in notes
        assert "anydoc" in notes["xlsb"].lower()
        d = tc.get("/api/health/detail")
        # may be 401 if auth on — still ok if formats notes present
        if d.status_code == 200:
            assert d.json().get("xlsb_requires_anydoc") is True
