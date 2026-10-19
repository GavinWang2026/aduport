"""Plain-text importer."""

from __future__ import annotations

from aduport.core.document_model import DocumentModel, Paragraph, RichText
from aduport.core.text_decode import decode_text_bytes


def import_txt(source_bytes: bytes, filename: str = "input.txt") -> DocumentModel:
    text = decode_text_bytes(source_bytes, filename=filename or "input.txt")
    stem = filename.rsplit(".", 1)[0] if filename else ""
    if not text.strip():
        return DocumentModel(title=stem, nodes=[])
    # export_txt writes setext headings, lists, quotes, and fences. Parsing
    # that text as Markdown restores them; plain prose stays paragraphs.
    from aduport.core.title_source import resolve_title
    from aduport.importers.md_importer import import_md

    model = import_md(text.encode("utf-8"))
    model.title = resolve_title(model.title, model.nodes, filename=filename or "input.txt")
    if not model.nodes:
        model.nodes = [Paragraph(text=RichText(text=text.strip()))]
    return model
