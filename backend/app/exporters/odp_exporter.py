"""ODP (OpenDocument Presentation) exporter — slides, notes, embedded images."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
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
)
from app.exporters.export_warnings import placeholder_line, warn_export
from app.exporters.odf_common import (
    image_ext,
    odf_manifest_xml,
    odf_meta_xml,
    odf_styles_xml,
)
from app.exporters.rich_visible import render_rich_visible

_MIME = "application/vnd.oasis.opendocument.presentation"


def export_odp(model: DocumentModel) -> bytes:
    """Export document as a multi-page ODP with notes + optional Pictures/."""
    pages = _bucket_pages(model)
    if not pages:
        pages = [("Slide 1", [""], [])]

    media: list[tuple[str, bytes, str]] = []
    page_xml: list[str] = []
    notes_by_slide = _notes_map(model)

    for i, (title, lines, imgs) in enumerate(pages[:50], start=1):
        name = escape(title[:80] or f"Slide {i}")
        paras = "".join(f"<text:p>{escape(line[:800])}</text:p>" for line in lines[:40])
        frames = (
            f'<draw:frame svg:width="25cm" svg:height="14cm" svg:x="1cm" svg:y="1cm">'
            f"<draw:text-box>{paras or '<text:p></text:p>'}</draw:text-box>"
            f"</draw:frame>"
        )
        for img in imgs[:4]:
            alt = (img.alt_text or "image").split("|", 1)[0]
            if not img.data:
                warn_export(model, "odp: Image missing binary → placeholder")
                frames += f"<draw:frame svg:width=\"10cm\" svg:height=\"1cm\" svg:x=\"1cm\" svg:y=\"15.5cm\">"
                frames += f"<draw:text-box><text:p>{escape(placeholder_line('Image', alt))}</text:p></draw:text-box></draw:frame>"
                continue
            idx = len(media) + 1
            ext = image_ext(img.mime_type)
            href = f"Pictures/image{idx}{ext}"
            mime = img.mime_type or "image/png"
            media.append((href, img.data, mime))
            frames += (
                f'<draw:frame draw:name="img{idx}" svg:width="8cm" svg:height="6cm" '
                f'svg:x="{1 + (idx % 2) * 9}cm" svg:y="15cm">'
                f'<draw:image xlink:href="{escape(href)}" xlink:type="simple" '
                f'xlink:show="embed" xlink:actuate="onLoad"/>'
                f"</draw:frame>"
            )

        note_text = notes_by_slide.get(i) or notes_by_slide.get(0)
        notes_block = ""
        if note_text:
            notes_block = (
                "<presentation:notes>"
                '<draw:page draw:name="Notes">'
                '<draw:frame svg:width="20cm" svg:height="8cm" svg:x="1cm" svg:y="1cm">'
                f"<draw:text-box><text:p>{escape(note_text[:2000])}</text:p></draw:text-box>"
                "</draw:frame></draw:page></presentation:notes>"
            )

        page_xml.append(
            f'<draw:page draw:name="{name}" draw:style-name="dp1">'
            f"{frames}{notes_block}</draw:page>"
        )

    # Notes with slide_index beyond exported pages → dedicated page
    leftover = [
        n
        for n in model.nodes
        if isinstance(n, SpeakerNote) and int(n.slide_index or 0) > len(pages)
    ]
    if leftover:
        warn_export(model, f"odp: {len(leftover)} SpeakerNote → SpeakersNotes page")
        note_lines = [f"[s{n.slide_index}] {n.text}" for n in leftover[:30]]
        paras = "".join(f"<text:p>{escape(t[:800])}</text:p>" for t in note_lines)
        page_xml.append(
            '<draw:page draw:name="SpeakerNotes" draw:style-name="dp1">'
            '<draw:frame svg:width="25cm" svg:height="16cm" svg:x="1cm" svg:y="1cm">'
            f"<draw:text-box>{paras}</draw:text-box>"
            "</draw:frame></draw:page>"
        )

    arts = [n for n in model.nodes if isinstance(n, (SmartArt, OleObject))]
    if arts:
        warn_export(model, f"odp: {len(arts)} SmartArt/Ole → caption lines on body slides")

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink"
 office:version="1.2">
 <office:body>
  <office:presentation>
   {"".join(page_xml)}
  </office:presentation>
 </office:body>
</office:document-content>
"""
    extra = []
    if media:
        extra.append(
            '<manifest:file-entry manifest:full-path="Pictures/" '
            'manifest:media-type="application/binary"/>'
        )
        for href, _data, mime in media:
            extra.append(
                f'<manifest:file-entry manifest:full-path="{escape(href)}" '
                f'manifest:media-type="{escape(mime)}"/>'
            )

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", _MIME, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/manifest.xml", odf_manifest_xml(_MIME, extra_entries=extra))
        zf.writestr("content.xml", content)
        zf.writestr("meta.xml", odf_meta_xml(title=model.title or "odp"))
        zf.writestr("styles.xml", odf_styles_xml())
        for href, data, _mime in media:
            zf.writestr(href, data)
    return buf.getvalue()


