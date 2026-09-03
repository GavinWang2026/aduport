"""Shared DocumentModel → HTML body renderer (PDF / HTML / EPUB)."""

from __future__ import annotations

import base64
import html
import re

from app.config import settings
from app.core.document_model import (
    Blockquote,
    Bookmark,
    Chart,
    CodeBlock,
    DocumentModel,
    Comment,
    ContentControl,
    FieldCode,
    Footnote,
    HeaderFooter,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    MathBlock,
    Numbering,
    PageSetup,
    PivotTable,
    Revision,
    Paragraph,
    RunStyle,
    Section,
    SpeakerNote,
    SmartArt,
    OleObject,
    Table,
    TextBox,
    ensure_rich_text,
)
from app.exporters.export_warnings import node_type_name, placeholder_line, warn_export

_SAFE_MIME_RE = re.compile(r"^[\w.+-]+/[\w.+-]+$")


def _safe_image_mime(mime: str | None) -> str:
    raw = (mime or "application/octet-stream").strip()
    if not _SAFE_MIME_RE.match(raw):
        return "application/octet-stream"
    # Escape any residual attribute-breaking chars
    return html.escape(raw, quote=True)


def render_body_html(model: DocumentModel, *, include_title: bool = True) -> str:
    parts: list[str] = []
    layout_preview = render_pdf_layout_html(model)
    if layout_preview:
        parts.append(layout_preview)
    section_html = render_section_props_html(model)
    if section_html:
        parts.append(section_html)
    if include_title and model.title:
        parts.append(f"<h1>{html.escape(model.title)}</h1>")

    i = 0
    nodes = model.nodes
    meta = model.metadata or {}
    # D-30: consume leftover OOXML metadata when AST chrome is absent.
    if not any(isinstance(n, HeaderFooter) for n in nodes) and meta.get("docx_header"):
        parts.append(
            f"<header class='docx-chrome'>{html.escape(str(meta['docx_header'])[:500])}</header>"
        )
    if not any(isinstance(n, FieldCode) for n in nodes):
        fields = meta.get("docx_field_codes") or []
        if isinstance(fields, list):
            for item in fields[:20]:
                if isinstance(item, dict):
                    inst = str(item.get("instr") or item.get("instruction") or item.get("text") or "")
                else:
                    inst = str(item)
                if inst:
                    parts.append(
                        f"<span class='field-code'>{html.escape(inst[:200])}</span>"
                    )
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
            parts.append('<hr data-mdbridge="---"/>')
            i += 1
        elif isinstance(node, ListItem):
            ordered = node.ordered
            tag = "ol" if ordered else "ul"
            parts.append(f"<{tag}>")
            while i < len(nodes) and isinstance(nodes[i], ListItem) and nodes[i].ordered == ordered:
                li = nodes[i]
                if li.checked is not None:
                    checked = " checked" if li.checked else ""
                    mark = "[x]" if li.checked else "[ ]"
                    box = (
                        f'<input type="checkbox" disabled{checked} '
                        f'aria-checked="{"true" if li.checked else "false"}"/> '
                        f'<span class="task-mark">{mark}</span> '
                    )
                else:
                    box = ""
                body = _render_rich_html(ensure_rich_text(li.text))
                # v6/T1-#3: render multi-paragraph / fenced-code children that
                # live inside the list item, instead of dropping them silently.
                for child in li.children:
                    if isinstance(child, CodeBlock):
                        lang = child.lang or ""
                        cls = f' class="language-{html.escape(lang)}"' if lang else ""
                        body += (
                            f"<pre><code{cls}>{html.escape(child.content)}</code></pre>"
                        )
                    elif isinstance(child, Paragraph):
                        body += f"<p>{_render_rich_html(ensure_rich_text(child.text))}</p>"
                parts.append(f"<li>{box}{body}</li>")
                i += 1
            parts.append(f"</{tag}>")
        elif isinstance(node, Table):
            parts.append(_render_table_html(node))
            i += 1
        elif isinstance(node, Image):
            wrap_style = ""
            if node.wrap == "left":
                wrap_style = " style='float:left;margin:0 1em 1em 0;max-width:45%'"
            elif node.wrap == "right":
                wrap_style = " style='float:right;margin:0 0 1em 1em;max-width:45%'"
            if node.data:
                b64 = base64.b64encode(node.data).decode("ascii")
                alt = html.escape(node.alt_text.split("|", 1)[0])
                mime = _safe_image_mime(node.mime_type)
                parts.append(
                    f"<img src='data:{mime};base64,{b64}' alt='{alt}'{wrap_style}/>"
                )
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
            # Defer to endnotes section (better PDF/EPUB layout).
            i += 1
        elif isinstance(node, Comment):
            author = html.escape(node.author or "reviewer")
            body = html.escape(node.text)
            anchor = html.escape(node.anchor)
            anchor_html = (
                f"<span class='comment-anchor'>（{anchor}）</span>" if anchor else ""
            )
            parts.append(
                f"<aside class='comment' data-author='{author}'>"
                f"<p><strong class='comment-author'>{author}</strong>"
                f"{anchor_html}: {body}</p></aside>"
            )
            i += 1
        elif isinstance(node, Revision):
            tag = "ins" if node.change == "insert" else "del"
            author = html.escape(node.author or "")
            body = html.escape(node.text)
            parts.append(
                f"<{tag} class='revision' data-author='{author}' title='{author}'>"
                f"{body}</{tag}>"
            )
            i += 1
        elif isinstance(node, ContentControl):
            label = html.escape(node.alias or node.tag or "control")
            ctype = html.escape(node.control_type or "")
            body = html.escape(node.text)
            parts.append(
                f"<aside class='content-control' data-tag='{html.escape(node.tag)}' "
                f"data-type='{ctype}'><strong>{label}</strong> {body}</aside>"
            )
            i += 1
        elif isinstance(node, PivotTable):
            parts.append(_render_pivot_table_html(node))
            i += 1
        elif isinstance(node, Chart):
            parts.append(
                f"<aside class='chart' data-chart-type='{html.escape(node.chart_type)}'>"
                f"{html.escape(node.plain_text())}</aside>"
            )
            i += 1
        elif isinstance(node, SpeakerNote):
            parts.append(
                f"<aside class='speaker-note' data-slide='{int(node.slide_index)}'>"
                f"{html.escape(node.text)}</aside>"
            )
            i += 1
        elif isinstance(node, SmartArt):
            items = "".join(f"<li>{html.escape(it)}</li>" for it in node.items[:50])
            parts.append(
                f"<aside class='smartart' data-layout='{html.escape(node.layout)}'>"
                f"<strong>{html.escape(node.plain_text())}</strong>"
                f"<ul>{items}</ul></aside>"
            )
            i += 1
        elif isinstance(node, OleObject):
            parts.append(
                f"<aside class='ole-object' data-prog='{html.escape(node.prog_id)}'>"
                f"{html.escape(node.plain_text())}</aside>"
            )
            i += 1
        elif isinstance(node, TextBox):
            parts.append(_render_textbox_html(node))
            i += 1
        elif isinstance(node, HtmlBlock):
            from app.importers.md_importer import _sanitize_html_fragment

            parts.append(_sanitize_html_fragment(node.content or ""))
            i += 1
        elif isinstance(node, CodeBlock):
            lang = html.escape(node.lang)
            content = html.escape(node.content)
            parts.append(f"<pre><code class='language-{lang}'>{content}</code></pre>")
            i += 1
        elif isinstance(node, HeaderFooter):
            tag = "header" if node.kind == "header" else "footer"
            parts.append(
                f"<{tag} class='docx-chrome'>{html.escape(node.text)}</{tag}>"
            )
            i += 1
        elif isinstance(node, FieldCode):
            label = html.escape(
                node.result_text or node.instruction or node.field_type
            )
            parts.append(
                f"<span class='field-code' data-field='{html.escape(node.field_type)}'>"
                f"{label}</span>"
            )
            i += 1
        elif isinstance(
            node,
            (Section, PageSetup, Bookmark, RunStyle, Numbering),
        ):
            kind = node_type_name(node)
            detail = node.plain_text() if hasattr(node, "plain_text") else kind
            warn_export(model, f"html: {kind} → placeholder (layout shallow)")
            parts.append(
                f"<aside class='mdbridge-placeholder' data-type='{html.escape(kind)}'>"
                f"{html.escape(placeholder_line(kind, detail))}</aside>"
            )
            i += 1
        else:
            kind = node_type_name(node)
            plain = getattr(node, "plain_text", None)
            detail = plain() if callable(plain) else kind
            warn_export(model, f"html: unhandled node {kind} → placeholder")
            parts.append(
                f"<aside class='mdbridge-placeholder' data-type='{html.escape(kind)}'>"
                f"{html.escape(placeholder_line(kind, str(detail)[:200]))}</aside>"
            )
            i += 1
    footnotes_html = _render_footnotes_section(nodes)
    if footnotes_html:
        parts.append(footnotes_html)
    return "\n".join(parts)


