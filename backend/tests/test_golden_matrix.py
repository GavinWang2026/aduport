"""P2-5: golden fixture matrix — sources × md/docx/pdf/html targets.

Uses anydoc upstream fixtures (real Office/PDF samples). DOCX row requires
anydoc native wheel or docling (`md-bridge[office]`); mobi/azw3 rows use
programmatic golden samples (CI-safe, no docling).
"""

from __future__ import annotations

import os
import zipfile
from io import BytesIO

import pytest

from app.config import settings
from app.core.converter import convert
from app.core.document_model import DocumentModel, Footnote, Heading, Paragraph
from app.core.exceptions import InvalidDocumentError
from app.exporters.azw3_exporter import export_azw3, export_kf8
from app.exporters.epub_exporter import export_epub
from app.exporters.mobi_exporter import export_mobi
from app.importers import anydoc_bridge as bridge

FIX = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "native", "anydoc", "tests", "fixtures"
    )
)

TARGETS = ("md", "docx", "pdf", "html", "txt", "epub")

# rel path under FIX → md substring assertions after import
GOLDEN_ROWS: list[tuple[str, str, tuple[str, ...]]] = [
    ("docx", "docx/text.docx", ("docx", "text")),
    ("pptx", "pptx/pres.pptx", ("Deck", "Slide")),
    ("xlsx", "xlsx/sheet.xlsx", ("Percent", "Currency")),
    ("pdf", "pdf/text.pdf", ("Fixture", "Document")),
]

# Programmatic ebook golden samples (export → import matrix)
EBOOK_GOLDEN_ROWS: list[tuple[str, tuple[str, ...]]] = [
    ("mobi", ("GoldenChapter", "Sample body", "footnote")),
    ("azw3", ("GoldenChapter", "Sample body", "footnote")),
    ("kf8", ("GoldenChapter", "Sample body", "footnote")),
    ("epub", ("GoldenChapter", "Sample body", "footnote")),
]


def _fixture_bytes(rel: str) -> bytes:
    path = os.path.join(FIX, rel)
    if not os.path.isfile(path):
        pytest.skip(f"fixture missing: {rel}")
    return open(path, "rb").read()


def _docx_import_available() -> bool:
    if bridge.anydoc_available() and bridge.should_handle("docx"):
        return True
    try:
        import docx  # noqa: F401

        return True
    except ImportError:
        return False

def _binary_magic(target: str, data: bytes) -> bool:
    if target == "md":
        return len(data.strip()) > 10
    if target == "docx":
        return data[:2] == b"PK"
    if target == "pdf":
        return data[:4] == b"%PDF"
    if target == "html":
        head = data[:800].decode("utf-8", errors="replace").lower()
        return "<html" in head or "<!doctype" in head or "class='comment'" in head
    if target == "txt":
        return len(data.strip()) > 8
    if target == "epub":
        return data[:2] == b"PK"
    return len(data) > 20


def _epub_spine_text(data: bytes) -> str:
    """Extract XHTML body text from EPUB zip for golden assertions."""
    if data[:2] != b"PK":
        return ""
    parts: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for name in zf.namelist():
            lower = name.lower()
            if not lower.endswith((".xhtml", ".html", ".htm")):
                continue
            if "nav.xhtml" in lower or "/nav." in lower:
                continue
            try:
                parts.append(zf.read(name).decode("utf-8", errors="replace"))
            except KeyError:
                continue
    return "\n".join(parts)


def _assert_needles(tgt_fmt: str, out: bytes, needles: tuple[str, ...], label: str) -> None:
    if not needles or tgt_fmt not in ("md", "html", "txt", "epub"):
        return
    if tgt_fmt == "epub":
        if out[:2] != b"PK":
            raise AssertionError(f"{label} epub not a zip")
        body = _epub_spine_text(out)
        assert body, f"{label} epub has no spine xhtml"
        assert any(n.lower() in body.lower() for n in needles), (
            f"{label} epub body missing {needles}"
        )
        return
    text = out.decode("utf-8", errors="replace")
    assert any(n.lower() in text.lower() for n in needles), f"{label} {tgt_fmt} missing {needles}"


@pytest.fixture
def golden_light_pdf(monkeypatch):
    """Prefer text-layer PDF path (no OCR) for digital fixtures."""
    monkeypatch.setattr(settings, "pdf_engine", "text")
    monkeypatch.setattr(settings, "pdf_text_min_chars", 8)
    monkeypatch.setattr(settings, "ocr_enabled", False)


def _pdf_text_import_available() -> bool:
    try:
        import pypdfium2  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.parametrize("src_fmt,rel,needles", GOLDEN_ROWS)
@pytest.mark.parametrize("tgt_fmt", TARGETS)
def test_golden_matrix_row(
    src_fmt: str,
    rel: str,
    needles: tuple[str, ...],
    tgt_fmt: str,
    golden_light_pdf,
):
    if src_fmt == "docx" and not _docx_import_available():
        pytest.skip("docx import needs anydoc wheel or md-bridge[office]")
    if src_fmt == "pdf" and not _pdf_text_import_available():
        pytest.skip("pdf text import needs pypdfium2")

    raw = _fixture_bytes(rel)
    out = convert(raw, src_fmt, tgt_fmt, filename=os.path.basename(rel))
    assert out and len(out) > 20, f"{rel} → {tgt_fmt} empty"
    assert _binary_magic(tgt_fmt, out), f"{rel} → {tgt_fmt} bad magic"
    _assert_needles(tgt_fmt, out, needles, rel)


@pytest.mark.skipif(not bridge.anydoc_available(), reason="anydoc wheel not installed")
def test_golden_docx_anydoc_channel():
    """Native-build job: docx fixture through anydoc fast path."""
    raw = _fixture_bytes("docx/handmade-tables.docx")
    md = convert(raw, "docx", "md", filename="handmade-tables.docx")
    text = md.decode("utf-8", errors="replace")
    assert "|" in text


def _build_ebook_golden_sample(fmt: str) -> bytes:
    """Programmatic ebook golden sample with heading, body, footnote (CI-safe)."""
    model = DocumentModel(
        title="GoldenChapter",
        nodes=[
            Heading(level=1, text="GoldenChapter"),
            Paragraph(text="Sample body for golden matrix."),
            Footnote(id="1", text="Golden footnote sample."),
        ],
    )
    if fmt == "mobi":
        return export_mobi(model)
    if fmt == "azw3":
        return export_azw3(model)
    if fmt == "kf8":
        return export_kf8(model)
    if fmt == "epub":
        return export_epub(model)
    raise ValueError(fmt)


@pytest.mark.parametrize("src_fmt,needles", EBOOK_GOLDEN_ROWS)
@pytest.mark.parametrize("tgt_fmt", TARGETS)
def test_golden_ebook_matrix_row(
    src_fmt: str,
    needles: tuple[str, ...],
    tgt_fmt: str,
    golden_light_pdf,
):
    """mobi/azw3/kf8/epub golden samples × md/docx/pdf/html/txt/epub."""
    raw = _build_ebook_golden_sample(src_fmt)
    assert len(raw) > 64
    if src_fmt == "epub":
        assert raw[:2] == b"PK"
    out = convert(raw, src_fmt, tgt_fmt, filename=f"golden.{src_fmt}")
    assert out and len(out) > 20, f"{src_fmt} → {tgt_fmt} empty"
    assert _binary_magic(tgt_fmt, out), f"{src_fmt} → {tgt_fmt} bad magic"
    _assert_needles(tgt_fmt, out, needles, src_fmt)
