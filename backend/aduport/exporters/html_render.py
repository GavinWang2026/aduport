"""Shared DocumentModel → HTML body renderer (PDF / HTML / EPUB)."""

from __future__ import annotations

import base64
import html
import re

from aduport.config import settings
from aduport.core.document_model import (
    Blockquote,
    Bookmark,
    Chart,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    FieldCode,
    Footnote,
    HeaderFooter,
    Heading,
    HorizontalRule,
    PageBreak,
    HtmlBlock,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    MathBlock,
    Numbering,
    OleObject,
    PageSetup,
    Paragraph,
    PivotTable,
    Revision,
    RunStyle,
    Section,
    SmartArt,
    SpeakerNote,
    Table,
    TableCell,
    TextBox,
    ensure_rich_text,
)
from aduport.core.title_source import title_repeats_leading_heading
from aduport.exporters.export_warnings import node_type_name, placeholder_line, warn_export

_SAFE_MIME_RE = re.compile(r"^[\w.+-]+/[\w.+-]+$")


def _safe_image_mime(mime: str | None) -> str:
    raw = (mime or "application/octet-stream").strip()
    if not _SAFE_MIME_RE.match(raw):
        return "application/octet-stream"
    # Escape any residual attribute-breaking chars
    return html.escape(raw, quote=True)


def render_body_html(
    model: DocumentModel,
    *,
    include_title: bool = True,
    markdown_cues: bool = False,
) -> str:
    parts: list[str] = []
    layout_preview = render_pdf_layout_html(model)
    if layout_preview:
        parts.append(layout_preview)
    section_html = render_section_props_html(model)
    if section_html:
        parts.append(section_html)
    if include_title and model.title and not title_repeats_leading_heading(model):
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
            parts.append(
                f"<p>{_render_rich_html(ensure_rich_text(node.text), markdown_cues=markdown_cues)}</p>"
            )
            i += 1
        elif isinstance(node, Blockquote):
            inner = "".join(
                f"<p>{_render_rich_html(ensure_rich_text(p), markdown_cues=markdown_cues)}</p>"
                for p in node.paragraphs
            )
            parts.append(f"<blockquote>{inner}</blockquote>")
            i += 1
        elif isinstance(node, PageBreak):
            parts.append("<div class='page-break' style='page-break-before:always'></div>")
            i += 1
        elif isinstance(node, HorizontalRule):
            parts.append('<hr data-aduport="---"/>')
            if markdown_cues:
                # The rule itself has no text layer. A following line does.
                parts.append("<p>---</p>")
            i += 1
        elif isinstance(node, ListItem):
            items: list[ListItem] = []
            while i < len(nodes) and isinstance(nodes[i], ListItem):
                items.append(nodes[i])
                i += 1
            parts.append(_render_nested_list(items))
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
                    f"<span class='img-alt'>[Image: {alt}]</span>"
                )
            else:
                alt_raw = node.alt_text or ""
                if "|" in alt_raw:
                    alt_name, src = alt_raw.split("|", 1)
                else:
                    alt_name, src = alt_raw, (node.url or "")
                src = (src or "").strip()
                low = src.lower()
                if src and not low.startswith(("javascript:", "data:", "vbscript:", "file:")):
                    alt = html.escape(alt_name.split("|", 1)[0])
                    parts.append(
                        f"<img src='{html.escape(src, quote=True)}' alt='{alt}'{wrap_style}/>"
                    )
                else:
                    alt = html.escape(alt_raw)
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
            from aduport.importers.md_importer import _sanitize_html_fragment

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
                f"<aside class='aduport-placeholder' data-type='{html.escape(kind)}'>"
                f"{html.escape(placeholder_line(kind, detail))}</aside>"
            )
            i += 1
        else:
            kind = node_type_name(node)
            plain = getattr(node, "plain_text", None)
            detail = plain() if callable(plain) else kind
            warn_export(model, f"html: unhandled node {kind} → placeholder")
            parts.append(
                f"<aside class='aduport-placeholder' data-type='{html.escape(kind)}'>"
                f"{html.escape(placeholder_line(kind, str(detail)[:200]))}</aside>"
            )
            i += 1
    footnotes_html = _render_footnotes_section(nodes)
    if footnotes_html:
        parts.append(footnotes_html)
    # Cue markdown is a PDF attachment, not a following page. Painting it
    # here used to add a visible source page the user could print.
    return "\n".join(parts)


