"""Packaging acceptance pack — unit-level functional clarity (no live HTTP).
"""

from __future__ import annotations

import io

import pytest

from aduport.config import settings
from aduport.core.converter import (
    SUPPORTED_SOURCE_FORMATS,
    SUPPORTED_TARGET_FORMATS,
    convert,
)
from aduport.core.document_model import DocumentModel, Heading, Paragraph
from aduport.core.exceptions import ExportError
from aduport.exporters.docx_exporter import export_docx


def test_formats_latex_azw3_importable():
    """B6: latex/azw3/kf8 are bidirectional (no longer export-only)."""
    assert {"azw3", "kf8", "latex"} <= SUPPORTED_SOURCE_FORMATS
    assert {"azw3", "kf8", "latex"} <= SUPPORTED_TARGET_FORMATS
    export_only = SUPPORTED_TARGET_FORMATS - SUPPORTED_SOURCE_FORMATS
    assert not ({"azw3", "kf8", "latex"} & export_only)


def test_acceptance_latex_roundtrip():
    """LaTeX export → import keeps section text."""
    md = "# Section One\n\nHello latex body.\n"
    tex = convert(md.encode("utf-8"), "md", "latex", filename="a.md")
    assert b"\\section" in tex or b"Section One" in tex
    back = convert(tex, "latex", "md", filename="a.tex")
    text = back.decode("utf-8")
    assert "Section One" in text or "Hello latex" in text


def test_acceptance_cjk_pdf_extractable():
    """B2: Chinese MD→PDF remains searchable."""
    md = "# 中文标题测试\n\n这是中文段落内容。\n"
    out = convert(md.encode("utf-8"), "md", "pdf", filename="a.md")
    assert len(out) > 1500
    assert out.count(b"?????") == 0
    try:
        from pypdf import PdfReader
    except ImportError:
        pytest.skip("pypdf not installed")
    text = PdfReader(io.BytesIO(out)).pages[0].extract_text() or ""
    assert "中文" in text or "标题" in text


def test_acceptance_md_docx_roundtrip():
    """B3: MD → DOCX → MD keeps heading text."""
    md = "# Hello Heading\n\nBody paragraph for roundtrip.\n"
    docx = convert(md.encode("utf-8"), "md", "docx", filename="a.md")
    assert len(docx) > 1000
    back = convert(docx, "docx", "md", filename="a.docx")
    text = back.decode("utf-8")
    assert "Hello Heading" in text
    assert "Body paragraph" in text


def test_acceptance_docx_max_nodes_guard(monkeypatch):
    """B7: oversized DOCX AST fails closed with docx_too_large."""
    monkeypatch.setattr(settings, "docx_max_nodes", 3)
    nodes = [Paragraph(text=f"p{i}") for i in range(10)]
    model = DocumentModel(title="big", nodes=[Heading(level=1, text="t"), *nodes])
    with pytest.raises(ExportError) as ei:
        export_docx(model)
    assert ei.value.code == "docx_too_large"


def test_acceptance_docx_max_nodes_default_documented():
    """Default soft cap is set for enterprise packaging docs."""
    assert int(settings.docx_max_nodes) >= 10_000


def test_acceptance_virus_fail_closed_without_binary(monkeypatch):
    """B10: virus_scan on + fail_closed + no clam → virus_scan_unavailable."""
    from aduport.core.exceptions import AduPortError
    from aduport.core import virus_scan as vs

    monkeypatch.setattr(settings, "virus_scan", True)
    monkeypatch.setattr(settings, "virus_scan_fail_closed", True)
    monkeypatch.setattr(vs.shutil, "which", lambda _name: None)
    with pytest.raises(AduPortError) as ei:
        vs.scan_bytes(b"hello", "a.bin")
    assert ei.value.code == "virus_scan_unavailable"


def test_acceptance_formats_api_export_only_excludes_latex_family():
    """B6 matrix: latex/azw3/kf8 are sources, not export_only."""
    export_only = SUPPORTED_TARGET_FORMATS - SUPPORTED_SOURCE_FORMATS
    assert "latex" not in export_only
    assert "azw3" not in export_only
    assert "kf8" not in export_only
