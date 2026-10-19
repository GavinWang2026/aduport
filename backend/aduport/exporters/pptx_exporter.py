"""Export DocumentModel to PPTX using python-pptx.

Depth goals vs the previous title+bullet-only exporter:
- Real PowerPoint tables for ``Table`` nodes
- Embedded pictures when ``Image.data`` is present
- Mixed body content (bullets + tables + images) on content slides
"""

from __future__ import annotations

import re
from io import BytesIO

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Emu, Inches, Pt

from aduport.core.document_model import (
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
    OleObject,
    Paragraph,
    PivotTable,
    Revision,
    SmartArt,
    SpeakerNote,
    Table,
    TextBox,
    ensure_rich_text,
)
from aduport.exporters.export_warnings import placeholder_line, warn_export
from aduport.exporters.table_span import table_flat_matrix, table_has_span_cells

_MAX_CHARS_PER_SLIDE = 900
_MAX_TABLE_ROWS = 16
_MAX_TABLE_COLS = 10
# Rendered on dedicated meta slides — excluded from content-slide buckets.
_META_SLIDE_NODE_TYPES = (
    Comment,
    Footnote,
    Revision,
    ContentControl,
    PivotTable,
    SpeakerNote,
    Chart,
    SmartArt,
    OleObject,
)


def export_pptx(model: DocumentModel) -> bytes:
    prs = Presentation()
    prs.core_properties.category = "AduPort"
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    title = model.title or _first_heading(model) or ""
    cover = ""
    if title and not _is_scaffold_title(title):
        _add_title_slide(prs, title)
        cover = title.strip()
    chrome_lines = _chrome_placeholder_lines(model)

    buckets: list[list] = []
    current: list = []
    chars = 0
    for node in model.nodes:
        if isinstance(node, _META_SLIDE_NODE_TYPES):
            continue
        # Heavy visual nodes get their own slide when the bucket already has content
        if isinstance(node, (Table, Image)) and current:
            buckets.append(current)
            current = [node]
            chars = _estimate_chars(node)
            continue
        if isinstance(node, Heading) and node.level <= 2 and current:
            buckets.append(current)
            current = [node]
            chars = len(node.plain_text())
            continue
        est = _estimate_chars(node)
        if current and chars + est > _MAX_CHARS_PER_SLIDE:
            buckets.append(current)
            current = [node]
            chars = est
        else:
            current.append(node)
            chars += est
    if current:
        buckets.append(current)

    for i, bucket in enumerate(buckets, start=1):
        body = _without_cover_heading(bucket, cover)
        slide_title = _bucket_title(body, i)
        if not slide_title and not body:
            continue
        _add_content_slide(prs, slide_title, body)

    if chrome_lines:
        _add_meta_textbox_slide(prs, "Layout", chrome_lines)

    comments = [n for n in model.nodes if isinstance(n, Comment)]
    if comments:
        _add_comments_slide(prs, comments)

    footnotes = [n for n in model.nodes if isinstance(n, Footnote)]
    if footnotes:
        _add_footnotes_slide(prs, footnotes)

    revisions = [n for n in model.nodes if isinstance(n, Revision)]
    if revisions:
        _add_revisions_slide(prs, revisions)

    controls = [n for n in model.nodes if isinstance(n, ContentControl)]
    if controls:
        _add_content_controls_slide(prs, controls)

    text_boxes = [n for n in model.nodes if isinstance(n, TextBox)]
    if text_boxes:
        _add_text_boxes_slide(prs, text_boxes)

    pivots = [n for n in model.nodes if isinstance(n, PivotTable)]
    if pivots:
        _add_pivot_tables_slide(prs, pivots)

    charts = [n for n in model.nodes if isinstance(n, Chart)]
    if charts:
        _add_charts_slide(prs, charts)

    smartarts = [n for n in model.nodes if isinstance(n, SmartArt)]
    if smartarts:
        _add_smartart_slide(prs, smartarts)
        warn_export(
            model,
            f"pptx: {len(smartarts)} SmartArt → caption slide (no native diagram tree)",
        )

    oles = [n for n in model.nodes if isinstance(n, OleObject)]
    if oles:
        _add_ole_slide(prs, oles)
        warn_export(
            model,
            f"pptx: {len(oles)} OleObject → caption slide (no binary embed)",
        )

    anims = (model.metadata or {}).get("pptx_animations")
    if isinstance(anims, list) and anims:
        _add_animations_slide(prs, anims)

    _apply_speaker_notes(prs, model)

    buf = BytesIO()
    prs.save(buf)
    layouts = [s.slide_layout.name for s in prs.slides]
    if layouts:
        model.metadata = dict(model.metadata or {})
        model.metadata["pptx_slide_layouts"] = layouts[:50]
    return buf.getvalue()