PDF_CUE_MARK = "ADUPORT-CUES-v1"
PDF_CUE_ATTACHMENT = "aduport-cues.md"


def pdf_cue_markdown(nodes: list) -> str:
    """Markdown the PDF text importer folds back over the visual page.

    This is an attachment, not a printed page. WeasyPrint's text layer drops
    heading levels, tables, list depth, task boxes, footnote ids, and inline
    marks; the attachment carries those lines, and the sentinel is how the
    importer finds them.
    """
    from aduport.exporters.md_exporter import export_md

    cue_nodes = []
    for node in nodes:
        if isinstance(node, (Blockquote, CodeBlock, ListItem, Footnote, Heading, Table)):
            cue_nodes.append(node)
            continue
        if isinstance(node, Paragraph):
            rich = ensure_rich_text(getattr(node, "text", ""))
            if rich.marks:
                cue_nodes.append(node)
    if not cue_nodes:
        return ""
    markdown = export_md(DocumentModel(nodes=cue_nodes))
    lines = [line for line in markdown.splitlines() if line.strip()]
    if not lines:
        return ""
    return PDF_CUE_MARK + "\n" + "\n".join(lines) + "\n"


def _pdf_markdown_appendix(nodes: list) -> str:
    """Cue payload as HTML that does not start a page.

    Older exports painted this with ``page-break-before:always``. Callers that
    still inspect the payload get the same lines, without a page break.
    """
    payload = pdf_cue_markdown(nodes)
    if not payload:
        return ""
    paras = "".join(
        "<p style='margin:0'>"
        f"{html.escape(line)}</p>"
        for line in payload.splitlines()
        if line.strip()
    )
    return f"<section class='aduport-cues'>{paras}</section>"


def _list_item_body(li: ListItem) -> str:
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
    for child in li.children:
        if isinstance(child, CodeBlock):
            lang = child.lang or ""
            cls = f' class="language-{html.escape(lang)}"' if lang else ""
            body += f"<pre><code{cls}>{html.escape(child.content)}</code></pre>"
        elif isinstance(child, Paragraph):
            body += f"<p>{_render_rich_html(ensure_rich_text(child.text))}</p>"
    return box + body


def _render_nested_list(items: list[ListItem]) -> str:
    """Nest ``<ul>``/``<ol>`` from ``ListItem.level`` so HTML round-trip keeps depth."""

    def rec(start: int) -> tuple[str, int]:
        if start >= len(items):
            return "", start
        level = int(items[start].level or 0)
        ordered = items[start].ordered
        tag = "ol" if ordered else "ul"
        start_attr = ""
        if ordered and items[start].number not in (None, 1):
            start_attr = f' start="{int(items[start].number)}"'
        chunks = [f"<{tag}{start_attr}>"]
        i = start
        while i < len(items):
            cur = items[i]
            cur_level = int(cur.level or 0)
            if cur_level < level:
                break
            if cur_level == level and cur.ordered != ordered:
                break
            if cur_level > level:
                nested, i = rec(i)
                if chunks[-1].endswith("</li>"):
                    chunks[-1] = chunks[-1][:-5] + nested + "</li>"
                else:
                    chunks.append(f"<li>{nested}</li>")
                continue
            i += 1
            nested = ""
            if i < len(items) and int(items[i].level or 0) > level:
                nested, i = rec(i)
            chunks.append(f"<li>{_list_item_body(cur)}{nested}</li>")
        chunks.append(f"</{tag}>")
        return "".join(chunks), i

    parts: list[str] = []
    seg = 0
    while seg < len(items):
        chunk, nxt = rec(seg)
        if not chunk or nxt <= seg:
            break
        parts.append(chunk)
        seg = nxt
    return "".join(parts)


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
        '<section class="footnotes" epub:type="footnotes" aria-label="Footnotes">'
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


