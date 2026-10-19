"""v0.12 breadth: fill pdf/pptx/epub/mobi source rows + PDF export fallback."""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from pptx import Presentation
from pptx.util import Inches

from aduport.config import settings
from aduport.core.converter import convert
from aduport.core.document_model import DocumentModel, Paragraph
from aduport.exporters.azw3_exporter import export_azw3
from aduport.exporters.pdf_simple import export_pdf_simple
from aduport.importers.epub_text import try_extract_epub_model
from aduport.importers.mobi_text import try_extract_mobi_model
from aduport.importers.pdf_text_fallback import extract_pdf_text_model
from aduport.importers.pptx_text import try_extract_pptx_model

TARGETS = ("md", "docx", "pdf", "html", "txt", "epub", "pptx", "mobi", "azw3", "kf8")


def _build_pdf(text: str = "Hello PDF Breadth Sample Text Layer Content") -> bytes:
    return export_pdf_simple(DocumentModel(title="PDF", nodes=[Paragraph(text=text)]))


def _build_pptx() -> bytes:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
    tf = box.text_frame
    tf.text = "Hello PPTX Title"
    p = tf.add_paragraph()
    p.text = "Breadth matrix body line."
    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


def _build_epub() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
</rootfiles></container>""",
        )
        zf.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:title>Breadth EPUB</dc:title><dc:identifier id="uid">b1</dc:identifier>
</metadata>
<manifest>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="c1" href="chap.xhtml" media-type="application/xhtml+xml"/>
</manifest>
<spine toc="ncx"><itemref idref="c1"/></spine>
</package>""",
        )
        zf.writestr(
            "OEBPS/toc.ncx",
            """<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<navMap><navPoint id="n1"><navLabel><text>Ch</text></navLabel>
<content src="chap.xhtml"/></navPoint></navMap></ncx>""",
        )
        zf.writestr(
            "OEBPS/chap.xhtml",
            """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ch</title></head>
<body><h1>Hello EPUB</h1><p>Breadth matrix body.</p></body></html>""",
        )
    return buf.getvalue()


def _build_mobi() -> bytes:
    return export_azw3(
        DocumentModel(title="Mobi", nodes=[Paragraph(text="Hello MOBI breadth")])
    )


@pytest.fixture
def force_light_importers(monkeypatch):
    """Skip docling so CI uses PalmDB / XHTML / slide-XML fallbacks."""
    from aduport.core.exceptions import InvalidDocumentError

    def _boom():
        raise InvalidDocumentError("force light path")

    monkeypatch.setattr("aduport.importers.epub_importer._require_docling", _boom)
    monkeypatch.setattr("aduport.importers.pptx_importer._require_docling", _boom)
    monkeypatch.setattr("aduport.importers.mobi_importer._require_docling", _boom)
    monkeypatch.setattr(settings, "pdf_engine", "text")
    monkeypatch.setattr(settings, "pdf_text_min_chars", 8)


def test_pdf_simple_export_and_roundtrip():
    data = convert(b"# Hi\n\nPDF fallback body.\n", "md", "pdf")
    assert data.startswith(b"%PDF")
    model = extract_pdf_text_model(data, "x.pdf")
    blob = " ".join(n.plain_text() for n in model.nodes if hasattr(n, "plain_text"))
    assert any(k in blob for k in ("PDF", "Hi", "fallback", "body", "Hi"))


def test_light_extractors_work():
    pdf = _build_pdf()
    assert extract_pdf_text_model(pdf, "t.pdf").nodes
    assert try_extract_pptx_model(_build_pptx(), title="p").nodes
    assert try_extract_epub_model(_build_epub(), title="e").nodes
    assert try_extract_mobi_model(_build_mobi(), title="m").nodes


@pytest.mark.parametrize(
    "src_fmt,builder",
    [
        ("pdf", _build_pdf),
        ("pptx", _build_pptx),
        ("epub", _build_epub),
        ("mobi", _build_mobi),
    ],
)
def test_source_row_to_all_targets(src_fmt, builder, force_light_importers):
    raw = builder()
    sizes = {}
    for tgt in TARGETS:
        out = convert(raw, src_fmt, tgt)
        assert out and len(out) > 20, f"{src_fmt}->{tgt} empty"
        sizes[tgt] = len(out)
    assert sizes["md"] > 10 and sizes["html"] > 10 and sizes["txt"] > 5
