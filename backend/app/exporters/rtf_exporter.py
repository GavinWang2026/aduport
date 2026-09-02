"""Minimal RTF exporter (plain text + headings/lists/tables)."""

from __future__ import annotations

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    ListItem,
    Paragraph,
    PivotTable,
    Revision,
    Table,
    TextBox,
    ensure_rich_text,
)


def _rtf_escape(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if ch in ("\\", "{", "}"):
            out.append("\\" + ch)
        elif code < 128:
            out.append(ch)
        else:
            # Unicode RTF: \uN?
            if code > 32767:
                code -= 65536
            out.append(f"\\u{code}?")
    return "".join(out)


def export_rtf(model: DocumentModel) -> bytes:
    parts: list[str] = [
        r"{\rtf1\ansi\deff0",
        r"{\fonttbl{\f0 Times New Roman;}}",
        r"\f0\fs24",
    ]
    if model.title:
        parts.append(r"\b " + _rtf_escape(model.title) + r"\b0\par")

    for node in model.nodes:
        if isinstance(node, Heading):
            size = max(28, 48 - 4 * max(1, min(6, node.level)))
            parts.append(rf"\fs{size}\b {_rtf_escape(node.plain_text())}\b0\fs24\par")
        elif isinstance(node, Paragraph):
            parts.append(_rtf_escape(node.plain_text()) + r"\par")
        elif isinstance(node, ListItem):
            bullet = r"\bullet " if not node.ordered else "1. "
            parts.append(bullet + _rtf_escape(node.plain_text()) + r"\par")
        elif isinstance(node, Table):
            from app.exporters.table_span import (
                render_table_rtf,
                table_flat_matrix,
                table_has_span_cells,
            )

            if table_has_span_cells(node):
                parts.append(render_table_rtf(node))
            else:
                matrix = table_flat_matrix(node)
                for row in matrix:
                    parts.append(_rtf_escape(" | ".join(row)) + r"\par")
        elif isinstance(node, HorizontalRule):
            parts.append(r"\par --------------------\par")
        elif isinstance(node, CodeBlock):
            parts.append(_rtf_escape(node.content) + r"\par")
        elif isinstance(node, Blockquote):
            for p in node.paragraphs:
                parts.append(_rtf_escape("「" + ensure_rich_text(p).text + "」") + r"\par")
        elif isinstance(node, Comment):
            anchor = f" ({node.anchor})" if node.anchor else ""
            parts.append(
                _rtf_escape(f"[Comment {node.author or 'reviewer'}{anchor}] {node.text}")
                + r"\par"
            )
        elif isinstance(node, Footnote):
            parts.append(_rtf_escape(f"[^{node.id}] {node.text}") + r"\par")
        elif isinstance(node, Revision):
            tag = "ins" if node.change == "insert" else "del"
            parts.append(_rtf_escape(f"[{tag} {node.author}] {node.text}") + r"\par")
        elif isinstance(node, ContentControl):
            label = node.alias or node.tag or "control"
            parts.append(_rtf_escape(f"[{label}] {node.text}") + r"\par")
        elif isinstance(node, TextBox):
            src = f" ({node.source})" if node.source else ""
            parts.append(_rtf_escape(f"[TextBox{src}] {node.text}") + r"\par")
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            parts.append(_rtf_escape(f"[PivotTable {node.name}] {fields}") + r"\par")
        else:
            text = getattr(node, "plain_text", None)
            if callable(text):
                t = text()
                if t:
                    parts.append(_rtf_escape(str(t)) + r"\par")

    parts.append("}")
    return "\n".join(parts).encode("utf-8")
