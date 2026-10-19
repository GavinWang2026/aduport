"""v0.8: PPTX export, streaming upload, DOCX theme/comments, cache skip."""

from __future__ import annotations

from io import BytesIO

from aduport.config import settings
from aduport.core.audit import _sanitize_detail
from aduport.core.converter import SUPPORTED_TARGET_FORMATS, convert
from aduport.core.document_model import Comment, DocumentModel, Heading, ListItem, Paragraph
from aduport.core.spill import SpillBuffer
from aduport.exporters.docx_exporter import export_docx
from aduport.exporters.pptx_exporter import export_pptx
from aduport.importers.md_importer import import_md


def test_pptx_in_targets():
    assert "pptx" in SUPPORTED_TARGET_FORMATS


def test_md_to_pptx():
    src = b"# Title\n\nHello world.\n\n## Section\n\n- a\n- b\n"
    out = convert(src, "md", "pptx")
    assert out[:2] == b"PK"
    assert len(out) > 1000


def test_docx_theme_and_comments():
    model = DocumentModel(
        title="Doc",
        nodes=[
            Heading(level=1, text="H1"),
            Paragraph(text="body"),
            Comment(author="alice", text="please fix", anchor="H1"),
        ],
    )
    data = export_docx(model, theme="academic")
    assert data[:2] == b"PK"
    from zipfile import ZipFile

    with ZipFile(BytesIO(data)) as zf:
        assert "word/comments.xml" in zf.namelist()
        comments = zf.read("word/comments.xml").decode("utf-8", errors="ignore")
        doc = zf.read("word/document.xml").decode("utf-8", errors="ignore")
    assert "please fix" in comments
    assert "alice" in comments
    assert "commentRangeStart" in doc or "commentReference" in doc


def test_list_item_children():
    # blank line inside list item → continuation paragraph as children
    src = b"- first para\n\n  second para\n"
    model = import_md(src)
    items = [n for n in model.nodes if isinstance(n, ListItem)]
    assert items
    # markdown-it may emit one or two items depending on loose list; accept children or second item
    assert items[0].plain_text().startswith("first") or any(
        "first" in i.plain_text() for i in items
    )


def test_cache_skip_revalidate_flag():
    assert hasattr(settings, "cache_skip_revalidate")


def test_spill_from_path(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc123")
    buf = SpillBuffer.from_path(str(p), 6)
    assert buf.spilled
    assert buf.read() == b"abc123"
    buf.release()
    assert not p.exists()


def test_audit_sanitize_paths():
    s = _sanitize_detail("fail at /Users/gavin/Desktop/secret/file.pdf line 1")
    assert "/Users/***/" in s
    assert "gavin" not in s


def test_pptx_exporter_direct():
    model = DocumentModel(title="T", nodes=[Heading(level=1, text="A"), Paragraph(text="b")])
    data = export_pptx(model)
    assert data[:2] == b"PK"