def _notes_map(model: DocumentModel) -> dict[int, str]:
    out: dict[int, str] = {}
    for n in model.nodes:
        if not isinstance(n, SpeakerNote):
            continue
        idx = int(n.slide_index or 0)
        key = idx if idx > 0 else 1
        prev = out.get(key, "")
        out[key] = (prev + "\n" + n.text).strip() if prev else n.text
    return out


def _bucket_pages(
    model: DocumentModel,
) -> list[tuple[str, list[str], list[Image]]]:
    pages: list[tuple[str, list[str], list[Image]]] = []
    current_title = model.title or "Slide 1"
    current: list[str] = []
    current_imgs: list[Image] = []

    def flush() -> None:
        nonlocal current, current_title, current_imgs
        if current or current_imgs or not pages:
            pages.append((current_title, list(current) or [""], list(current_imgs)))
        current = []
        current_imgs = []

    for node in model.nodes:
        if isinstance(node, SpeakerNote):
            continue
        if isinstance(node, Image):
            current_imgs.append(node)
            alt = (node.alt_text or "image").split("|", 1)[0]
            current.append(f"[Image: {alt}]")
            continue
        if isinstance(node, Heading) and node.level <= 2:
            if current or current_imgs:
                flush()
            current_title = node.plain_text()[:80] or current_title
            current.append(node.plain_text())
            continue
        line = _node_line(node)
        if line is None:
            continue
        current.append(line)
        if len(current) >= 14:
            flush()
            current_title = f"Slide {len(pages) + 1}"
    if current or current_imgs:
        flush()
    return pages


def _node_line(node) -> str | None:
    if isinstance(node, Paragraph):
        return render_rich_visible(node.text)
    if isinstance(node, ListItem):
        if node.checked is not None:
            mark = "[x] " if node.checked else "[ ] "
        else:
            mark = "• " if not node.ordered else "1. "
        return mark + render_rich_visible(node.text)
    if isinstance(node, Table):
        rows = node.rows or []
        if not rows:
            return None
        parts = [" | ".join(str(c) for c in rows[0][:8])]
        if len(rows) > 1:
            parts.append(" | ".join(str(c) for c in rows[1][:8]))
        return " · ".join(parts)
    if isinstance(node, Footnote):
        return f"[^{node.id}]: {node.text}"
    if isinstance(node, CodeBlock):
        return node.content[:400]
    if isinstance(node, MathBlock):
        return node.content[:200]
    if isinstance(node, Blockquote):
        parts = []
        for p in node.paragraphs:
            parts.append(render_rich_visible(p) if not isinstance(p, str) else p)
        return "\n".join(parts)
    if isinstance(node, HtmlBlock):
        return placeholder_line("HtmlBlock", (node.content or "")[:200])
    if isinstance(node, Comment):
        return f"[Comment {node.author}] {node.text}"
    if isinstance(node, Revision):
        tag = "ins" if node.change == "insert" else "del"
        return f"[{tag} {node.author}] {node.text}"
    if isinstance(node, TextBox):
        return f"[TextBox] {node.text}"
    if isinstance(node, PivotTable):
        fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
        return f"[PivotTable {node.name}] {fields}"
    if isinstance(node, HorizontalRule):
        return "---"
    if isinstance(node, (SmartArt, OleObject)):
        return node.plain_text()
    plain = getattr(node, "plain_text", None)
    if callable(plain):
        t = plain()
        return str(t) if t else None
    return None
