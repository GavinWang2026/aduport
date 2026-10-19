"""Turn markdown-visible plain text back into AST nodes.

CSV, ODS, ODP, TXT, and the pdf_simple text layer already write headings,
lists, quotes, and fences as characters. Importers that only build paragraphs
or tables drop that structure on the way back to Markdown.
"""

from __future__ import annotations

import re

from aduport.core.document_model import Paragraph, RichText

_STRUCT_LINE = re.compile(
    r"^(#{1,6}\s+\S|>\s?|```|[-*+]\s+\S|\d+\.\s+\S|\|.*\||\[\^[^\]]+\]:)"
)
_INLINE_MARK = re.compile(r"\*\*|~~|`|(?<!\*)\*(?!\*)")


def parse_markdown_nodes(text: str) -> list:
    from aduport.importers.md_importer import import_md

    payload = text if text.endswith("\n") else text + "\n"
    return list(import_md(payload.encode("utf-8")).nodes)


def looks_structural(text: str) -> bool:
    """True when a line starts like a Markdown block we know how to restore."""
    hits = 0
    for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if _STRUCT_LINE.match(ln.strip()):
            hits += 1
            if hits >= 1:
                return True
    return False


def promote_inline(text: str) -> RichText:
    """Parse ``**bold**`` / ``*italic*`` / ``code`` into marks when present."""
    if not text or not _INLINE_MARK.search(text):
        return RichText(text=text)
    nodes = parse_markdown_nodes(text)
    if len(nodes) == 1 and isinstance(nodes[0], Paragraph):
        rich = nodes[0].text
        if isinstance(rich, RichText):
            return rich
        if isinstance(rich, str):
            return RichText(text=rich)
    return RichText(text=text)
