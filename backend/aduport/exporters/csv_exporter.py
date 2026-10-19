"""Export DocumentModel tables (and optional flat rows) to CSV."""

from __future__ import annotations

import csv
from io import StringIO

from aduport.core.document_model import (
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
from aduport.exporters.rich_visible import render_rich_visible

# OWASP CSV injection prefixes (Excel / Sheets / LibreOffice) — P2-7: = + @ only
_FORMULA_PREFIXES = ("=", "+", "@")


def _neutralize_csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    if text and text[0] in _FORMULA_PREFIXES:
        return "'" + text
    return text


def _content_rows(model: DocumentModel) -> list[list[str]]:
    """Non-table body as single-column markdown-visible rows.

    A blank row is a blank line. Without it, a paragraph after a list is
    swallowed into the list item, and two sentences become one block.
    """
    rows: list[list[str]] = []
    previous: str | None = None

    def separate(kind: str) -> None:
        nonlocal previous
        if previous is not None and rows and rows[-1] != [""]:
            if kind != previous or kind in {"Paragraph", "Heading", "Blockquote"}:
                rows.append([""])
        previous = kind

    for node in model.nodes:
        if isinstance(node, Table):
            continue
        if isinstance(node, Heading):
            separate("Heading")
            rows.append([_neutralize_csv_cell("#" * node.level + " " + node.plain_text())])
        elif isinstance(node, Paragraph):
            from aduport.importers.image_importer import ocr_notice_for_placeholder

            separate("Paragraph")
            rows.append(
                [
                    _neutralize_csv_cell(
                        ocr_notice_for_placeholder(render_rich_visible(node.text))
                    )
                ]
            )
        elif isinstance(node, ListItem):
            separate("ListItem")
            indent = "  " * max(0, int(node.level or 0))
            if node.checked is not None:
                mark = "[x]" if node.checked else "[ ]"
            else:
                mark = "1." if node.ordered else "-"
            rows.append(
                [_neutralize_csv_cell(f"{indent}{mark} {render_rich_visible(node.text)}")]
            )
        elif isinstance(node, Blockquote):
            separate("Blockquote")
            for p in node.paragraphs:
                for line in render_rich_visible(p).splitlines() or [""]:
                    rows.append([_neutralize_csv_cell(f"> {line}")])
        elif isinstance(node, CodeBlock):
            separate("CodeBlock")
            rows.append([_neutralize_csv_cell(f"```{node.lang or ''}")])
            for line in (node.content or "").splitlines() or [""]:
                rows.append([_neutralize_csv_cell(line)])
            rows.append([_neutralize_csv_cell("```")])
        elif isinstance(node, MathBlock):
            separate("MathBlock")
            rows.append([_neutralize_csv_cell(f"$$ {node.content} $$")])
        elif isinstance(node, HorizontalRule):
            separate("HorizontalRule")
            rows.append([_neutralize_csv_cell("---")])
        elif isinstance(node, Footnote):
            separate("Footnote")
            rows.append([_neutralize_csv_cell(f"[^{node.id}]: {node.text}")])
        elif isinstance(node, Image):
            separate("Image")
            alt = (node.alt_text or "").strip() or "image"
            rows.append([_neutralize_csv_cell(f"[Image: {alt}]")])
        elif isinstance(node, HtmlBlock):
            import re

            separate("HtmlBlock")
            text = re.sub(r"<[^>]+>", "", node.content or "").strip()
            if text:
                rows.append([_neutralize_csv_cell(text)])
        else:
            separate(type(node).__name__)
            plain = getattr(node, "plain_text", None)
            if callable(plain):
                text = plain()
                if text:
                    rows.append([_neutralize_csv_cell(text)])
    return rows


_CONTENT_MARKER = "_CONTENT"
# First row after the marker: human-visible label in Excel; Markdown heading on re-import.
_CONTENT_BANNER = "# [AduPort] Markdown 正文区（非表格列；可用 csv_table_only 关闭）"


def _emit_content_section(writer: csv.writer, content: list[list[str]], model: DocumentModel) -> None:
    from aduport.exporters.export_warnings import warn_export

    writer.writerow([_CONTENT_MARKER])
    writer.writerow([_CONTENT_BANNER])
    for row in content:
        writer.writerow(row)
    warn_export(
        model,
        "csv: 正文写在 _CONTENT 段；Excel 打开会看到 Markdown 源码列。"
        "需要纯表格时设 csv_table_only=true",
    )


def export_csv(model: DocumentModel, *, table_only: bool | None = None) -> str:
    """
    Export Table nodes as CSV blocks. Mixed documents also emit a ``_CONTENT``
    section so headings/lists/rich text remain searchable (not table-only 0%).

    ``table_only=True`` drops the Markdown body section (no round-trip of prose).
    """
    from aduport.core.export_flags import export_flag

    if table_only is None:
        table_only = bool(export_flag("csv_table_only", False))

    buf = StringIO()
    writer = csv.writer(buf)
    tables = [n for n in model.nodes if isinstance(n, Table)]
    prose = _content_rows(model)
    content = [] if table_only else prose

    if tables:
        for ti, table in enumerate(tables):
            if ti:
                buf.write("\n")
            for row in table.rows:
                writer.writerow([_neutralize_csv_cell(c) for c in row])
        if content:
            buf.write("\n")
            _emit_content_section(writer, content, model)
        elif table_only and prose:
            from aduport.exporters.export_warnings import warn_export

            warn_export(model, "csv: csv_table_only=true，已省略 Markdown 正文段")
        return buf.getvalue()

    if content:
        # Prose-only files have no grid. The marker selects the native
        # importer, which reads the column as Markdown instead of one table.
        _emit_content_section(writer, content, model)
        return buf.getvalue()

    if table_only and not tables:
        from aduport.exporters.export_warnings import warn_export

        warn_export(model, "csv: csv_table_only=true 且文档无表格，导出为空表")
        writer.writerow([""])
        return buf.getvalue()

    # Empty document fallback
    writer.writerow([""])
    return buf.getvalue()
