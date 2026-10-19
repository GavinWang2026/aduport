"""ODP (OpenDocument Presentation) exporter — slides, notes, embedded images."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

from aduport.core.document_model import (
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
from aduport.exporters.export_warnings import placeholder_line, warn_export
from aduport.exporters.odf_common import (
    image_ext,
    odf_manifest_xml,
    odf_meta_xml,
    odf_styles_xml,
)
from aduport.exporters.rich_visible import render_rich_visible

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
        raw_title = (title[:80] or f"Slide {i}").strip() or f"Slide {i}"
        name = escape(raw_title)
        # draw:name is an attribute, so the title is also the first visible
        # paragraph. A code block stays one paragraph so its line breaks survive.
        # A table is table:table, not pipe text inside that paragraph.
        chunks = [f"<text:p>{name}</text:p>"]
        for line in lines[:40]:
            if isinstance(line, list):
                chunks.append(_odp_table_xml(line))
                continue
            text = str(line)
            if not text.strip() or text.strip() == raw_title:
                continue
            chunks.append(f"<text:p>{escape(text[:800])}</text:p>")
        paras = "".join(chunks)
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
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
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

    def flush(*, force: bool = False) -> None:
        nonlocal current, current_title, current_imgs, opened
        if current or current_imgs or force or not pages:
            pages.append((current_title, list(current) or [""], list(current_imgs)))
        current = []
        current_imgs = []
        opened = False

    opened = False
    for node in model.nodes:
        if isinstance(node, SpeakerNote):
            continue
        if isinstance(node, Image):
            current_imgs.append(node)
            alt = (node.alt_text or "image").split("|", 1)[0]
            current.append(f"[Image: {alt}]")
            continue
        if isinstance(node, Table):
            matrix = _table_matrix(node)
            if matrix:
                current.append(matrix)
                opened = True
            continue
        if isinstance(node, Heading) and node.level <= 2:
            # A following H1/H2 starts a new slide even when the previous
            # slide has no body yet; otherwise the earlier title is overwritten.
            if opened:
                flush(force=True)
            current_title = node.plain_text()[:80] or current_title
            opened = True
            continue
        line = _node_line(node)
        if line is None:
            continue
        current.append(line)
        if len(current) >= 14:
            flush()
            current_title = f"Slide {len(pages) + 1}"
    # A heading-only document never fills `current`, so the slide title
    # (draw:name) would otherwise be discarded for an empty "Slide 1".
    if opened or current or current_imgs or not pages:
        flush(force=True)
    return pages


def _table_matrix(node: Table) -> list[list[str]]:
    """Prefer ``rows``. Some importers fill only the span-aware ``cells`` grid."""
    if node.rows:
        return [[str(cell) for cell in row] for row in node.rows]
    if not node.cells:
        return []
    matrix: list[list[str]] = []
    for row in node.cells:
        matrix.append([str(getattr(cell, "text", cell) or "") for cell in row])
    return matrix


def _odp_table_xml(rows: list[list[str]]) -> str:
    """Native presentation table. Cell text is visible; pipes are not."""
    width = max((len(row) for row in rows), default=0)
    if width <= 0:
        return ""
    columns = "".join("<table:table-column/>" for _ in range(width))
    body: list[str] = []
    for row in rows:
        cells: list[str] = []
        for idx in range(width):
            raw = row[idx] if idx < len(row) else ""
            text = escape(str(raw).replace("\n", " ")[:2000])
            cells.append(
                '<table:table-cell office:value-type="string">'
                f"<text:p>{text}</text:p></table:table-cell>"
            )
        body.append("<table:table-row>" + "".join(cells) + "</table:table-row>")
    return "<table:table>" + columns + "".join(body) + "</table:table>"


def _node_line(node) -> str | None:
    if isinstance(node, Paragraph):
        return render_rich_visible(node.text)
    if isinstance(node, ListItem):
        if node.checked is not None:
            mark = "[x] " if node.checked else "[ ] "
        else:
            mark = "- " if not node.ordered else "1. "
        return mark + render_rich_visible(node.text)
    if isinstance(node, Footnote):
        return f"[^{node.id}]: {node.text}"
    if isinstance(node, CodeBlock):
        body = node.content or ""
        return f"```{node.lang or ''}\n{body}\n```"
    if isinstance(node, MathBlock):
        return node.content[:200]
    if isinstance(node, Blockquote):
        parts = []
        for p in node.paragraphs:
            rendered = render_rich_visible(p) if not isinstance(p, str) else p
            for line in str(rendered).splitlines() or [""]:
                parts.append(f"> {line}")
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
