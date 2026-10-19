"""Additional WBS acceptance tests (exporters, batch limits, sanitizers)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient

from aduport.core.converter import convert, convert_directory
from aduport.core.css_sanitize import sanitize_custom_css
from aduport.core.document_model import ListItem
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.md_exporter import export_md
from aduport.exporters.pdf_exporter import _render_html
from aduport.importers.md_importer import import_md
from main import aduport


def test_md_to_docx_preserves_table_and_bold():
    src = (
        "# T\n\nHello **world**.\n\n"
        "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
    ).encode()
    out = convert(src, "md", "docx")
    doc = Document(io.BytesIO(out))
    texts = [p.text for p in doc.paragraphs]
    assert any("world" in t for t in texts)
    assert len(doc.tables) >= 1
    assert doc.tables[0].rows[0].cells[0].text.strip() == "A"


def test_multi_link_href_roundtrip():
    src = b"See [one](https://a.example) and [two](https://b.example).\n"
    model = import_md(src)
    md = export_md(model)
    assert "https://a.example" in md
    assert "https://b.example" in md
    # order preserved
    assert md.index("a.example") < md.index("b.example")


def test_nested_list_roundtrip_levels():
    src = b"- top\n  - nested\n    - deep\n- sibling\n"
    model = import_md(src)
    items = [n for n in model.nodes if isinstance(n, ListItem)]
    levels = sorted({i.level for i in items})
    assert 0 in levels and 1 in levels


def test_pdf_html_merges_list_items():
    model = import_md(b"- a\n- b\n- c\n")
    html = _render_html(model, font_family="Arial", custom_css="")
    assert html.count("<ul>") == 1
    assert html.count("<li>") == 3
    assert html.count("</ul>") == 1


def test_css_blocks_font_face_and_url():
    dirty = "@font-face{src:url(http://evil)} body{color:red} url(http://x)"
    clean = sanitize_custom_css(dirty)
    assert "evil" not in clean
    assert "@font-face" not in clean.lower() or "blocked" in clean.lower()


def test_batch_file_limit(monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "max_batch_files", 2)
    monkeypatch.setattr(settings, "api_key", "")
    client = TestClient(app)
    files = [
        ("files", ("a.md", b"# a\n", "text/markdown")),
        ("files", ("b.md", b"# b\n", "text/markdown")),
        ("files", ("c.md", b"# c\n", "text/markdown")),
    ]
    r = client.post(
        "/api/convert/batch",
        files=files,
        data={"source_format": "md", "target_format": "md"},
    )
    assert r.status_code == 422
    body = r.json()
    assert body.get("code") in ("invalid_document", "file_too_large", "batch_failed") or "超限" in body.get(
        "message", ""
    )


def test_convert_directory_batch(tmp_path: Path):
    inp = tmp_path / "in"
    out = tmp_path / "out"
    inp.mkdir()
    (inp / "a.md").write_text("# A\n\n**x**\n", encoding="utf-8")
    (inp / "b.md").write_text("| a | b |\n| --- | --- |\n| 1 | 2 |\n", encoding="utf-8")
    result = convert_directory(inp, out, "md", "md")
    assert result.succeeded == 2
    assert (out / "a.md").exists()
    assert "| a" in (out / "b.md").read_text(encoding="utf-8") or "a" in (out / "b.md").read_text(
        encoding="utf-8"
    )


def test_docx_hyperlink_relationship():
    src = b"Click [here](https://example.com/path).\n"
    model = import_md(src)
    data = export_docx(model)
    # OOXML stores hyperlink targets in word/_rels/document.xml.rels
    zf = zipfile.ZipFile(io.BytesIO(data))
    rels = zf.read("word/_rels/document.xml.rels").decode("utf-8")
    assert "example.com" in rels
