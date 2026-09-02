"""PDF / PPTX export: Comment nodes with anchor metadata."""

from __future__ import annotations

from io import BytesIO
import zipfile

from app.core.document_model import Comment, DocumentModel, Heading, Paragraph
from app.exporters.pdf_simple import export_pdf_simple
from app.exporters.pptx_exporter import export_pptx


def test_pdf_simple_includes_comment():
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="Title"),
            Paragraph(text="body"),
            Comment(author="alice", text="fix typo", anchor="Title"),
        ]
    )
    pdf = export_pdf_simple(model)
    assert pdf.startswith(b"%PDF")
    assert b"alice" in pdf or b"fix typo" in pdf


def test_pptx_comments_slide_with_anchor():
    model = DocumentModel(
        title="Deck",
        nodes=[
            Heading(level=1, text="Overview"),
            Comment(author="bob", text="needs cite", anchor="Overview"),
        ],
    )
    data = export_pptx(model)
    assert data[:2] == b"PK"
    with zipfile.ZipFile(BytesIO(data)) as zf:
        slide_xml = [
            zf.read(n).decode("utf-8", errors="replace")
            for n in zf.namelist()
            if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        ]
    blob = "\n".join(slide_xml)
    assert "Review comments" in blob or "needs cite" in blob
    assert "bob" in blob
