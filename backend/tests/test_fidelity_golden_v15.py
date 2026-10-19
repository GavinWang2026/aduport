"""v15 §4.2: structural golden fidelity (not substring fake-green)."""

from __future__ import annotations

from aduport.core.fidelity_golden import (
    compare_structure,
    fingerprint_model,
    golden_roundtrip_md,
)
from aduport.importers.md_importer import import_md
from aduport.importers.pdf_importer import import_pdf


_GOLDEN_MD = """# Golden Chapter

Intro with **bold** mark.

## Section

- item alpha
- item beta

| ColA | ColB |
| --- | --- |
| Jia | 1 |
| Yi | 2 |
""".encode("utf-8")


def test_golden_fingerprint_counts():
    model = import_md(_GOLDEN_MD)
    fp = fingerprint_model(model)
    assert fp.headings >= 2
    assert fp.tables >= 1
    assert fp.table_rows >= 2
    assert any("Golden" in t or "Chapter" in t for t in fp.tokens)


def test_golden_md_docx_roundtrip_structure():
    diff = golden_roundtrip_md(_GOLDEN_MD, via="docx", min_score=0.8)
    assert diff.ok, diff.issues
    assert diff.score >= 0.8


def test_golden_md_html_roundtrip_structure():
    diff = golden_roundtrip_md(_GOLDEN_MD, via="html", min_score=0.8)
    assert diff.ok, diff.issues


def test_compare_structure_detects_table_loss():
    a = fingerprint_model(import_md(_GOLDEN_MD))
    b = fingerprint_model(import_md(b"# Only heading\n\nplain\n"))
    diff = compare_structure(a, b)
    assert not diff.ok
    assert any("table" in i for i in diff.issues)


def test_pdf_auto_prefer_docling_flag(monkeypatch):
    """When prefer_docling is on and models ready, pipeline stamps docling; else text."""
    from aduport.config import settings
    from aduport.core.document_model import DocumentModel, Paragraph
    from aduport.importers import pdf_importer as pi

    monkeypatch.setattr(settings, "pdf_engine", "auto")
    monkeypatch.setattr(settings, "pdf_auto_prefer_docling", True)
    monkeypatch.setattr(settings, "pdf_text_min_chars", 8)
    monkeypatch.setattr(settings, "ocr_enabled", False)
    monkeypatch.setattr(pi, "docling_models_ready", lambda: True)

    called = {"docling": 0}

    def _fake_docling(data: bytes, filename: str = "") -> DocumentModel:
        called["docling"] += 1
        return DocumentModel(
            title="d",
            nodes=[Paragraph(text="docling path")],
            metadata={"importer": "docling"},
        )

    def _fake_text(data: bytes, filename: str = "") -> DocumentModel:
        return DocumentModel(
            title="t",
            nodes=[Paragraph(text="text path enough chars here")],
            metadata={"pdf_text_chars": 40},
        )

    monkeypatch.setattr(pi, "_import_with_docling", _fake_docling)
    monkeypatch.setattr(pi, "extract_pdf_text_model", _fake_text)
    model = pi.import_pdf(b"%PDF-1.4 fake", "x.pdf")
    assert called["docling"] == 1
    assert (model.metadata or {}).get("pdf_pipeline") == "docling"

    # Prefer off → text short-circuit
    monkeypatch.setattr(settings, "pdf_auto_prefer_docling", False)
    called["docling"] = 0
    model2 = pi.import_pdf(b"%PDF-1.4 fake", "y.pdf")
    assert called["docling"] == 0
    assert (model2.metadata or {}).get("pdf_pipeline") == "text"


def test_epub_css_collected_and_html_export_attaches():
    """EPUB package CSS lands in metadata and sanitized HTML export."""
    import io
    import zipfile

    from aduport.exporters.html_exporter import export_html
    from aduport.importers.epub_importer import import_epub

    opf = """<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:title>CSS Book</dc:title>
        <dc:identifier id="uid">css-1</dc:identifier>
      </metadata>
      <manifest>
        <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
        <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
        <item id="st" href="theme.css" media-type="text/css"/>
      </manifest>
      <spine toc="ncx"><itemref idref="c1"/></spine>
    </package>
    """
    ncx = """<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
    <navMap><navPoint id="n1"><navLabel><text>Ch</text></navLabel><content src="c1.xhtml"/></navPoint></navMap></ncx>"""
    xhtml = """<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">
    <head><title>Ch</title><style type="text/css">p { color: navy; }</style></head>
    <body><h1>Chapter One</h1><p>Hello theme.</p></body></html>"""
    css = "body { font-family: Georgia, serif; } h1 { color: #234; }"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles><rootfile full-path=\"OEBPS/content.opf\" "
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/toc.ncx", ncx)
        zf.writestr("OEBPS/c1.xhtml", xhtml)
        zf.writestr("OEBPS/theme.css", css)
    model = import_epub(buf.getvalue(), "css.epub")
    assert (model.metadata or {}).get("epub_css")
    assert "Georgia" in (model.metadata or {}).get("epub_css", "")
    html = export_html(model)
    assert "epub theme" in html
    assert "Georgia" in html
    assert "url(" not in html.lower() or "/*blocked*/" in html