def _first_heading(model: DocumentModel) -> str:
    for n in model.nodes:
        if isinstance(n, Heading):
            return n.plain_text()
    return ""


def _estimate_chars(node) -> int:
    if isinstance(node, (Heading, Paragraph)):
        return len(node.plain_text()) + 8
    if isinstance(node, ListItem):
        return len(node.plain_text()) + 4
    if isinstance(node, Blockquote):
        return sum(len(ensure_rich_text(p).text) for p in node.paragraphs) + 8
    if isinstance(node, CodeBlock):
        return len(node.content) + 8
    if isinstance(node, Table):
        rows = node.rows or [[c.text for c in row] for row in (node.cells or [])]
        return sum(len(str(c)) for row in rows for c in row) + 80
    if isinstance(node, MathBlock):
        return len(node.content) + 4
    if isinstance(node, Footnote):
        return len(node.text) + 8
    if isinstance(node, Comment):
        return len(node.text) + len(node.author) + len(node.anchor) + 12
    if isinstance(node, Revision):
        return len(node.text) + len(node.author) + 12
    if isinstance(node, ContentControl):
        return len(node.text) + len(node.alias) + len(node.tag) + 12
    if isinstance(node, TextBox):
        return len(node.text) + len(node.source) + 12
    if isinstance(node, PivotTable):
        fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
        return len(node.name) + len(fields) + len(node.source_range) + 20
    if isinstance(node, Image):
        return 120
    return 10


def _is_scaffold_title(title: str) -> bool:
    """Titles the exporter invents. They must not come back as body headings."""
    text = (title or "").strip()
    if not text:
        return True
    if text.casefold() in {"document", "untitled", "AduPort"}:
        return True
    return re.fullmatch(r"Slide \d+", text) is not None


def _chrome_placeholder_lines(model: DocumentModel) -> list[str]:
    """Field codes, bookmarks, sections and headers have no slide object (v19 D9).

    The text still lands on a Layout slide so the conversion is not a silent drop.
    """
    from aduport.core.document_model import Bookmark, FieldCode, HeaderFooter, PageSetup, Section

    lines: list[str] = []
    seen: set[str] = set()
    for node in model.nodes:
        if not isinstance(node, (FieldCode, Bookmark, Section, HeaderFooter, PageSetup)):
            continue
        kind = str(getattr(node, "type", "") or type(node).__name__)
        if kind not in seen:
            seen.add(kind)
            warn_export(
                model,
                f"pptx: {kind} kept as a Layout slide line (no slide equivalent)",
            )
        if isinstance(node, PageSetup):
            continue
        detail = node.plain_text() if hasattr(node, "plain_text") else ""
        if detail:
            lines.append(placeholder_line(kind, detail)[:500])
    return lines


def _bucket_title(bucket: list, index: int) -> str:
    del index
    for n in bucket:
        if isinstance(n, Heading):
            return n.plain_text()[:80]
    return ""


def _without_cover_heading(nodes: list, cover: str) -> list:
    """Drop the heading already shown on the title slide.

    The next content slide used to take that same string as its own title,
    so opening the deck showed the title twice. A later heading with different
    text stays, and becomes that slide's title.
    """
    if not cover:
        return list(nodes)
    dropped = False
    kept: list = []
    for node in nodes:
        if (
            not dropped
            and isinstance(node, Heading)
            and node.plain_text().strip() == cover
        ):
            dropped = True
            continue
        kept.append(node)
    return kept


def _add_comments_slide(prs: Presentation, comments: list[Comment]) -> None:
    """Review slide: author + anchor + text (Word-style comment metadata)."""
    layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = "Review comments"
    left = Inches(0.7)
    top = Inches(1.55)
    width = Inches(12.0)
    for c in comments[:24]:
        anchor = (c.anchor or "").strip()
        head = c.author or "reviewer"
        if anchor:
            head = f"{head} → {anchor}"
        line = f"{head}: {c.text}"
        height = Inches(0.55)
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = line[:500]
        p.font.size = Pt(13)
        p.font.color.rgb = RGBColor(0x5B, 0x66, 0x75)
        top += height + Inches(0.08)


