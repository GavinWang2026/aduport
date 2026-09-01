"""Shared DocumentModel → HTML body renderer (PDF / HTML / EPUB)."""

from __future__ import annotations

import base64
import html

from app.config import settings
from app.core.document_model import (
    Blockquote,
    CodeBlock,
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
    Paragraph,
    Table,
    ensure_rich_text,
)


def render_body_html(model: DocumentModel, *, include_title: bool = True) -> str:
    parts: list[str] = []
    if include_title and model.title:
        parts.append(f"<h1>{html.escape(model.title)}</h1>")

    i = 0
    nodes = model.nodes
    while i < len(nodes):
        node = nodes[i]
        if isinstance(node, Heading):
            tag = f"h{node.level}"
            hid = f' id="h-{i}"' if True else ""
            parts.append(
                f"<{tag}{hid}>{_render_rich_html(ensure_rich_text(node.text))}</{tag}>"
            )
            i += 1
        elif isinstance(node, Paragraph):
            parts.append(f"<p>{_render_rich_html(ensure_rich_text(node.text))}</p>")
            i += 1
        elif isinstance(node, Blockquote):
            inner = "".join(
                f"<p>{_render_rich_html(ensure_rich_text(p))}</p>" for p in node.paragraphs
            )
            parts.append(f"<blockquote>{inner}</blockquote>")
            i += 1
        elif isinstance(node, HorizontalRule):
            parts.append("<hr/>")
            i += 1
        elif isinstance(node, ListItem):
            ordered = node.ordered
            tag = "ol" if ordered else "ul"
            parts.append(f"<{tag}>")
            while i < len(nodes) and isinstance(nodes[i], ListItem) and nodes[i].ordered == ordered:
                li = nodes[i]
                if li.checked is not None:
                    checked = " checked" if li.checked else ""
                    box = (
                        f'<input type="checkbox" disabled{checked} '
                        f'aria-checked="{"true" if li.checked else "false"}"/> '
                    )
                else:
                    box = ""
                parts.append(f"<li>{box}{_render_rich_html(ensure_rich_text(li.text))}</li>")
                i += 1
            parts.append(f"</{tag}>")
        elif isinstance(node, Table):
            parts.append(_render_table_html(node))
            i += 1
        elif isinstance(node, Image):
            if node.data:
                b64 = base64.b64encode(node.data).decode("ascii")
                alt = html.escape(node.alt_text.split("|", 1)[0])
                parts.append(f"<img src='data:{node.mime_type};base64,{b64}' alt='{alt}'/>")
            else:
                alt = html.escape(node.alt_text)
                parts.append(f"<p>[Image: {alt}]</p>")
            i += 1
        elif isinstance(node, MathBlock):
            content = html.escape(node.content)
            if settings.html_katex:
                # KaTeX / MathJax delimiters (auto-render)
                if node.display:
                    parts.append(f'<div class="math-display">\\[{content}\\]</div>')
                else:
                    parts.append(f'<span class="math-inline">\\({content}\\)</span>')
            else:
                if node.display:
                    parts.append(f"<pre class='math'>{content}</pre>")
                else:
                    parts.append(f"<code class='math'>{content}</code>")
            i += 1
        elif isinstance(node, Footnote):
            fid = html.escape(node.id)
            parts.append(
                f"<p class='footnote' id='fn-{fid}'>"
                f"<a href='#fnref-{fid}'><sup>[{fid}]</sup></a> "
                f"{html.escape(node.text)}</p>"
            )
            i += 1
        elif isinstance(node, HtmlBlock):
            safe = node.content.replace("<script", "&lt;script").replace("<iframe", "&lt;iframe")
            parts.append(safe)
            i += 1
        elif isinstance(node, CodeBlock):
            lang = html.escape(node.lang)
            content = html.escape(node.content)
            parts.append(f"<pre><code class='language-{lang}'>{content}</code></pre>")
            i += 1
        else:
            i += 1
    return "\n".join(parts)


def build_toc_html(model: DocumentModel) -> str:
    items: list[str] = []
    for i, node in enumerate(model.nodes):
        if isinstance(node, Heading) and node.level <= 3:
            text = html.escape(node.plain_text())
            items.append(f'<li class="toc-l{node.level}"><a href="#h-{i}">{text}</a></li>')
    if not items:
        return ""
    return '<nav class="toc"><h2>目录</h2><ol>' + "".join(items) + "</ol></nav>"


def _render_rich_html(rich) -> str:
    if not rich.marks:
        return html.escape(rich.text)
    rendered = []
    for segment, active_marks in _split_by_marks(rich):
        types = {m.type for m in active_marks}
        escaped = html.escape(segment)
        if InlineMarkType.LINK in types:
            href = next((m.href for m in active_marks if m.type == InlineMarkType.LINK), "#") or "#"
            # Footnote ref → id for backlink
            extra = ""
            if href.startswith("#fn-"):
                fid = href[4:]
                extra = f" id='fnref-{html.escape(fid)}'"
                escaped = f"<sup>{escaped}</sup>"
            escaped = f"<a href='{html.escape(href)}'{extra}>{escaped}</a>"
        if InlineMarkType.BOLD in types:
            escaped = f"<strong>{escaped}</strong>"
        if InlineMarkType.ITALIC in types:
            escaped = f"<em>{escaped}</em>"
        if InlineMarkType.CODE in types:
            escaped = f"<code>{escaped}</code>"
        if InlineMarkType.STRIKETHROUGH in types:
            escaped = f"<del>{escaped}</del>"
        rendered.append(escaped)
    return "".join(rendered)


def _split_by_marks(rich) -> list[tuple[str, list[InlineMark]]]:
    boundaries = {0, len(rich.text)}
    for mark in rich.marks:
        boundaries.add(max(0, min(mark.start, len(rich.text))))
        boundaries.add(max(0, min(mark.end, len(rich.text))))
    points = sorted(boundaries)
    segments = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        active = [m for m in rich.marks if m.start <= start and m.end >= end]
        segments.append((rich.text[start:end], active))
    return segments


def _render_table_html(table: Table) -> str:
    if not table.rows:
        return ""
    col_count = max(len(r) for r in table.rows)
    use_header = settings.table_first_row_header
    lines = ["<table>"]
    for r_idx, row in enumerate(table.rows):
        lines.append("<tr>")
        tag = "th" if (use_header and r_idx == 0) else "td"
        padded = list(row) + [""] * (col_count - len(row))
        for cell in padded[:col_count]:
            lines.append(f"<{tag}>{html.escape(str(cell))}</{tag}>")
        lines.append("</tr>")
    lines.append("</table>")
    return "\n".join(lines)
