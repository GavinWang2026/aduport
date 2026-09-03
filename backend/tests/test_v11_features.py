"""v0.11: DOCX OOXML enrich, autolink/footnotes, KaTeX HTML, MOBI text fallback."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from app.config import settings
from app.core.converter import convert
from app.core.document_model import Comment, DocumentModel, Paragraph
from app.exporters.azw3_exporter import export_azw3
from app.exporters.html_render import render_body_html
from app.importers.docx_ooxml import enrich_docx_model
from app.importers.md_importer import import_md
from app.importers.mobi_text import try_extract_mobi_model


def _minimal_docx_with_comments_header() -> bytes:
    """Build a tiny DOCX zip with comments.xml + header1.xml + theme."""
    ns_w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:document xmlns:w="{ns_w}"><w:body>'
        f"<w:p><w:r><w:t>Hello body</w:t></w:r></w:p>"
        f"</w:body></w:document>"
    )
    comments = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:comments xmlns:w="{ns_w}">'
        f'<w:comment w:id="0" w:author="alice">'
        f"<w:p><w:r><w:t>fix me</w:t></w:r></w:p>"
        f"</w:comment></w:comments>"
    )
    header = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:hdr xmlns:w="{ns_w}">'
        f"<w:p><w:r><w:t>Acme Header</w:t></w:r></w:p>"
        f"</w:hdr>"
    )
    theme = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme">'
        "<a:themeElements></a:themeElements></a:theme>"
    )
    ct = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    buf = BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", ct)
        zf.writestr("word/document.xml", document)
        zf.writestr("word/comments.xml", comments)
        zf.writestr("word/header1.xml", header)
        zf.writestr("word/theme/theme1.xml", theme)
    return buf.getvalue()


def test_docx_ooxml_enrich_comments_header_theme():
    raw = _minimal_docx_with_comments_header()
    model = DocumentModel(title="t", nodes=[Paragraph(text="Hello body")])
    enrich_docx_model(model, raw)
    assert model.metadata.get("docx_theme") == "Office Theme"
    assert "Acme Header" in (model.metadata.get("docx_header") or "")
    assert any(isinstance(n, Comment) and n.text == "fix me" for n in model.nodes)
    # Header stays in metadata only — must not pollute body (P1 #6)
    assert not any(
        isinstance(n, Paragraph) and "页眉" in n.plain_text() for n in model.nodes
    )


def test_md_autolink_and_footnote_crossref():
    src = b"See https://example.com/docs and note[^1].\n\n[^1]: the note\n"
    model = import_md(src)
    paras = [n for n in model.nodes if isinstance(n, Paragraph)]
    assert paras
    rich = paras[0].text if hasattr(paras[0].text, "marks") else None
    assert rich is not None
    hrefs = [m.href for m in rich.marks if m.href]
    assert any(h and h.startswith("https://example.com") for h in hrefs)
    assert any(h == "#fn-1" for h in hrefs)
    html = render_body_html(model)
    assert 'id="fn-1"' in html or "id='fn-1'" in html
    assert "fnref-1" in html


def test_html_katex_math_and_table_header_flag(monkeypatch):
    src = b"# T\n\n$$\nx^2\n$$\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    out = convert(src, "md", "html")
    text = out.decode()
    assert "katex" in text.lower()
    assert "\\[" in text or "math-display" in text
    monkeypatch.setattr(settings, "table_first_row_header", False)
    body = render_body_html(import_md(b"| a | b |\n|---|---|\n| 1 | 2 |\n"))
    assert "<th>" not in body
    assert "<td>" in body


def test_mobi_text_fallback_from_azw3():
    model = DocumentModel(title="K", nodes=[Paragraph(text="Kindle hello world")])
    data = export_azw3(model)
    extracted = try_extract_mobi_model(data, title="K")
    assert extracted is not None
    blob = " ".join(n.plain_text() for n in extracted.nodes if hasattr(n, "plain_text"))
    assert "Kindle" in blob or "hello" in blob
