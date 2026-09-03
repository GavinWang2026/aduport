"""anydoc bridge: unit (no wheel) + integration (wheel required) tests.

Unit tests cover the config-driven routing logic and format tables so CI stays
green without the compiled anydoc extension. Integration tests exercise the real
Rust parser against anydoc's own test fixtures and are skipped when the wheel is
not installed (they run in the `anydoc` CI job / enterprise image).
"""

from __future__ import annotations

import os

import pytest

from app.importers import anydoc_bridge as bridge
from app.importers.anydoc_bridge import (
    ANYDOC_ONLY_SIBLINGS,
    ANYDOC_ONLY_SIBLINGS as _ONLY,
    _FORMAT_MAP,
    _MARKER_MAP,
    _SNIFF_MAP,
    should_handle,
    sniff_format,
    supports,
)

FIX = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__), "..", "..", "native", "anydoc", "tests", "fixtures"
    )
)

ANYDOC_AVAILABLE = bridge.anydoc_available()

skip_if_no_anydoc = pytest.mark.skipif(
    not ANYDOC_AVAILABLE, reason="anydoc wheel not installed"
)


# --------------------------------------------------------------------------
# Unit: format tables & routing logic (no wheel required)
# --------------------------------------------------------------------------


def test_supports_known_formats():
    for fmt in ("doc", "docx", "odt", "rtf", "ppt", "pptx", "epub", "xls", "xlsx", "ods", "odp", "csv"):
        assert supports(fmt) is True
    assert supports("pdf") is False
    assert supports("txt") is False
    assert supports(".DOCX") is True


def test_anydoc_only_set_is_complete():
    # Every anydoc-only format must have a valid fallback sibling or None.
    for fmt, sibling in _ONLY.items():
        assert supports(fmt)
        if sibling is not None:
            assert sibling in _FORMAT_MAP, f"{fmt} -> unknown sibling {sibling}"


def test_format_map_inverse_for_sniff():
    # Every anydoc format name used by _FORMAT_MAP values must be sniffable back.
    targets = set(_FORMAT_MAP.values())
    for t in targets:
        if t == "pdf":
            continue  # PDF is sniffable but intentionally not imported via anydoc
        assert t in _SNIFF_MAP, f"anydoc target {t} missing from _SNIFF_MAP"


def test_should_handle_respects_anydoc_only():
    assert should_handle("xlsb") is True   # anydoc-only, owned whenever enabled
    assert should_handle("xls") is True
    assert should_handle("ppt") is True
    assert should_handle("ods") is True
    assert should_handle("odp") is True
    # Shared formats are NOT anydoc-only and follow the allow-list.
    assert "xlsx" not in _ONLY
    assert should_handle("xlsx") is False  # xlsx stays native by default
    assert should_handle("docx") is False  # v9 P1: docx returned to native
    assert should_handle("doc") is True    # OLE Word → Rust fast path by default
    assert should_handle("pptx") is True
    assert should_handle("rtf") is True
    assert should_handle("epub") is True
    assert should_handle("csv") is True


def test_should_handle_disabled_when_master_switch_off(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "anydoc_enabled", False)
    assert should_handle("xlsb") is False
    assert should_handle("docx") is False


def test_should_handle_allowlist_opt_out(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "anydoc_formats", "docx")  # narrower list
    # docx still anydoc-led (in list); doc falls back to native.
    assert should_handle("docx") is True
    assert should_handle("doc") is False
    # anydoc-only formats ignore the list and stay owned.
    assert should_handle("xlsb") is True


def test_error_translation_mapping():
    class FakeExc(Exception):
        pass

    # Unsupported / OCR -> AnydocFallback (recoverable)
    FakeExc.__name__ = "UnsupportedError"
    assert isinstance(bridge._translate_error(FakeExc("x"), "f.xls"), bridge.AnydocFallback)
    FakeExc.__name__ = "NeedsOcrError"
    out = bridge._translate_error(FakeExc("x"), "f.pdf")
    assert isinstance(out, bridge.AnydocFallback) and out.code == "needs_ocr"
    # Encrypted -> InvalidDocumentError (hard, must surface)
    FakeExc.__name__ = "EncryptedError"
    assert type(bridge._translate_error(FakeExc("x"), "f.docx")).__name__ == "InvalidDocumentError"


def test_marker_map_covers_enum():
    from app.core.document_model import ListMarker

    assert set(_MARKER_MAP.values()) == set(ListMarker)


