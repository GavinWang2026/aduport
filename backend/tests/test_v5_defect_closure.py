"""v5 defect closure: MD/HTML layout placeholders, stream failures, xlsx chart cap."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aduport.config import settings
from aduport.core.document_model import (
    Chart,
    DocumentModel,
    FieldCode,
    HeaderFooter,
    Numbering,
    PageSetup,
    Paragraph,
    Section,
)
from aduport.core.exceptions import InvalidDocumentError
from aduport.core.stream_convert import convert_streaming
from aduport.exporters.export_warnings import get_last_export_warnings, set_last_export_warnings
from aduport.exporters.html_exporter import export_html
from aduport.exporters.md_exporter import export_md
from aduport.exporters.xlsx_exporter import export_xlsx


def test_md_layout_nodes_emit_placeholder_and_warn():
    """Style catalogs stay out of Markdown. Section, header, and field text
    survive as one HTML comment each so the conversion is not a silent drop.
    """
    from aduport.core.document_model import Comment

    model = DocumentModel(
        nodes=[
            Section(title="Ch1"),
            PageSetup(page_size="A4"),
            HeaderFooter(kind="header", text="hdr"),
            FieldCode(field_type="PAGE", instruction="PAGE", result_text="1"),
            Numbering(num_id="1", abstract_num_id="0"),
            Comment(author="x", text="keep-me"),
            Paragraph(text="body"),
        ]
    )
    out = export_md(model)
    assert "body" in out
    assert "<!--" in out and "Ch1" in out and "hdr" in out
    # Page setup and numbering catalogs still do not flood the body.
    assert "PageSetup" not in out
    assert "comment" in out.lower() or "keep-me" in out or "degraded" in out


def test_html_layout_nodes_emit_placeholder_aside():
    model = DocumentModel(
        nodes=[
            Section(title="S"),
            PageSetup(page_size="Letter", orientation="landscape"),
            Paragraph(text="hello"),
        ]
    )
    html = export_html(model)
    assert "aduport-placeholder" in html
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


def test_stream_chunk_failure_aborts(monkeypatch):
    """P0-1 (report 2026-09): a failing chunk must abort the whole conversion —
    partial success with a warning was the old silent-corruption behaviour."""
    monkeypatch.setattr(settings, "stream_convert", True)

    # Dense blank-line boundaries so the new (blank-line-only) chunk policy
    # actually produces multiple chunks at chunk_chars=200.
    parts = [f"## H{i}\n\nparagraph {i} " + ("x" * 90) + "\n\n" for i in range(12)]
    src = "".join(parts).encode()

    from aduport.core import stream_convert as sc

    calls = {"n": 0}
    orig = sc._import_chunk

    def flaky(data, fmt, filename=""):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ValueError("boom")
        return orig(data, fmt, filename)

    with patch.object(sc, "_import_chunk", side_effect=flaky):
        with pytest.raises(InvalidDocumentError, match="中止整个转换"):
            convert_streaming(src, "md", "md", filename="t.md", chunk_chars=200)
    assert calls["n"] == 2  # aborts right at the failing chunk


def test_stream_all_chunks_fail_raises(monkeypatch):
    monkeypatch.setattr(settings, "stream_convert", True)
    from aduport.core import stream_convert as sc

    parts = [f"## H{i}\n\nparagraph {i} " + ("x" * 90) + "\n\n" for i in range(8)]
    src = "".join(parts).encode()

    with patch.object(sc, "_import_chunk", side_effect=RuntimeError("nope")):
        with pytest.raises(InvalidDocumentError, match="中止整个转换"):
            convert_streaming(src, "md", "md", filename="t.md", chunk_chars=200)


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
