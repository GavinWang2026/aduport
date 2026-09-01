"""v0.15: ODT/RTF/DOC + JSON/XLSX + image compress + ebook samples + dir async."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from app.config import settings
from app.core.converter import (
    SUPPORTED_SOURCE_FORMATS,
    SUPPORTED_TARGET_FORMATS,
    convert,
    convert_directory,
    convert_directory_async,
)
from app.core.document_model import DocumentModel, Heading, Image, Paragraph, Table
from app.core.image_ops import maybe_compress_images
from app.exporters.azw3_exporter import export_azw3, export_kf8
from app.exporters.mobi_exporter import export_mobi
from app.exporters.odt_exporter import export_odt
from app.exporters.xlsx_exporter import import_xlsx
from app.importers.mobi_text import try_extract_mobi_model
from app.importers.odt_text import try_extract_odt_model


def test_formats_v15_registered():
    for fmt in ("odt", "rtf", "doc", "json", "xlsx", "csv"):
        assert fmt in SUPPORTED_SOURCE_FORMATS
    for fmt in ("odt", "json", "xlsx"):
        assert fmt in SUPPORTED_TARGET_FORMATS


def test_odt_roundtrip():
    model = DocumentModel(
        title="T",
        nodes=[
            Heading(level=1, text="Hello"),
            Paragraph(text="World"),
            Table(rows=[["a", "b"], ["1", "2"]]),
        ],
    )
    data = export_odt(model)
    assert data.startswith(b"PK")
    back = try_extract_odt_model(data, title="T")
    assert back is not None
    texts = " ".join(
        getattr(n, "plain_text", lambda: "")() if hasattr(n, "plain_text") else ""
        for n in back.nodes
    )
    assert "Hello" in texts and "World" in texts
    out = convert(data, "odt", "md")
    assert b"Hello" in out


def test_rtf_import():
    rtf = br"{\rtf1\ansi\deff0 {\fonttbl{\f0 Times;}}\f0\fs24 Hello RTF\par World\par}"
    out = convert(rtf, "rtf", "md").decode()
    assert "Hello" in out and "World" in out


def test_json_xlsx_csv_table_io():
    src = b"# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    j = convert(src, "md", "json").decode()
    assert '"tables"' in j and "a" in j
    back = convert(j.encode(), "json", "md").decode()
    assert "a" in back and "1" in back

    x = convert(src, "md", "xlsx")
    assert x.startswith(b"PK")
    model = import_xlsx(x, "t.xlsx")
    assert any(isinstance(n, Table) for n in model.nodes)
    assert convert(x, "xlsx", "csv").decode().startswith("a")

    c = convert(src, "md", "csv")
    assert convert(c, "csv", "json").decode()


def test_doc_rejects_non_ole():
    with pytest.raises(Exception):
        convert(b"not a doc", "doc", "md")


def test_image_compress_optional(monkeypatch):
    # 1x1 PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    model = DocumentModel(nodes=[Image(data=png, mime_type="image/png")])
    monkeypatch.setattr(settings, "image_compress", True)
    out = maybe_compress_images(model)
    assert isinstance(out.nodes[0], Image)


def test_ebook_sample_regression():
    """Minimal real-ish mobi/azw3/kf8 samples must round-extract."""
    model = DocumentModel(
        title="EbookSample",
        nodes=[Heading(level=1, text="Chapter One"), Paragraph(text="Hello Kindle world.")],
    )
    for name, blob in (
        ("mobi", export_mobi(model)),
        ("azw3", export_azw3(model)),
        ("kf8", export_kf8(model)),
    ):
        assert len(blob) > 64, name
        extracted = try_extract_mobi_model(blob, title=name)
        assert extracted is not None, name
        blob_txt = " ".join(n.plain_text() for n in extracted.nodes if hasattr(n, "plain_text"))
        assert "Hello" in blob_txt or "Chapter" in blob_txt or "Kindle" in blob_txt, name


def test_convert_directory_async_pool(tmp_path: Path):
    src_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    src_dir.mkdir()
    (src_dir / "a.md").write_bytes(b"# A\n\nhi\n")
    (src_dir / "b.md").write_bytes(b"# B\n\nho\n")
    progress = []

    def cb(done, total, item):
        progress.append((done, total, item.success))

    result = convert_directory(
        src_dir, out_dir, "md", "txt", workers=2, progress_cb=cb
    )
    assert result.succeeded == 2
    assert (out_dir / "a.txt").exists()
    assert progress

    cancel = threading.Event()
    cancel.set()
    cancelled = convert_directory(src_dir, out_dir, "md", "html", cancel_event=cancel)
    assert cancelled.total == 2

    ids = convert_directory_async(src_dir, "md", "txt")
    assert len(ids) == 2