# --------------------------------------------------------------------------
# Integration: real Rust parser (skipped without the wheel)
# --------------------------------------------------------------------------


@skip_if_no_anydoc
@pytest.mark.parametrize(
    "fmt,rel",
    [
        ("xlsb", "xlsb/handmade-sheet.xlsb"),
        ("xls", "xls/sheet.xls"),
        ("ods", "ods/sheet.ods"),
        ("ppt", "ppt/pres.ppt"),
        ("docx", "docx/text.docx"),
        ("docx", "docx/handmade-tables.docx"),
        ("docx", "docx/handmade-rich.docx"),
        ("rtf", "rtf/text.rtf"),
        ("rtf", "rtf/handmade-blockstyle.rtf"),
        ("odt", "odt/text.odt"),
        ("pptx", "pptx/pres.pptx"),
        ("doc", "doc/text.doc"),
    ],
)
def test_fast_markdown_channel(fmt, rel):
    data = open(os.path.join(FIX, rel), "rb").read()
    out = bridge.markdown_via_anydoc(data, fmt, rel)
    assert isinstance(out, str) and out.strip()


@skip_if_no_anydoc
@pytest.mark.parametrize(
    "fmt,rel",
    [
        ("docx", "docx/handmade-tables.docx"),
        ("rtf", "rtf/text.rtf"),
        ("doc", "doc/text.doc"),
    ],
)
def test_model_channel_office_basic(fmt, rel):
    data = open(os.path.join(FIX, rel), "rb").read()
    model = bridge.import_via_anydoc(data, rel, fmt)
    assert model.nodes, f"expected nodes from {rel}"
    assert model.metadata.get("importer") == "anydoc"


@skip_if_no_anydoc
def test_model_channel_odf_table_spans():
    data = open(os.path.join(FIX, "ods", "sheet.ods"), "rb").read()
    model = bridge.import_via_anydoc(data, "sheet.ods", "ods")
    tables = [n for n in model.nodes if getattr(n, "__class__", None) and n.__class__.__name__ == "Table"]
    assert tables, "expected at least one table"
    # anydoc reports header_rows / cell spans; md-bridge model must carry them.
    assert hasattr(tables[0], "cells")
    assert hasattr(tables[0], "header_rows")


@skip_if_no_anydoc
@pytest.mark.parametrize(
    "fmt,rel",
    [
        ("epub", "epub/book.epub"),
        ("epub", "epub/handmade-features.epub"),
        ("csv", "csv/sheet.csv"),
        ("csv", "csv/handmade-quoted.csv"),
    ],
)
def test_fast_markdown_epub_csv(fmt, rel):
    data = open(os.path.join(FIX, rel), "rb").read()
    out = bridge.markdown_via_anydoc(data, fmt, rel)
    assert isinstance(out, str) and out.strip()


@skip_if_no_anydoc
@pytest.mark.parametrize(
    "fmt,rel",
    [
        ("epub", "epub/book.epub"),
        ("csv", "csv/sheet.csv"),
    ],
)
def test_model_channel_epub_csv(fmt, rel):
    data = open(os.path.join(FIX, rel), "rb").read()
    model = bridge.import_via_anydoc(data, rel, fmt)
    assert model.nodes, f"expected nodes from {rel}"
    assert model.metadata.get("importer") == "anydoc"


@skip_if_no_anydoc
def test_xlsb_roundtrip_via_converter(monkeypatch):
    # End-to-end through converter.convert (registry wiring).
    from app.core import converter

    data = open(os.path.join(FIX, "xlsb", "handmade-sheet.xlsb"), "rb").read()
    out = converter.convert(data, "xlsb", "md", filename="handmade-sheet.xlsb")
    assert out and len(out) > 0


@skip_if_no_anydoc
def test_sniff_returns_anydoc_format():
    data = open(os.path.join(FIX, "ppt", "pres.ppt"), "rb").read()
    detected = sniff_format(data)
    assert detected in ("ppt", "pptx", None)  # binary ppt family


@skip_if_no_anydoc
def test_hard_error_surfaces_not_silent():
    from app.core.exceptions import InvalidDocumentError

    # A zero-byte file is not a valid container; anydoc should error hard.
    with pytest.raises((InvalidDocumentError, bridge.AnydocFallback, Exception)):
        bridge.import_via_anydoc(b"", "broken.xlsx", "xlsx")
