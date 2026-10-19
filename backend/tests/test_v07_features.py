"""v0.7: export matrix, PDF layout, SSRF fetch, spill buffer."""

from __future__ import annotations

import zipfile
from io import BytesIO

from aduport.config import settings
from aduport.core.converter import SUPPORTED_TARGET_FORMATS, convert
from aduport.core.document_model import DocumentModel, Heading, Image, Paragraph
from aduport.core.safe_fetch import is_safe_url
from aduport.core.spill import SpillBuffer
from aduport.exporters.epub_exporter import export_epub
from aduport.exporters.html_exporter import export_html
from aduport.exporters.pdf_exporter import _normalize_page_size, _render_html
from aduport.exporters.txt_exporter import export_txt


def test_target_formats_expanded():
    assert {"html", "txt", "epub", "md", "docx", "pdf"} <= SUPPORTED_TARGET_FORMATS


def test_md_to_html_txt_epub():
    src = b"# Hello\n\nPara with **bold**.\n\n## Sec\n"
    html = convert(src, "md", "html").decode("utf-8")
    assert "<h1" in html and "Hello" in html
    assert "<strong>bold</strong>" in html or "bold" in html

    txt = convert(src, "md", "txt").decode("utf-8")
    assert "Hello" in txt and "Para" in txt

    epub = convert(src, "md", "epub")
    assert epub[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(epub)) as zf:
        assert "mimetype" in zf.namelist()
        assert zf.read("mimetype") == b"application/epub+zip"
        assert any(n.endswith("chapter.xhtml") for n in zf.namelist())


def test_pdf_page_layout_css():
    assert _normalize_page_size("A4", "landscape") == "A4 landscape"
    assert _normalize_page_size("Letter", "portrait") == "Letter"
    model = DocumentModel(title="T", nodes=[Heading(level=1, text="H1"), Paragraph(text="p")])
    html = _render_html(
        model,
        font_family="Arial",
        custom_css="",
        page_size="A4",
        orientation="landscape",
        header="HDR",
        footer="FTR",
        page_numbers=True,
        toc=True,
    )
    assert "A4 landscape" in html
    assert "HDR" in html
    assert "counter(page)" in html
    assert 'class="toc"' in html
    assert "bookmark-level" in html


def test_ssrf_blocks_private():
    assert is_safe_url("http://127.0.0.1/x") is False
    assert is_safe_url("http://localhost/x") is False
    assert is_safe_url("http://169.254.169.254/latest") is False
    assert is_safe_url("file:///etc/passwd") is False


def test_spill_buffer_threshold(monkeypatch):
    monkeypatch.setattr(settings, "spill_threshold_mb", 1)
    big = b"x" * (1024 * 1024)  # 1 MiB >= threshold
    buf = SpillBuffer(big, prefix="t-")
    assert buf.spilled is True
    assert buf.read() == big
    buf.release()

    monkeypatch.setattr(settings, "spill_threshold_mb", 100)
    buf2 = SpillBuffer(b"small", prefix="t2-")
    assert buf2.spilled is False
    assert buf2.read() == b"small"
    buf2.release()


def test_export_helpers_direct():
    model = DocumentModel(
        title="Doc",
        nodes=[Heading(level=1, text="A"), Paragraph(text="b")],
    )
    assert "Doc" in export_html(model)
    assert "A" in export_txt(model)
    assert export_epub(model)[:2] == b"PK"


def test_remote_image_placeholder_without_fetch(monkeypatch):
    monkeypatch.setattr(settings, "fetch_remote_images", False)
    model = DocumentModel(
        nodes=[Image(data=b"", alt_text="pic|https://example.com/a.png", mime_type="image/png")]
    )
    from aduport.core.remote_images import resolve_remote_images

    out = resolve_remote_images(model)
    assert out.nodes[0].data == b""
