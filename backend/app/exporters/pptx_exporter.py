"""Export DocumentModel to PPTX using python-pptx."""

from __future__ import annotations

from io import BytesIO

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

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

# ~ title slide + content slides; pack dense paragraphs per slide
_MAX_CHARS_PER_SLIDE = 900


def export_pptx(model: DocumentModel) -> bytes:
    prs = Presentation()
    # Widescreen 16:9
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    title = model.title or _first_heading(model) or "Document"
    _add_title_slide(prs, title)

    # Bucket nodes into slides by size / heading breaks
    buckets: list[list] = []
    current: list = []
    chars = 0
    for node in model.nodes:
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
        slide_title = _bucket_title(bucket, i)
        _add_content_slide(prs, slide_title, bucket)

    buf = BytesIO()
    prs.save(buf)
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
        return sum(len(str(c)) for row in node.rows for c in row) + 16
    if isinstance(node, MathBlock):
        return len(node.content) + 4
    if isinstance(node, Footnote):
        return len(node.text) + 8
    if isinstance(node, Image):
        return 40
    return 10


def _bucket_title(bucket: list, index: int) -> str:
    for n in bucket:
        if isinstance(n, Heading):
            return n.plain_text()[:80]
    return f"Slide {index}"


def _add_title_slide(prs: Presentation, title: str) -> None:
    layout = prs.slide_layouts[0]  # title
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = title[:200]
    if slide.placeholders and len(slide.placeholders) > 1:
        try:
            slide.placeholders[1].text = "md-bridge"
        except Exception:
            pass


def _add_content_slide(prs: Presentation, title: str, nodes: list) -> None:
    layout = prs.slide_layouts[1]  # title and content
    slide = prs.slides.add_slide(layout)
    slide.shapes.title.text = title[:200]
    body = slide.placeholders[1]
    tf = body.text_frame
    tf.clear()

    first = True
    for node in nodes:
        if isinstance(node, Heading) and node.plain_text() == title:
            continue
        lines = _node_lines(node)
        for line, level in lines:
            if first:
                p = tf.paragraphs[0]
                first = False
            else:
                p = tf.add_paragraph()
            p.text = line[:500]
            p.level = min(level, 4)
            p.font.size = Pt(16 if level == 0 else 14)
            if isinstance(node, Heading):
                p.font.bold = True
                p.font.color.rgb = RGBColor(0x1A, 0x56, 0xDB)
            if isinstance(node, CodeBlock) or isinstance(node, MathBlock):
                p.font.name = "Courier New"
                p.font.size = Pt(12)


def _node_lines(node) -> list[tuple[str, int]]:
    if isinstance(node, Heading):
        return [(node.plain_text(), 0)]
    if isinstance(node, Paragraph):
        return [(ensure_rich_text(node.text).text, 0)]
    if isinstance(node, ListItem):
        prefix = ""
        if node.checked is not None:
            prefix = "☑ " if node.checked else "☐ "
        text = prefix + ensure_rich_text(node.text).text
        lines = [(text, max(0, node.level))]
        for child in getattr(node, "children", None) or []:
            if isinstance(child, Paragraph):
                lines.append((ensure_rich_text(child.text).text, max(0, node.level) + 1))
        return lines
    if isinstance(node, Blockquote):
        return [("「" + ensure_rich_text(p).text + "」", 0) for p in node.paragraphs]
    if isinstance(node, HorizontalRule):
        return [("—" * 20, 0)]
    if isinstance(node, CodeBlock):
        return [(node.content[:800], 0)]
    if isinstance(node, MathBlock):
        return [(node.content, 0)]
    if isinstance(node, Footnote):
        return [(f"[^{node.id}] {node.text}", 0)]
    if isinstance(node, Table):
        out = []
        for row in node.rows[:12]:
            out.append((" | ".join(str(c) for c in row), 0))
        return out or [("(empty table)", 0)]
    if isinstance(node, Image):
        alt = node.alt_text.split("|", 1)[0] if node.alt_text else "image"
        return [(f"[Image: {alt}]", 0)]
    if isinstance(node, HtmlBlock):
        import re

        text = re.sub(r"<[^>]+>", "", node.content).strip()
        return [(text[:400], 0)] if text else []
    return []