def _add_meta_textbox_slide(
    prs: Presentation,
    title: str,
    lines: list[str],
    *,
    font_size: int = 13,
    color: RGBColor | None = None,
) -> None:
    """Review-style slide: title + stacked text boxes (Comment/Footnote/meta parity)."""
    if not lines:
        return
    layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = title
    left = Inches(0.7)
    top = Inches(1.55)
    width = Inches(12.0)
    rgb = color or RGBColor(0x5B, 0x66, 0x75)
    for line in lines[:24]:
        height = Inches(0.55)
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = line[:500]
        p.font.size = Pt(font_size)
        p.font.color.rgb = rgb
        top += height + Inches(0.08)


def _add_revisions_slide(prs: Presentation, revisions: list[Revision]) -> None:
    lines: list[str] = []
    for rev in revisions:
        tag = "ins" if rev.change == "insert" else "del"
        author = rev.author or "editor"
        lines.append(f"[{tag} {author}] {rev.text}")
    _add_meta_textbox_slide(prs, "Track changes", lines, color=RGBColor(0x9A, 0x67, 0x00))


def _add_content_controls_slide(prs: Presentation, controls: list[ContentControl]) -> None:
    lines: list[str] = []
    for cc in controls:
        label = cc.alias or cc.tag or "control"
        ctype = cc.control_type or "richText"
        lines.append(f"{label} ({ctype}): {cc.text}")
    _add_meta_textbox_slide(prs, "Content controls", lines, color=RGBColor(0x18, 0x5F, 0xA5))


def _add_text_boxes_slide(prs: Presentation, boxes: list[TextBox]) -> None:
    lines: list[str] = []
    for box in boxes:
        src = f" [{box.source}]" if box.source else ""
        lines.append(f"Text box{src}: {box.text}")
    _add_meta_textbox_slide(prs, "Text boxes", lines)


def _add_pivot_tables_slide(prs: Presentation, pivots: list[PivotTable]) -> None:
    lines: list[str] = []
    for pt in pivots:
        fields = " / ".join(pt.row_fields + pt.col_fields + pt.data_fields)
        loc = f" @ {pt.location}" if pt.location else ""
        src = f" ← {pt.source_range}" if pt.source_range else ""
        lines.append(f"{pt.name or 'Pivot'}{loc}: {fields}{src}")
    _add_meta_textbox_slide(prs, "Pivot tables", lines, font_size=12)


def _add_charts_slide(prs: Presentation, charts: list[Chart]) -> None:
    """Emit native PowerPoint charts (DrawingML), not text captions."""
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE

    type_map = {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE,
        "pie": XL_CHART_TYPE.PIE,
        "area": XL_CHART_TYPE.AREA,
        "scatter": XL_CHART_TYPE.XY_SCATTER,
    }
    blank = prs.slide_layouts[6] if len(prs.slide_layouts) > 6 else prs.slide_layouts[-1]
    for ch in charts[:12]:
        slide = prs.slides.add_slide(blank)
        # Title
        title_box = slide.shapes.add_textbox(Inches(0.5), Inches(0.2), Inches(9), Inches(0.5))
        tp = title_box.text_frame.paragraphs[0]
        tp.text = (ch.title or ch.chart_type or "Chart")[:120]
        tp.font.size = Pt(18)
        tp.font.bold = True

        cats = [str(c) for c in (ch.categories or [])] or ["A", "B"]
        series_list = list(ch.series or []) or [{"name": "Series1", "values": [1, 2]}]
        data = CategoryChartData()
        data.categories = cats
        for ser in series_list[:8]:
            if not isinstance(ser, dict):
                continue
            name = str(ser.get("name") or "S")
            raw = ser.get("values") if "values" in ser else ser.get("data")
            vals: list[float] = []
            if isinstance(raw, (list, tuple)):
                for v in raw:
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        vals.append(0.0)
            while len(vals) < len(cats):
                vals.append(0.0)
            data.add_series(name[:80], tuple(vals[: len(cats)]))

        xl_type = type_map.get((ch.chart_type or "bar").lower(), XL_CHART_TYPE.COLUMN_CLUSTERED)
        slide.shapes.add_chart(
            xl_type, Inches(0.5), Inches(0.9), Inches(9), Inches(5.5), data
        )


