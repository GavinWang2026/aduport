"""Visible plain-text rendering of RichText for TXT/PDF/CSV/PPTX/XLSX fidelity."""

from __future__ import annotations

import re

from aduport.core.document_model import (
    CodeBlock,
    InlineMarkType,
    Paragraph,
    ensure_rich_text,
)


def render_rich_visible(value) -> str:
    """Render rich text with detectable bold/italic/code/link markers."""
    rich = ensure_rich_text(value)
    if not rich.marks:
        return rich.text

    boundaries = {0, len(rich.text)}
    for mark in rich.marks:
        boundaries.add(max(0, min(mark.start, len(rich.text))))
        boundaries.add(max(0, min(mark.end, len(rich.text))))
    points = sorted(boundaries)
    parts: list[str] = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        segment = rich.text[start:end]
        active = [m for m in rich.marks if m.start <= start and m.end >= end]
        types = {m.type for m in active}
        if InlineMarkType.LINK in types:
            href = next((m.href for m in active if m.type == InlineMarkType.LINK), "") or ""
            # Footnote refs are stored as links to ``#fn-id``. Writing them as
            # ``[[^id]](#fn-id)`` makes the next import see a hyperlink, not a note.
            if re.fullmatch(r"\[\^[^\]]+\]", segment) or href.startswith("#fn-"):
                if not re.fullmatch(r"\[\^[^\]]+\]", segment):
                    fid = href[4:] if href.startswith("#fn-") else segment
                    segment = f"[^{fid}]"
            else:
                segment = f"[{segment}]({href})"
        if InlineMarkType.BOLD in types:
            segment = f"**{segment}**"
        if InlineMarkType.ITALIC in types:
            segment = f"*{segment}*"
        if InlineMarkType.CODE in types:
            segment = f"`{segment}`"
        if InlineMarkType.STRIKETHROUGH in types:
            segment = f"~~{segment}~~"
        parts.append(segment)
    return "".join(parts)


def list_item_children(node) -> list[tuple[bool, str]]:
    """Flatten a ListItem's ``children`` into ``(is_code, text)`` pairs.

    v6/T1-#3: every exporter rendered only ``node.text`` and dropped the extra
    paragraphs / fenced code blocks hanging off a multi-paragraph list item.
    That silently deleted user content from txt/html/json/docx/pdf/latex/odt/
    rtf. Centralising the flatten here means one fix covers all exporters.

    Returns ``(True, code_body)`` for code blocks and ``(False, paragraph)``
    for prose, so callers can pick their own markup for each.
    """
    out: list[tuple[bool, str]] = []
    for child in getattr(node, "children", None) or []:
        if isinstance(child, CodeBlock):
            out.append((True, child.content or ""))
        elif isinstance(child, Paragraph):
            out.append((False, render_rich_visible(child.text)))
        else:
            text = getattr(child, "content", None) or getattr(child, "text", None)
            if text:
                out.append((False, render_rich_visible(text)))
    return out