def _safe_href(href: str) -> str:
    """Whitelist link schemes for preview/export HTML (report P1-4)."""
    h = (href or "").strip()
    if not h:
        return "#"
    if h.startswith("#"):
        return h
    # Root-relative paths only (block protocol-relative //evil)
    if h.startswith("/") and not h.startswith("//"):
        return h
    lower = h.lower()
    if lower.startswith(("http://", "https://", "mailto:")):
        return h
    return "#"


def _render_rich_html(rich, *, markdown_cues: bool = False) -> str:
    if not rich.marks:
        return html.escape(rich.text)
    rendered = []
    for segment, active_marks in _split_by_marks(rich):
        types = {m.type for m in active_marks}
        escaped = html.escape(segment)
        if InlineMarkType.LINK in types:
            href = next((m.href for m in active_marks if m.type == InlineMarkType.LINK), "#") or "#"
            href = _safe_href(href)
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
            if markdown_cues:
                escaped = f"`{escaped}`"
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
    if table.cells and (
        _table_has_span_cells(table) or _table_has_cell_images(table)
    ):
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
    # ADUPORT_TABLE_FIRST_ROW_HEADER=false must still emit all <td> (P1 #10 / v11).
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


def _table_has_cell_images(table: Table) -> bool:
    if not table.cells:
        return False
    return any(cell.images for row in table.cells for cell in row)


def _table_cell_inner_html(cell: TableCell) -> str:
    parts = [html.escape(cell.text)] if cell.text else []
    for img in cell.images or []:
        alt = html.escape((img.alt_text or "image").split("|", 1)[0])
        if img.data:
            b64 = base64.b64encode(img.data).decode("ascii")
            mime = _safe_image_mime(img.mime_type)
            parts.append(f"<img src='data:{mime};base64,{b64}' alt='{alt}'/>")
        elif img.url:
            parts.append(f"<img src='{html.escape(img.url)}' alt='{alt}'/>")
        else:
            parts.append(f"[Image: {alt}]")
    return "".join(parts) or "&nbsp;"


def _table_cell_tag(r_idx: int, cell: TableCell, header_rows: int) -> str:
    if header_rows > 0 and r_idx < header_rows:
        return "th"
    return "td"


def _render_table_html_spans(table: Table) -> str:
    """Emit a rectangular HTML grid: empty cells become <td></td>, not omitted."""
    header_rows = _table_header_rows(table)
    n_cols = max((len(row) for row in table.cells), default=0)
    # Columns still occupied by a rowspan from a previous row.
    span_remain = [0] * n_cols
    lines = ["<table>"]
    for r_idx, row in enumerate(table.cells):
        lines.append("<tr>")
        padded = list(row) + [
            TableCell(text="", covered=False) for _ in range(max(0, n_cols - len(row)))
        ]
        col = 0
        while col < n_cols:
            if span_remain[col] > 0:
                span_remain[col] -= 1
                col += 1
                continue
            cell = padded[col]
            if cell.covered:
                # Horizontal merge continuation already counted in a prior colspan.
                col += 1
                continue
            tag = _table_cell_tag(r_idx, cell, header_rows)
            col_span = max(1, int(cell.col_span or 1))
            row_span = max(1, int(cell.row_span or 1))
            # Do not overrun the grid when a colspan was recorded too wide.
            col_span = min(col_span, n_cols - col)
            attrs: list[str] = []
            if col_span > 1:
                attrs.append(f'colspan="{col_span}"')
            if row_span > 1:
                attrs.append(f'rowspan="{row_span}"')
            attr_str = f" {' '.join(attrs)}" if attrs else ""
            lines.append(
                f"<{tag}{attr_str}>{_table_cell_inner_html(cell)}</{tag}>"
            )
            if row_span > 1:
                for k in range(col_span):
                    span_remain[col + k] = max(span_remain[col + k], row_span - 1)
            col += col_span
        # Any trailing hole left by a bad grid → empty cell (keeps paste width).
        while col < n_cols:
            if span_remain[col] > 0:
                span_remain[col] -= 1
                col += 1
                continue
            tag = "th" if header_rows > 0 and r_idx < header_rows else "td"
            lines.append(f"<{tag}></{tag}>")
            col += 1
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