def _add_smartart_slide(prs: Presentation, arts: list[SmartArt]) -> None:
    lines: list[str] = []
    for art in arts:
        lines.append(art.plain_text())
        for it in (art.items or [])[:12]:
            lines.append(f"  • {it}")
    _add_meta_textbox_slide(prs, "SmartArt", lines, color=RGBColor(0x5B, 0x21, 0xB6))


def _add_ole_slide(prs: Presentation, oles: list[OleObject]) -> None:
    lines: list[str] = []
    for ole in oles:
        nbytes = len(ole.data or b"")
        extra = f" ({nbytes} bytes)" if nbytes else ""
        lines.append(f"{ole.plain_text()}{extra}")
    _add_meta_textbox_slide(prs, "OLE objects", lines, color=RGBColor(0x9A, 0x34, 0x12))


def _apply_speaker_notes(prs: Presentation, model: DocumentModel) -> None:
    """Write SpeakerNote nodes onto notesSlide of content slides (skip title = index 0)."""
    notes = [n for n in model.nodes if isinstance(n, SpeakerNote)]
    if not notes:
        return
    # Map 1-based content slide index → note text (title slide is prs.slides[0])
    by_idx: dict[int, list[str]] = {}
    for note in notes:
        idx = max(1, int(note.slide_index or 1))
        text = (note.text or "").strip()
        if not text:
            continue
        by_idx.setdefault(idx, []).append(text)
    for content_idx, texts in by_idx.items():
        slide_pos = content_idx  # title at 0; content slide 1 → slides[1]
        if slide_pos >= len(prs.slides):
            slide_pos = len(prs.slides) - 1
        if slide_pos < 1:
            slide_pos = 1 if len(prs.slides) > 1 else 0
        slide = prs.slides[slide_pos]
        try:
            notes_slide = slide.notes_slide
            tf = notes_slide.notes_text_frame
            tf.text = "\n\n".join(texts)[:4000]
        except Exception:
            continue


def _add_footnotes_slide(prs: Presentation, footnotes: list[Footnote]) -> None:
    layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = "Footnotes"
    left = Inches(0.7)
    top = Inches(1.55)
    width = Inches(12.0)
    for fn in footnotes[:24]:
        height = Inches(0.5)
        box = slide.shapes.add_textbox(left, top, width, height)
        tf = box.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = f"[^{fn.id}]: {fn.text}"[:500]
        p.font.size = Pt(12)
        top += height + Inches(0.06)


def _add_animations_slide(prs: Presentation, anims: list) -> None:
    """Speaker-notes style summary of imported animation XML (no playback)."""
    layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = "Animation metadata"
    left = Inches(0.7)
    top = Inches(1.55)
    width = Inches(12.0)
    for raw in anims[:20]:
        if not isinstance(raw, dict):
            continue
        slide_n = raw.get("slide", "?")
        effects = raw.get("effects") or []
        eff = ", ".join(str(e) for e in effects[:5]) if effects else "timing nodes"
        line = f"Slide {slide_n}: {raw.get('node_count', 0)} nodes — {eff}"
        box = slide.shapes.add_textbox(left, top, width, Inches(0.45))
        box.text_frame.text = line[:500]
        for p in box.text_frame.paragraphs:
            p.font.size = Pt(12)
        top += Inches(0.5)


def _add_title_slide(prs: Presentation, title: str) -> None:
    layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = title[:200]
    # Leave the subtitle placeholder empty. A stock "AduPort" line came
    # back as a body paragraph and crowded out the real H1.


