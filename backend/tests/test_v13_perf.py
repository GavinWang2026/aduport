"""v0.13: DOCX FastBody O(1) append + stream_convert covers docx/pdf."""

from __future__ import annotations

import time
import zipfile
from io import BytesIO

from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.document_model import DocumentModel, ListItem, Paragraph
from aduport.core.stream_convert import can_stream_convert, convert_streaming
from aduport.exporters.docx_exporter import export_docx


def test_docx_fast_scales_near_linear():
    def run(n: int) -> float:
        nodes = [Paragraph(text=f"p{i} filler text") for i in range(n)]
        m = DocumentModel(title="t", nodes=nodes)
        t0 = time.perf_counter()
        data = export_docx(m)
        dt = time.perf_counter() - t0
        assert data[:2] == b"PK"
        with zipfile.ZipFile(BytesIO(data)) as zf:
            assert "word/document.xml" in zf.namelist()
            assert b"sectPr" in zf.read("word/document.xml")
        return dt

    t3 = run(3000)
    t10 = run(10000)
    # 10k/3k ≈ 3.3× nodes; allow up to ~5× time (near-linear, not O(n²)~11×)
    assert t10 / max(t3, 1e-6) < 6.0, f"scaling too steep: {t3:.3f}s → {t10:.3f}s"


def test_docx_list_fast_path():
    nodes = [ListItem(text=f"item {i}", ordered=False, level=0) for i in range(500)]
    data = export_docx(DocumentModel(title="L", nodes=nodes))
    with zipfile.ZipFile(BytesIO(data)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8")
    assert "item 0" in xml and "item 499" in xml


def test_stream_covers_docx_and_pdf(monkeypatch):
    monkeypatch.setattr(settings, "stream_convert", True)
    monkeypatch.setattr(settings, "stream_chunk_chars", 2000)
    monkeypatch.setattr(settings, "stream_min_bytes", 100)
    assert can_stream_convert("md", "docx")
    assert can_stream_convert("md", "pdf")
    parts = [f"## H{i}\n\nparagraph {i} " + ("x" * 80) + "\n" for i in range(40)]
    src = "\n".join(parts).encode()
    docx = convert_streaming(src, "md", "docx")
    assert docx[:2] == b"PK"
    pdf = convert_streaming(src, "md", "pdf")
    assert pdf.startswith(b"%PDF")
    # Through convert() gate
    out = convert(src, "md", "docx")
    assert out[:2] == b"PK"
