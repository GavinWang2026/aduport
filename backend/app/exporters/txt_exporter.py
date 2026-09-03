"""Export DocumentModel to plain text."""

from __future__ import annotations

from app.core.document_model import (
    Blockquote,
    Chart,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    ListItem,
    MathBlock,
    Paragraph,
    PivotTable,
    Revision,
    SpeakerNote,
    SmartArt,
    OleObject,
    Table,
    TextBox,
)
from app.exporters.rich_visible import render_rich_visible


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
            lines.append(render_rich_visible(node.text))
            lines.append("")
        elif isinstance(node, Blockquote):
            for p in node.paragraphs:
                for line in render_rich_visible(p).splitlines() or [""]:
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
            lines.append(f"{indent}{mark} {render_rich_visible(node.text)}")
            # v6/T1-#3: a multi-paragraph list item carries its extra paragraphs
            # and fenced code blocks in `children`. Rendering only `node.text`
            # silently dropped that content from txt/html/json output.
            for child in node.children:
                if isinstance(child, CodeBlock):
                    lines.append(f"{indent}  ```{child.lang or ''}")
                    for line in child.content.splitlines() or [""]:
                        lines.append(f"{indent}  {line}")
                    lines.append(f"{indent}  ```")
                elif isinstance(child, Paragraph):
                    for line in render_rich_visible(child.text).splitlines() or [""]:
                        lines.append(f"{indent}  {line}")
        elif isinstance(node, Table):
            from app.exporters.table_span import (
                render_table_html,
                table_flat_matrix,
                table_has_span_cells,
            )

            if table_has_span_cells(node):
                html_table = render_table_html(node)
                if html_table:
                    lines.append(html_table)
                    lines.append("")
                    continue
            matrix = table_flat_matrix(node)
            for row in matrix:
                lines.append(" | ".join(row))
            lines.append("")
        elif isinstance(node, Image):
            alt = node.alt_text or "image"
            lines.append(f"[Image: {alt}]")
            lines.append("")
        elif isinstance(node, Chart):
            lines.append(node.plain_text())
            lines.append("")
        elif isinstance(node, SpeakerNote):
            lines.append(f"[SpeakerNote s{node.slide_index}] {node.text}")
            lines.append("")
        elif isinstance(node, SmartArt):
            lines.append(node.plain_text())
            for it in node.items[:50]:
                lines.append(f"- {it}")
            lines.append("")
        elif isinstance(node, OleObject):
            lines.append(node.plain_text())
            lines.append("")
        elif isinstance(node, MathBlock):
            lines.append(f"$$ {node.content} $$" if node.display else f"$ {node.content} $")
            lines.append("")
        elif isinstance(node, Footnote):
            lines.append(f"[^{node.id}]: {node.text}")
            lines.append("")
        elif isinstance(node, Comment):
            anchor = f" ({node.anchor})" if node.anchor else ""
            lines.append(f"[Comment {node.author or 'reviewer'}{anchor}] {node.text}")
            lines.append("")
        elif isinstance(node, Revision):
            if node.change == "delete":
                lines.append(f"[-del {node.author}] {node.text}")
            else:
                lines.append(f"[+ins {node.author}] {node.text}")
            lines.append("")
        elif isinstance(node, ContentControl):
            label = node.alias or node.tag or "control"
            lines.append(f"[Control:{label}] {node.text}")
            lines.append("")
        elif isinstance(node, TextBox):
            src = f" ({node.source})" if node.source else ""
            lines.append(f"[TextBox{src}] {node.text}")
            lines.append("")
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            lines.append(f"[PivotTable {node.name}] {fields}")
            lines.append("")
        elif isinstance(node, HtmlBlock):
            import re

            lines.append(re.sub(r"<[^>]+>", "", node.content).strip())
            lines.append("")
        elif isinstance(node, CodeBlock):
            fence = f"```{node.lang or ''}"
            lines.append(fence)
            lines.append(node.content)
            lines.append("```")
            lines.append("")
        else:
            # v6/T2-#8: layout chrome (Section/HeaderFooter/FieldCode/…) must
            # not vanish on TXT the way ListItem.children used to.
            plain = getattr(node, "plain_text", None)
            text = plain() if callable(plain) else ""
            if text:
                lines.append(text)
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"