def _add_content_slide(prs: Presentation, title: str, nodes: list) -> None:
    layout = prs.slide_layouts[1]
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = title[:200]

    # Prefer free textboxes for body so tables/images can interleave (multi-frame layout).
    try:
        body = slide.placeholders[1]
        body.text_frame.clear()
    except Exception:
        pass

    cursor_top = Inches(1.55)
    left = Inches(0.7)
    usable_width = Inches(12.0)
    text_buf: list[tuple[str, int, object]] = []

    def _flush_textboxes() -> None:
        nonlocal cursor_top, text_buf
        if not text_buf:
            return
        # One textbox per contiguous text run (max ~6 lines per box for readability)
        chunk: list[tuple[str, int, object]] = []
        for item in text_buf:
            chunk.append(item)
            if len(chunk) >= 6:
                cursor_top = _add_textbox_block(slide, chunk, left, cursor_top, usable_width)
                chunk = []
        if chunk:
            cursor_top = _add_textbox_block(slide, chunk, left, cursor_top, usable_width)
        text_buf = []

    for node in nodes:
        if isinstance(node, Heading) and node.plain_text() == title:
            continue

        if isinstance(node, Table):
            _flush_textboxes()
            cursor_top = _add_table_shape(slide, node, left, cursor_top, usable_width)
            continue

        if isinstance(node, TextBox):
            _flush_textboxes()
            cursor_top = _add_inline_textbox_shape(slide, node, left, cursor_top, usable_width)
            continue

        if isinstance(node, Image) and node.data:
            _flush_textboxes()
            cursor_top = _add_image_shape(slide, node, left, cursor_top, usable_width)
            continue

        if isinstance(node, ListItem):
            plain_lines = _node_lines(node, marker=False)
            if plain_lines and _append_body_placeholder(
                slide, plain_lines[0][0], plain_lines[0][1]
            ):
                for line, level in plain_lines[1:]:
                    if not _append_body_placeholder(slide, line, level):
                        text_buf.append((line, level, node))
            else:
                for line, level in _node_lines(node, marker=True):
                    text_buf.append((line, level, node))
            continue

        if isinstance(node, (CodeBlock, MathBlock)):
            _flush_textboxes()
            code_lines = [(line, level, node) for line, level in _node_lines(node)]
            if code_lines:
                cursor_top = _add_textbox_block(
                    slide, code_lines, left, cursor_top, usable_width
                )
            continue

        for line, level in _node_lines(node):
            text_buf.append((line, level, node))

    _flush_textboxes()


def _append_body_placeholder(slide, line: str, level: int) -> bool:
    """Put list lines in the content layout's body placeholder."""
    try:
        body = slide.placeholders[1]
        frame = body.text_frame
        paragraph = frame.paragraphs[0]
        if paragraph.text:
            paragraph = frame.add_paragraph()
        paragraph.text = line
        paragraph.level = max(0, min(int(level or 0), 8))
        return True
    except Exception:
        return False


def _add_textbox_block(slide, lines: list[tuple[str, int, object]], left, top, width) -> Emu:
    """Emit textboxes with real ``a:rPr`` bold/italic runs (v6/T2-#9 / D-11).

    Previously dumped ``render_rich_visible`` markdown markers (``**bold**``)
    as literal slide text.
    """
    from aduport.core.document_model import InlineMarkType, ensure_rich_text

    height = Inches(min(2.8, 0.28 * max(1, len(lines)) + 0.15))
    box = slide.shapes.add_textbox(left, top, width, height)
    tf = box.text_frame
    tf.word_wrap = True
    first = True
    for line, level, node in lines:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.clear()
        p.level = min(level, 4)
        # Prefer structured rich text when the source node carries marks
        rich = None
        if isinstance(node, Paragraph):
            rich = ensure_rich_text(node.text)
        elif isinstance(node, ListItem):
            rich = ensure_rich_text(node.text)
        elif isinstance(node, Heading):
            rich = ensure_rich_text(node.text)

        if rich is not None and rich.marks:
            _apply_rich_runs(p, rich)
        else:
            run = p.add_run()
            run.text = (line or "")[:500]
            run.font.size = Pt(16 if level == 0 else 14)
            if isinstance(node, Heading) and not str(line).lstrip().startswith("#"):
                run.font.bold = True
                run.font.color.rgb = RGBColor(0x1A, 0x56, 0xDB)
            if isinstance(node, (CodeBlock, MathBlock)):
                run.font.name = "Courier New"
                run.font.size = Pt(12)
        if isinstance(node, Heading) and str(line).lstrip().startswith("#"):
            pass
        elif isinstance(node, Heading) and not (rich and rich.marks):
            pass
        elif isinstance(node, Heading):
            for run in p.runs:
                run.font.bold = True
                run.font.color.rgb = RGBColor(0x1A, 0x56, 0xDB)
                run.font.size = Pt(16 if level == 0 else 14)
    return top + height + Inches(0.12)


