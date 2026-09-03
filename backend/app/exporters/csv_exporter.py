"""Export DocumentModel tables (and optional flat rows) to CSV."""

from __future__ import annotations

import csv
from io import StringIO

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    ListItem,
    MathBlock,
    Paragraph,
    Table,
)
from app.exporters.rich_visible import render_rich_visible

# OWASP CSV injection prefixes (Excel / Sheets / LibreOffice)
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in _FORMULA_PREFIXES:
        return "'" + text
    return text


def _content_rows(model: DocumentModel) -> list[list[str]]:
    """Non-table body as single-column markdown-visible rows."""
    rows: list[list[str]] = []
    for node in model.nodes:
        if isinstance(node, Table):
            continue
        if isinstance(node, Heading):
            rows.append([_neutralize_csv_cell("#" * node.level + " " + node.plain_text())])
        elif isinstance(node, Paragraph):
            rows.append([_neutralize_csv_cell(render_rich_visible(node.text))])
        elif isinstance(node, ListItem):
            indent = "  " * max(0, int(node.level or 0))
            if node.checked is not None:
                mark = "[x]" if node.checked else "[ ]"
            else:
                mark = "1." if node.ordered else "-"
            rows.append(
                [_neutralize_csv_cell(f"{indent}{mark} {render_rich_visible(node.text)}")]
            )
        elif isinstance(node, Blockquote):
            for p in node.paragraphs:
                for line in render_rich_visible(p).splitlines() or [""]:
                    rows.append([_neutralize_csv_cell(f"> {line}")])
        elif isinstance(node, CodeBlock):
            rows.append([_neutralize_csv_cell(f"```{node.lang or ''}")])
            for line in (node.content or "").splitlines() or [""]:
                rows.append([_neutralize_csv_cell(line)])
            rows.append([_neutralize_csv_cell("```")])
        elif isinstance(node, MathBlock):
            rows.append([_neutralize_csv_cell(f"$$ {node.content} $$")])
        elif isinstance(node, HorizontalRule):
            rows.append([_neutralize_csv_cell("---")])
        elif isinstance(node, Footnote):
            rows.append([_neutralize_csv_cell(f"[^{node.id}]: {node.text}")])
        elif isinstance(node, Image):
            alt = (node.alt_text or "image").split("|", 1)[0]
            rows.append([_neutralize_csv_cell(f"[Image: {alt}]")])
        elif isinstance(node, HtmlBlock):
            import re

            text = re.sub(r"<[^>]+>", "", node.content or "").strip()
            if text:
                rows.append([_neutralize_csv_cell(text)])
        else:
            plain = getattr(node, "plain_text", None)
            if callable(plain):
                text = plain()
                if text:
                    rows.append([_neutralize_csv_cell(text)])
    return rows


def export_csv(model: DocumentModel) -> str:
    """
    Export Table nodes as CSV blocks. Mixed documents also emit a ``_CONTENT``
    section so headings/lists/rich text remain searchable (not table-only 0%).
    """
    buf = StringIO()
    writer = csv.writer(buf)
    tables = [n for n in model.nodes if isinstance(n, Table)]
    content = _content_rows(model)

    if tables:
        for ti, table in enumerate(tables):
            if ti:
                buf.write("\n")
            for row in table.rows:
                writer.writerow([_neutralize_csv_cell(c) for c in row])
        if content:
            buf.write("\n")
            writer.writerow(["_CONTENT"])
            for row in content:
                writer.writerow(row)
        return buf.getvalue()

    if content:
        for row in content:
            writer.writerow(row)
        return buf.getvalue()

    # Empty document fallback
    writer.writerow([""])
    return buf.getvalue()