def _render_footnotes_section(nodes: list) -> str:
    fns = [n for n in nodes if isinstance(n, Footnote)]
    if not fns:
        return ""
    items = []
    for fn in fns:
        fid = html.escape(fn.id)
        items.append(
            f'<li class="footnote" id="fn-{fid}" value="{fid}">'
            f"{html.escape(fn.text)}</li>"
        )
    return (
        '<section class="footnotes" aria-label="Footnotes">'
        "<h2>Footnotes</h2><ol>"
        + "".join(items)
        + "</ol></section>"
    )


def render_pdf_layout_html(model: DocumentModel) -> str:
    """Positioned HTML preview from metadata.pdf_layout.blocks (PDF charbox import)."""
    layout = (model.metadata or {}).get("pdf_layout") or {}
    blocks = layout.get("blocks") or []
    if not blocks:
        return ""
    by_page: dict[int, list[dict[str, object]]] = {}
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        page = int(raw.get("page") or 1)
        by_page.setdefault(page, []).append(raw)

    parts = [
        '<section class="pdf-layout-preview" aria-label="PDF layout preview">',
        '<h2 class="pdf-layout-title">版面预览（charbox）</h2>',
    ]
    for page in sorted(by_page):
        page_blocks = by_page[page]
        max_right = max(float(b.get("right", 0) or 0) for b in page_blocks)
        max_y = max(
            float(b.get("y_from_top", 0) or 0) + max(
                float(b.get("top", 0) or 0) - float(b.get("bottom", 0) or 0), 8
            )
            for b in page_blocks
        )
        w = int(max(max_right + 24, 320))
        h = int(max(max_y + 32, 200))
        parts.append(
            f'<div class="pdf-page" data-page="{page}" '
            f'style="position:relative;width:{w}px;height:{h}px">'
        )
        for b in page_blocks:
            left = float(b.get("left", 0) or 0)
            top = float(b.get("y_from_top", 0) or 0)
            text = html.escape(str(b.get("text") or ""))
            if not text:
                continue
            parts.append(
                f'<div class="pdf-block" style="position:absolute;left:{left:.1f}px;'
                f'top:{top:.1f}px;font-size:11px;line-height:1.25">{text}</div>'
            )
        parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)


