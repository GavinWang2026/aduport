"""HTML / ebook export: Comment nodes via shared html_render."""

from __future__ import annotations

from io import BytesIO
import zipfile

from aduport.core.document_model import Comment, DocumentModel, Heading, Paragraph
from aduport.exporters.azw3_exporter import export_azw3
from aduport.exporters.epub_exporter import export_epub
from aduport.exporters.html_render import render_body_html
from aduport.exporters.mobi_exporter import export_mobi


def test_html_render_includes_comment():
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="Title"),
            Paragraph(text="body"),
            Comment(author="alice", text="please fix", anchor="Title"),
        ]
    )
    html = render_body_html(model)
    assert "class='comment'" in html
    assert "alice" in html
    assert "please fix" in html
    assert "Title" in html


def test_epub_and_mobi_include_comment():
    model = DocumentModel(
        title="Book",
        nodes=[
            Paragraph(text="chapter"),
            Comment(author="bob", text="review note"),
        ],
    )
    epub = export_epub(model)
    with zipfile.ZipFile(BytesIO(epub)) as zf:
        chapter = zf.read("EPUB/chapter.xhtml").decode("utf-8")
    assert "review note" in chapter and "bob" in chapter

    mobi = export_mobi(model)
    assert b"review note" in mobi and b"bob" in mobi

    azw3 = export_azw3(model)
    assert b"review note" in azw3 and b"bob" in azw3
