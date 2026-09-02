"""Golden sample: MOBI footnote export → import roundtrip (CI-safe, no native)."""

from __future__ import annotations

from app.core.converter import convert
from app.core.document_model import DocumentModel, Footnote, Paragraph
from app.exporters.mobi_exporter import export_mobi
from app.importers.mobi_text import try_extract_mobi_model


def _footnote_model() -> DocumentModel:
    return DocumentModel(
        title="FootnoteGolden",
        nodes=[
            Paragraph(text="Main body with a reference."),
            Footnote(id="1", text="This is the endnote text."),
        ],
    )


def test_golden_mobi_footnote_ast_roundtrip():
    """Export MOBI with footnotes section → extract Footnote AST nodes."""
    blob = export_mobi(_footnote_model())
    assert blob[:32].find(b"BOOK") >= 0 or len(blob) > 128
    model = try_extract_mobi_model(blob, title="FootnoteGolden")
    assert model is not None
    footnotes = [n for n in model.nodes if isinstance(n, Footnote)]
    assert footnotes, "expected Footnote nodes after MOBI import"
    assert any("endnote" in fn.text.lower() for fn in footnotes)


def test_golden_mobi_footnote_convert_md_roundtrip():
    """md → mobi → md preserves footnote body (pipeline golden)."""
    src = b"# Doc\n\nBody line.\n\n[^1]: Endnote golden sample.\n"
    mobi = convert(src, "md", "mobi", filename="golden.md")
    assert mobi and len(mobi) > 64
    back = convert(mobi, "mobi", "md", filename="golden.mobi").decode("utf-8", errors="replace")
    assert "Endnote golden" in back or "endnote" in back.lower()


def test_golden_mobi_footnote_html_section_in_payload():
    """Exported bytes must contain footnotes HTML section (regression guard)."""
    blob = export_mobi(_footnote_model())
    assert b"footnotes" in blob and b"endnote" in blob.lower()