def render_section_props_html(model: DocumentModel) -> str:
    """Prefer PageSetup/Section AST nodes; fall back to legacy metadata."""
    bits: list[str] = []
    page = next((n for n in model.nodes if isinstance(n, PageSetup)), None)
    section = next((n for n in model.nodes if isinstance(n, Section)), None)
    if page is not None:
        bits.append(f"{page.page_size} {page.orientation}")
        if page.margins_mm:
            bits.append(
                "边距 "
                + ", ".join(f"{k} {v}mm" for k, v in page.margins_mm.items())
            )
    if section is not None and section.title:
        bits.append(section.title)
    if section is not None and section.props:
        props = section.props
        if props.get("page_width_mm") and props.get("page_height_mm"):
            bits.append(f"{props['page_width_mm']}×{props['page_height_mm']} mm")
    if not bits:
        props = (model.metadata or {}).get("section_props") or {}
        if props.get("page_width_mm") and props.get("page_height_mm"):
            bits.append(f"{props['page_width_mm']}×{props['page_height_mm']} mm")
        margins = props.get("margins_mm") or {}
        if isinstance(margins, dict) and margins:
            bits.append(
                "边距 "
                + ", ".join(f"{k} {v}mm" for k, v in margins.items())
            )
        if props.get("orientation"):
            bits.append(str(props["orientation"]))
    if not bits:
        return ""
    body = html.escape(" · ".join(bits))
    return (
        f'<aside class="section-props" title="节属性">'
        f"<strong>节属性</strong> {body}</aside>"
    )


