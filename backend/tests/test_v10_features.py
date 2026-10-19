"""v0.10: AZW3/KF8, Word comments.xml, streaming convert."""

from __future__ import annotations

import zipfile
from io import BytesIO

from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.document_model import Comment, DocumentModel, Heading, Image, Paragraph
from aduport.core.stream_convert import can_stream_convert, convert_streaming
from aduport.exporters.azw3_exporter import export_azw3
from aduport.exporters.docx_exporter import export_docx


def test_azw3_export_with_image():
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    model = DocumentModel(
        title="Rich",
        nodes=[
            Heading(level=1, text="Hi"),
            Paragraph(text="body"),
            Image(data=png, alt_text="dot", mime_type="image/png"),
        ],
    )
    data = export_azw3(model)
    assert b"BOOKMOBI" in data[:128] or (data[60:64] == b"BOOK" and data[64:68] == b"MOBI")
    assert b"EXTH" in data
    assert b"recindex" in data or png[:4] in data


def test_md_to_azw3_and_kf8():
    src = b"# T\n\nHello Kindle.\n"
    a = convert(src, "md", "azw3")
    k = convert(src, "md", "kf8")
    assert len(a) > 100 and len(k) > 100
    assert b"MOBI" in a and b"MOBI" in k


def test_word_real_comments_xml():
    model = DocumentModel(
        title="Doc",
        nodes=[
            Heading(level=1, text="Section"),
            Paragraph(text="body"),
            Comment(author="bob", text="needs rewrite", anchor="Section"),
        ],
    )
    data = export_docx(model)
    with zipfile.ZipFile(BytesIO(data)) as zf:
        assert "word/comments.xml" in zf.namelist()
        comments = zf.read("word/comments.xml").decode("utf-8")
        doc = zf.read("word/document.xml").decode("utf-8")
    assert "needs rewrite" in comments
    assert "bob" in comments
    assert "commentRangeStart" in doc
    assert "commentReference" in doc


def test_stream_convert_chunks(monkeypatch):
    monkeypatch.setattr(settings, "stream_convert", True)
    monkeypatch.setattr(settings, "stream_chunk_chars", 80)
    monkeypatch.setattr(settings, "stream_min_bytes", 10)
    assert can_stream_convert("md", "md")
    # Build multi-paragraph markdown larger than chunk
    parts = [f"## H{i}\n\nparagraph {i} " + ("x" * 40) + "\n" for i in range(8)]
    src = "\n".join(parts).encode()
    out = convert_streaming(src, "md", "md")
    assert b"## H0" in out and b"## H7" in out
    # Through convert() path
    out2 = convert(src, "md", "txt")
    assert b"paragraph 0" in out2
