"""Document title source: in-document heading wins over the filename stem."""

from __future__ import annotations

from aduport.core.document_model import DocumentModel, Heading

# Importers historically stuffed these in when the file had no real title.
_FALLBACK_TITLES = frozenset(
    {"", "input", "document", "docx", "odt", "untitled", "notebook", "t"}
)

_CHROME = frozenset(
    {
        "page_setup",
        "header_footer",
        "run_style",
        "numbering",
        "section",
        "field_code",
        "bookmark",
    }
)


def _stem(filename: str) -> str:
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        name = name.rsplit(".", 1)[0]
    return name.strip()


def is_placeholder_title(value: str) -> bool:
    """Filename stems and stock labels must not become an extra body heading."""
    return (value or "").strip().casefold() in _FALLBACK_TITLES


def _is_fallback(value: str, stem: str) -> bool:
    text = (value or "").strip()
    if not text:
        return True
    low = text.casefold()
    if low in _FALLBACK_TITLES:
        return True
    if stem and low == stem.casefold():
        return True
    return False


def first_heading_text(nodes: list) -> str:
    for node in nodes:
        if isinstance(node, Heading) and int(getattr(node, "level", 1) or 1) <= 1:
            text = node.plain_text().strip()
            if text:
                return text
    return ""


def resolve_title(explicit: str, nodes: list, *, filename: str = "") -> str:
    """Prefer a real document title, then an in-body H1, then the filename stem."""
    stem = _stem(filename)
    explicit = (explicit or "").strip()
    headings = []
    for node in nodes:
        if isinstance(node, Heading) and int(getattr(node, "level", 1) or 1) <= 1:
            text = node.plain_text().strip()
            if text:
                headings.append(text)
    if not _is_fallback(explicit, stem):
        return explicit
    for text in headings:
        if not _is_fallback(text, stem):
            return text
    # A filename stem is metadata, not a body heading (v18 B5).
    return ""


def title_repeats_leading_heading(model: DocumentModel) -> bool:
    """True when exporters would print ``model.title`` and then the same H1."""
    title = (model.title or "").strip()
    if not title:
        return False
    for node in model.nodes:
        kind = str(getattr(node, "type", "") or "")
        if kind in _CHROME:
            continue
        if isinstance(node, Heading) and int(getattr(node, "level", 1) or 1) <= 1:
            return node.plain_text().strip() == title
        return False
    return False