def _render_textbox_html(box: TextBox) -> str:
    src = html.escape(box.source or "")
    text = html.escape(box.text)
    pos = ""
    if box.x_mm is not None and box.y_mm is not None:
        pos = f" data-x-mm='{box.x_mm}' data-y-mm='{box.y_mm}'"
    return (
        f"<aside class='text-box'{pos} data-source='{src}'>"
        f"<span class='text-box-label'>文本框</span> {text}</aside>"
    )


def _render_pivot_table_html(p: PivotTable) -> str:
    name = html.escape(p.name or "Pivot")
    loc = html.escape(p.location or "")
    src = html.escape(p.source_range or "")
    rows = ", ".join(html.escape(f) for f in p.row_fields)
    cols = ", ".join(html.escape(f) for f in p.col_fields)
    data = ", ".join(html.escape(f) for f in p.data_fields)
    return (
        f"<aside class='pivot-table' data-name='{name}'>"
        f"<p><strong>Pivot:</strong> {name}"
        f"{f' @ {loc}' if loc else ''}"
        f"{f' (src {src})' if src else ''}</p>"
        f"{f'<p>Rows: {rows}</p>' if rows else ''}"
        f"{f'<p>Cols: {cols}</p>' if cols else ''}"
        f"{f'<p>Values: {data}</p>' if data else ''}"
        "</aside>"
    )


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
    if not table.rows and not table.cells:
        return ""
    header_rows = _table_header_rows(table)
    if table.cells and _table_has_span_cells(table):
        return _render_table_html_spans(table)
    if not table.rows:
        return ""
    col_count = max(len(r) for r in table.rows)
    lines = ["<table>"]
    for r_idx, row in enumerate(table.rows):
        lines.append("<tr>")
        tag = "th" if r_idx < header_rows else "td"
        padded = list(row) + [""] * (col_count - len(row))
        for cell in padded[:col_count]:
            lines.append(f"<{tag}>{html.escape(str(cell))}</{tag}>")
        lines.append("</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def _table_header_rows(table: Table) -> int:
    # Global switch wins — callers may set header_rows from GFM thead, but
    # MDBRIDGE_TABLE_FIRST_ROW_HEADER=false must still emit all <td> (P1 #10 / v11).
    if not settings.table_first_row_header:
        return 0
    if table.header_rows > 0:
        return int(table.header_rows)
    return 1


def _table_has_span_cells(table: Table) -> bool:
    if not table.cells:
        return False
    for row in table.cells:
        for cell in row:
            # Only real spans — origin_* alone must not bypass header switch (v8 P1).
            if cell.covered or cell.row_span > 1 or cell.col_span > 1:
                return True
    return False


def _table_cell_tag(r_idx: int, cell: TableCell, header_rows: int) -> str:
    if header_rows > 0 and r_idx < header_rows:
        return "th"
    return "td"


def _render_table_html_spans(table: Table) -> str:
    header_rows = _table_header_rows(table)
    lines = ["<table>"]
    for r_idx, row in enumerate(table.cells):
        lines.append("<tr>")
        for cell in row:
            if cell.covered:
                continue
            tag = _table_cell_tag(r_idx, cell, header_rows)
            attrs: list[str] = []
            if cell.col_span > 1:
                attrs.append(f'colspan="{cell.col_span}"')
            if cell.row_span > 1:
                attrs.append(f'rowspan="{cell.row_span}"')
            attr_str = f" {' '.join(attrs)}" if attrs else ""
            lines.append(f"<{tag}{attr_str}>{html.escape(cell.text)}</{tag}>")
        lines.append("</tr>")
    lines.append("</table>")
    return "\n".join(lines)


def render_ebook_css() -> str:
    """Inline CSS for MOBI/AZW3/KF8 HTML payloads (footnotes, math, pivot)."""
    return (
        "body{font-family:serif;line-height:1.5}"
        ".footnotes{border-top:1px solid #ccc;margin-top:1.5em;padding-top:0.5em;font-size:0.9em}"
        ".math-display,pre.math{font-family:monospace}"
        "aside.pivot-table,aside.content-control,aside.comment"
        "{border-left:3px solid #999;padding-left:0.5em;margin:0.6em 0}"
    )
