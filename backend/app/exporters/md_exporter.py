"""Export DocumentModel to Markdown."""

from __future__ import annotations

import base64
import re

from app.config import settings
from app.exporters.table_span import (
    render_table_html,
    table_flat_matrix,
    table_has_span_cells,
)
from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    MathBlock,
    PivotTable,
    Revision,
    Paragraph,
    RichText,
    Table,
    TextBox,
    ensure_rich_text,
)


def export_md(model: DocumentModel) -> str:
    lines: list[str] = []
    if model.title:
        lines.append(f"# {model.title}")
        lines.append("")

    for node in model.nodes:
        if isinstance(node, Heading):
            prefix = "#" * node.level
            lines.append(f"{prefix} {_render_rich(ensure_rich_text(node.text))}")
            lines.append("")
        elif isinstance(node, Paragraph):
            lines.append(_render_rich(ensure_rich_text(node.text)))
            lines.append("")
        elif isinstance(node, Blockquote):
            for para in node.paragraphs:
                rendered = _render_rich(ensure_rich_text(para))
                for line in rendered.split("\n"):
                    lines.append(f"> {line}")
                lines.append(">")
            if lines and lines[-1] == ">":
                lines.pop()
            lines.append("")
        elif isinstance(node, HorizontalRule):
            lines.append("---")
            lines.append("")
        elif isinstance(node, ListItem):
            indent = "  " * node.level
            if node.checked is not None:
                box = "[x]" if node.checked else "[ ]"
                marker = f"- {box}"
            else:
                marker = "1." if node.ordered else "-"
            lines.append(f"{indent}{marker} {_render_rich(ensure_rich_text(node.text))}")
            for child in node.children:
                # Continuation paragraphs indented under the item
                lines.append(f"{indent}  {_render_rich(ensure_rich_text(child.text))}")
        elif isinstance(node, Comment):
            anchor = f' "{node.anchor}"' if node.anchor else ""
            lines.append(f"<!-- comment:{node.author}{anchor} {node.text} -->")
            lines.append("")
        elif isinstance(node, Revision):
            if node.change == "delete":
                lines.append(f"~~{node.text}~~ <!-- del:{node.author} -->")
            else:
                lines.append(f"++{node.text}++ <!-- ins:{node.author} -->")
            lines.append("")
        elif isinstance(node, ContentControl):
            label = node.alias or node.tag or "control"
            lines.append(
                f"**[{label}]** {node.text} <!-- content_control:{node.control_type} -->"
            )
            lines.append("")
        elif isinstance(node, PivotTable):
            lines.append(f"### Pivot: {node.name or 'table'}")
            if node.location:
                lines.append(f"- location: {node.location}")
            if node.source_range:
                lines.append(f"- source: {node.source_range}")
            if node.row_fields:
                lines.append(f"- rows: {', '.join(node.row_fields)}")
            if node.col_fields:
                lines.append(f"- cols: {', '.join(node.col_fields)}")
            if node.data_fields:
                lines.append(f"- values: {', '.join(node.data_fields)}")
            lines.append("")
        elif isinstance(node, TextBox):
            src = f" ({node.source})" if node.source else ""
            lines.append(f"> **[文本框{src}]** {node.text}")
            lines.append("")
        elif isinstance(node, Table):
            lines.extend(_render_table(node))
            lines.append("")
        elif isinstance(node, Image):
            lines.append(_render_image(node))
            lines.append("")
        elif isinstance(node, MathBlock):
            if node.display:
                lines.append("$$")
                lines.append(node.content)
                lines.append("$$")
            else:
                lines.append(f"${node.content}$")
            lines.append("")
        elif isinstance(node, Footnote):
            lines.append(f"[^{node.id}]: {node.text}")
            lines.append("")
        elif isinstance(node, HtmlBlock):
            lines.append(node.content)
            lines.append("")
        elif isinstance(node, CodeBlock):
            lang = node.lang or ""
            lines.append(f"```{lang}")
            lines.append(node.content)
            lines.append("```")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_image(node: Image) -> str:
    if node.data:
        alt = node.alt_text.split("|", 1)[0] if "|" in node.alt_text else node.alt_text
        b64 = base64.b64encode(node.data).decode("ascii")
        return f"![{alt}](data:{node.mime_type};base64,{b64})"
    # Placeholder for remote / relative images: alt may encode "alt|url"
    if "|" in node.alt_text:
        alt, src = node.alt_text.split("|", 1)
        return f"![{alt}]({src})"
    return f"![{node.alt_text}]()"


def _render_rich(rich: RichText) -> str:
    if not rich.marks:
        return rich.text

    parts: list[str] = []
    for segment, active_marks in _split_by_marks(rich):
        types = {m.type for m in active_marks}
        if InlineMarkType.LINK in types:
            href = next((m.href for m in active_marks if m.type == InlineMarkType.LINK), "") or ""
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


def _split_by_marks(rich: RichText) -> list[tuple[str, list[InlineMark]]]:
    boundaries = {0, len(rich.text)}
    for mark in rich.marks:
        boundaries.add(max(0, min(mark.start, len(rich.text))))
        boundaries.add(max(0, min(mark.end, len(rich.text))))
    points = sorted(boundaries)
    segments: list[tuple[str, list[InlineMark]]] = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        active = [m for m in rich.marks if m.start <= start and m.end >= end]
        segments.append((rich.text[start:end], active))
    return segments


def _render_table(table: Table) -> list[str]:
    if table_has_span_cells(table):
        html_table = render_table_html(table)
        return ["", html_table, ""] if html_table else []

    matrix = table_flat_matrix(table)
    if not matrix:
        return []

    col_count = max((len(row) for row in matrix), default=0)
    if col_count == 0:
        return []

    widths = [3] * col_count
    for row in matrix:
        for i, cell in enumerate(row[:col_count]):
            widths[i] = max(widths[i], len(cell), 3)

    def fmt_row(cells: list[str]) -> str:
        padded = [cells[i].ljust(widths[i]) if i < len(cells) else "".ljust(widths[i]) for i in range(col_count)]
        return "| " + " | ".join(padded) + " |"

    separator = "| " + " | ".join("-" * widths[i] for i in range(col_count)) + " |"
    header_end = _table_md_header_rows(table, len(matrix))
    lines: list[str] = []

    if len(matrix) == 1:
        lines.append(fmt_row(matrix[0]))
        return lines

    for i in range(header_end):
        lines.append(fmt_row(matrix[i]))
    lines.append(separator)
    for row in matrix[header_end:]:
        lines.append(fmt_row(row))
    return lines


def _table_md_header_rows(table: Table, row_count: int) -> int:
    if table.header_rows > 0:
        return min(int(table.header_rows), row_count)
    return 1 if settings.table_first_row_header and row_count > 1 else 0