def _apply_rich_runs(paragraph, rich) -> None:
    """Split RichText into PPTX runs with bold/italic/strike (no markdown glyphs)."""
    from aduport.core.document_model import InlineMarkType

    text = rich.text or ""
    if not text:
        run = paragraph.add_run()
        run.text = ""
        return
    boundaries = {0, len(text)}
    for mark in rich.marks or []:
        boundaries.add(max(0, min(mark.start, len(text))))
        boundaries.add(max(0, min(mark.end, len(text))))
    points = sorted(boundaries)
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        segment = text[start:end]
        active = [m for m in (rich.marks or []) if m.start <= start and m.end >= end]
        types = {m.type for m in active}
        run = paragraph.add_run()
        run.text = segment[:500]
        run.font.size = Pt(14)
        if InlineMarkType.BOLD in types:
            run.font.bold = True
        if InlineMarkType.ITALIC in types:
            run.font.italic = True
        if InlineMarkType.CODE in types:
            run.font.name = "Courier New"
        if InlineMarkType.STRIKETHROUGH in types:
            # python-pptx Font has no strikethrough API — set DrawingML attr.
            try:
                rPr = run._r.get_or_add_rPr()
                rPr.set("strike", "sngStrike")
            except Exception:
                pass
        if InlineMarkType.LINK in types:
            href = next(
                (m.href for m in active if m.type == InlineMarkType.LINK and m.href),
                None,
            )
            if href:
                try:
                    run.hyperlink.address = href
                except Exception:
                    pass


def _table_matrix(node: Table) -> list[list[str]]:
    if node.cells and table_has_span_cells(node):
        return table_flat_matrix(node)
    if node.rows:
        return [[str(c) for c in row] for row in node.rows]
    if node.cells:
        return [[(c.text if c is not None else "") for c in row] for row in node.cells]
    return [["(empty table)"]]


def _table_has_span_cells(node: Table) -> bool:
    return table_has_span_cells(node)


def _add_inline_textbox_shape(slide, box: TextBox, left, top, width) -> Emu:
    height = Inches(min(1.2, 0.28 * max(1, len((box.text or "").splitlines())) + 0.35))
    shape = slide.shapes.add_textbox(left + Inches(0.15), top, width - Inches(0.3), height)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.text = (box.text or "")[:500]
    for p in tf.paragraphs:
        p.font.size = Pt(11)
        p.font.italic = True
        if box.source:
            p.font.color.rgb = RGBColor(0x5B, 0x66, 0x75)
    return top + height + Inches(0.18)


def _add_table_shape(slide, node: Table, left, top, width) -> Emu:
    matrix = _table_matrix(node)[:_MAX_TABLE_ROWS]
    if not matrix:
        return top
    cols = min(_MAX_TABLE_COLS, max(len(r) for r in matrix))
    rows = len(matrix)
    height = Inches(min(4.5, 0.35 * rows + 0.2))
    table_shape = slide.shapes.add_table(rows, cols, left, top, width, height).table
    header_end = max(0, int(node.header_rows or 0))
    for r_i, row in enumerate(matrix):
        for c_i in range(cols):
            cell = table_shape.cell(r_i, c_i)
            cell.text = (row[c_i] if c_i < len(row) else "")[:200]
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(11)
                    if header_end > 0 and r_i < header_end:
                        run.font.bold = True
                    elif header_end == 0 and r_i == 0:
                        run.font.bold = True

    if node.cells and _table_has_span_cells(node):
        for r_idx, row in enumerate(node.cells[:rows]):
            for c_idx, meta in enumerate(row[:cols]):
                if meta.covered:
                    continue
                if r_idx >= rows or c_idx >= cols:
                    continue
                cell = table_shape.cell(r_idx, c_idx)
                if meta.col_span > 1:
                    end_c = min(c_idx + meta.col_span - 1, cols - 1)
                    if end_c > c_idx:
                        cell.merge(table_shape.cell(r_idx, end_c))
                if meta.row_span > 1:
                    end_r = min(r_idx + meta.row_span - 1, rows - 1)
                    if end_r > r_idx:
                        cell.merge(table_shape.cell(end_r, c_idx))
    return top + height + Inches(0.25)


def _add_image_shape(slide, node: Image, left, top, width) -> Emu:
    alt = node.alt_text.split("|", 1)[0] if node.alt_text else "image"
    caption = f"[Image: {alt}]"
    try:
        stream = BytesIO(node.data)
        pic_width = min(width, Inches(8))
        pic = slide.shapes.add_picture(stream, left, top, width=pic_width)
        top = top + pic.height + Inches(0.08)
    except Exception:
        pass
    # Always emit searchable alt caption (matrix / accessibility)
    box = slide.shapes.add_textbox(left, top, width, Inches(0.4))
    box.text_frame.text = caption[:500]
    return top + Inches(0.5)


