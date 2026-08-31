"""Export DocumentModel to plain text."""

from __future__ import annotations

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
    ensure_rich_text,
)


def export_txt(model: DocumentModel) -> str:
    lines: list[str] = []
    if model.title:
        lines.append(model.title)
        lines.append("=" * len(model.title))
        lines.append("")

    for node in model.nodes:
        if isinstance(node, Heading):
            text = node.plain_text()
            lines.append(text)
            lines.append(("-" if node.level > 1 else "=") * min(40, max(3, len(text))))
            lines.append("")
        elif isinstance(node, Paragraph):
            lines.append(ensure_rich_text(node.text).text)
            lines.append("")
        elif isinstance(node, Blockquote):
            for p in node.paragraphs:
                for line in ensure_rich_text(p).text.splitlines() or [""]:
                    lines.append(f"> {line}")
            lines.append("")
        elif isinstance(node, HorizontalRule):
            lines.append("-" * 40)
            lines.append("")
        elif isinstance(node, ListItem):
            indent = "  " * node.level
            if node.checked is not None:
                mark = "[x]" if node.checked else "[ ]"
            else:
                mark = "1." if node.ordered else "-"
            lines.append(f"{indent}{mark} {ensure_rich_text(node.text).text}")
        elif isinstance(node, Table):
            for row in node.rows:
                lines.append(" | ".join(str(c) for c in row))
            lines.append("")
        elif isinstance(node, Image):
            lines.append(f"[Image: {node.alt_text}]")
            lines.append("")
        elif isinstance(node, MathBlock):
            lines.append(node.content)
            lines.append("")
        elif isinstance(node, Footnote):
            lines.append(f"[^{node.id}]: {node.text}")
            lines.append("")
        elif isinstance(node, HtmlBlock):
            import re

            lines.append(re.sub(r"<[^>]+>", "", node.content).strip())
            lines.append("")
        elif isinstance(node, CodeBlock):
            lines.append(node.content)
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
