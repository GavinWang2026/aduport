"""P1-1: DOCX theme / comments product switches."""

from __future__ import annotations

import zipfile
from io import BytesIO

from app.config import settings
from app.core.document_model import Comment, DocumentModel, Heading, Paragraph
from app.exporters.docx_exporter import export_docx


def test_docx_theme_academic_applies():
    model = DocumentModel(title="T", nodes=[Heading(level=1, text="H1"), Paragraph(text="p")])
    data = export_docx(model, theme="academic")
    assert data[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(data)) as zf:
        assert "word/document.xml" in zf.namelist()


def test_docx_export_comments_respects_setting(monkeypatch):
    model = DocumentModel(
        nodes=[
            Paragraph(text="body"),
            Comment(author="alice", text="note", anchor="body"),
        ]
    )
    monkeypatch.setattr(settings, "docx_export_comments", False)
    data_off = export_docx(model)
    with zipfile.ZipFile(BytesIO(data_off)) as zf:
        assert "word/comments.xml" not in zf.namelist()

    monkeypatch.setattr(settings, "docx_export_comments", True)
    data_on = export_docx(model)
    with zipfile.ZipFile(BytesIO(data_on)) as zf:
        assert "word/comments.xml" in zf.namelist()