def _node_lines(node, *, marker: bool = True) -> list[tuple[str, int]]:
    """Plain fallback lines — rich marks applied later in ``_add_textbox_block``.

    v6/T2-#9: do NOT inject markdown ``**`` glyphs; PPTX uses real runs.
    Body placeholders are already bullets, so ``marker=False`` writes the
    item text alone. Text boxes still get one ``- `` so the XML extract can
    see a list.
    """
    if isinstance(node, Heading):
        text = node.plain_text()
        if int(node.level or 1) >= 3:
            text = f"{'#' * min(6, int(node.level))} {text}"
        return [(text, 0)]
    if isinstance(node, Paragraph):
        return [(node.plain_text(), 0)]
    if isinstance(node, ListItem):
        from aduport.importers.list_markers import strip_redundant_list_prefix

        body = strip_redundant_list_prefix(
            node.plain_text(), ordered=bool(node.ordered and node.checked is None)
        )
        prefix = ""
        if marker:
            if node.checked is not None:
                prefix = "[x] " if node.checked else "[ ] "
            elif node.ordered:
                prefix = "1. "
            else:
                prefix = "- "
        text = f"{prefix}{body}" if prefix else body
        lines = [(text, max(0, node.level))]
        for child in getattr(node, "children", None) or []:
            if isinstance(child, Paragraph):
                lines.append((child.plain_text(), max(0, node.level) + 1))
        return lines
    if isinstance(node, Blockquote):
        from aduport.exporters.rich_visible import render_rich_visible

        out = []
        for p in node.paragraphs:
            rendered = render_rich_visible(p) if not isinstance(p, str) else p
            for line in str(rendered).splitlines() or [""]:
                out.append((f"> {line}", 0))
        return out
    if isinstance(node, HorizontalRule):
        return [("---", 0)]
    if isinstance(node, CodeBlock):
        lang = re.sub(r"[^A-Za-z0-9_+.-]", "", node.lang or "")
        lines = [(f"```{lang}", 0)]
        for raw in (node.content or "").splitlines() or [""]:
            lines.append((raw[:800], 0))
        lines.append(("```", 0))
        return lines
    if isinstance(node, MathBlock):
        if node.display:
            lines = [("```math", 0)]
            for raw in (node.content or "").splitlines() or [""]:
                lines.append((raw[:800], 0))
            lines.append(("```", 0))
            return lines
        return [(f"${node.content}$", 0)]
    if isinstance(node, Footnote):
        return [(f"[^{node.id}]: {node.text}", 0)]
    if isinstance(node, Comment):
        anchor = f" on {node.anchor}" if node.anchor else ""
        return [(f"[{node.author}{anchor}] {node.text}", 0)]
    if isinstance(node, Revision):
        tag = "ins" if node.change == "insert" else "del"
        return [(f"[{tag} {node.author}] {node.text}", 0)]
    if isinstance(node, ContentControl):
        label = node.alias or node.tag or "control"
        return [(f"[{label}] {node.text}", 0)]
    if isinstance(node, TextBox):
        src = f" ({node.source})" if node.source else ""
        return [(f"[Text box{src}] {node.text}", 0)]
    if isinstance(node, PivotTable):
        fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
        return [(f"[Pivot {node.name}] {fields}", 0)]
    if isinstance(node, Table):
        out = []
        for row in _table_matrix(node)[:12]:
            out.append((" | ".join(row), 0))
        return out or [("(empty table)", 0)]
    if isinstance(node, Image):
        alt = node.alt_text.split("|", 1)[0] if node.alt_text else "image"
        return [(f"[Image: {alt}]", 0)]
    if isinstance(node, HtmlBlock):
        text = re.sub(r"<[^>]+>", "", node.content).strip()
        return [(text[:400], 0)] if text else []
    if isinstance(node, SmartArt):
        lines = [(node.plain_text(), 0)]
        for it in (node.items or [])[:20]:
            lines.append((f"• {it}", 1))
        return lines
    if isinstance(node, OleObject):
        return [(node.plain_text(), 0)]
    if isinstance(node, Chart):
        return [(node.plain_text(), 0)]
    if isinstance(node, SpeakerNote):
        return [(f"[SpeakerNote s{node.slide_index}] {node.text}", 0)]
    return []
