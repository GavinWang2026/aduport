"""Minimal ODT (OpenDocument Text) exporter."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

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
    Paragraph,
    PivotTable,
    Revision,
    SpeakerNote,
    Table,
    TableCell,
    TextBox,
)
from aduport.exporters.export_warnings import node_type_name, placeholder_line, warn_export
from aduport.exporters.rich_visible import list_item_children


def _odt_inline(value) -> str:
    """Escape rich text with markdown-visible marks and real ``text:a`` hyperlinks."""
    from aduport.core.document_model import InlineMarkType, ensure_rich_text

    rich = ensure_rich_text(value)
    if not rich.marks:
        return escape(rich.text)

    boundaries = {0, len(rich.text)}
    for mark in rich.marks:
        boundaries.add(max(0, min(mark.start, len(rich.text))))
        boundaries.add(max(0, min(mark.end, len(rich.text))))
    points = sorted(boundaries)
    parts: list[str] = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        segment = escape(rich.text[start:end])
        active = [m for m in rich.marks if m.start <= start and m.end >= end]
        types = {m.type for m in active}
        if InlineMarkType.BOLD in types:
            segment = f"**{segment}**"
        if InlineMarkType.ITALIC in types:
            segment = f"*{segment}*"
        if InlineMarkType.CODE in types:
            segment = f"`{segment}`"
        if InlineMarkType.STRIKETHROUGH in types:
            segment = f"~~{segment}~~"
        if InlineMarkType.LINK in types:
            href = next((m.href for m in active if m.type == InlineMarkType.LINK), "") or ""
            href_esc = escape(href, {"\"": "&quot;"})
            segment = f'<text:a xlink:href="{href_esc}">{segment}</text:a>'
        parts.append(segment)
    return "".join(parts)


def _odt_lines(value: str) -> str:
    """Escape text and keep line breaks as ``text:line-break`` (not markdown fences)."""
    parts: list[str] = []
    for i, line in enumerate((value or "").split("\n")):
        if i:
            parts.append("<text:line-break/>")
        parts.append(escape(line))
    return "".join(parts)


def _image_ext(mime: str) -> str:
    m = (mime or "").lower()
    if "jpeg" in m or "jpg" in m:
        return ".jpg"
    if "gif" in m:
        return ".gif"
    if "svg" in m:
        return ".svg"
    return ".png"


def _mathml_block(tex: str) -> str:
    safe = escape((tex or "")[:800])
    return (
        "<math:math>"
        "<math:semantics>"
        f"<math:mrow><math:mtext>{safe}</math:mtext></math:mrow>"
        f'<math:annotation encoding="application/x-tex">{safe}</math:annotation>'
        "</math:semantics>"
        "</math:math>"
    )


def _odt_list_block(items: list[ListItem]) -> str:
    """Real nested text:list so import can rebuild level and ordered."""

    def emit(group: list[ListItem], base: int) -> str:
        if not group:
            return ""
        ordered = bool(group[0].ordered)
        style = "AduPortOrdered" if ordered else "AduPortUnordered"
        chunks = [f'<text:list text:style-name="{style}">']
        index = 0
        while index < len(group):
            node = group[index]
            chunks.append("<text:list-item>")
            prefix = ""
            if node.checked is True:
                prefix = "[x] "
            elif node.checked is False:
                prefix = "[ ] "
            chunks.append(f"<text:p>{escape(prefix)}{_odt_inline(node.text)}</text:p>")
            for is_code, text in list_item_children(node):
                if not text:
                    continue
                if is_code:
                    for line in text.splitlines() or [""]:
                        chunks.append(f"<text:p>{escape(line)}</text:p>")
                else:
                    chunks.append(f"<text:p>{escape(text)}</text:p>")
            index += 1
            nested: list[ListItem] = []
            while index < len(group) and int(group[index].level or 0) > base:
                nested.append(group[index])
                index += 1
            if nested:
                chunks.append(emit(nested, base + 1))
            chunks.append("</text:list-item>")
        chunks.append("</text:list>")
        return "".join(chunks)

    base = min(int(node.level or 0) for node in items) if items else 0
    return emit(items, base)


def export_odt(model: DocumentModel) -> bytes:
    body_parts: list[str] = []
    media: list[tuple[str, bytes, str]] = []  # href, data, mime
    seq = list(model.nodes)
    index = 0
    while index < len(seq):
        node = seq[index]
        if isinstance(node, ListItem):
            ordered = bool(node.ordered)
            group: list[ListItem] = []
            while (
                index < len(seq)
                and isinstance(seq[index], ListItem)
                and bool(seq[index].ordered) == ordered
            ):
                group.append(seq[index])
                index += 1
            body_parts.append(_odt_list_block(group))
            continue
        index += 1
        if isinstance(node, Heading):
            lv = max(1, min(6, node.level))
            body_parts.append(
                f'<text:h text:outline-level="{lv}">{escape(node.plain_text())}</text:h>'
            )
        elif isinstance(node, Paragraph):
            body_parts.append(f"<text:p>{_odt_inline(node.text)}</text:p>")
        elif isinstance(node, Table):
            body_parts.append(_table_xml(node))
        elif isinstance(node, HorizontalRule):
            body_parts.append("<text:p>---</text:p>")
        elif isinstance(node, CodeBlock):
            lang = escape(node.lang or "", {"\"": "&quot;"})
            body = _odt_lines(node.content or "")
            body_parts.append(
                f'<text:p text:style-name="AduPortCode" aduport:lang="{lang}">{body}</text:p>'
            )
        elif isinstance(node, Blockquote):
            for p in node.paragraphs:
                body_parts.append(
                    f'<text:p text:style-name="AduPortQuote">{_odt_inline(p)}</text:p>'
                )
        elif isinstance(node, Image):
            alt = (node.alt_text or "image").split("|", 1)[0]
            if node.data:
                idx = len(media) + 1
                ext = _image_ext(node.mime_type)
                href = f"Pictures/image{idx}{ext}"
                mime = node.mime_type or "image/png"
                media.append((href, node.data, mime))
                body_parts.append(
                    "<text:p>"
                    f'<draw:frame draw:name="img{idx}" text:anchor-type="paragraph" '
                    'svg:width="12cm" svg:height="9cm">'
                    f'<draw:image xlink:href="{escape(href)}" xlink:type="simple" '
                    'xlink:show="embed" xlink:actuate="onLoad"/>'
                    "</draw:frame>"
                    f"{escape(alt)}"
                    "</text:p>"
                )
            else:
                warn_export(model, "odt: Image missing binary → placeholder")
                body_parts.append(
                    f"<text:p>{escape(placeholder_line('Image', alt))}</text:p>"
                )
        elif isinstance(node, Chart):
            body_parts.append(f"<text:p>{escape(node.plain_text())}</text:p>")
        elif isinstance(node, SpeakerNote):
            body_parts.append(
                f"<text:p>{escape(f'[SpeakerNote s{node.slide_index}] {node.text}')}</text:p>"
            )
        elif isinstance(node, MathBlock):
            # Basic MathML wrapper with TeX annotation (not a full ODF Formula object).
            warn_export(model, "odt: MathBlock → MathML annotation (TeX encoding)")
            body_parts.append(f"<text:p>{_mathml_block(node.content)}</text:p>")
        elif isinstance(node, HtmlBlock):
            warn_export(model, "odt: HtmlBlock stripped to text")
            body_parts.append(
                f"<text:p>{escape(placeholder_line('HtmlBlock', (node.content or '')[:200]))}</text:p>"
            )
        elif isinstance(node, Comment):
            anchor = f" ({node.anchor})" if node.anchor else ""
            author = node.author or "reviewer"
            body_parts.append(
                f"<text:p>{escape(f'[Comment {author}{anchor}] {node.text}')}</text:p>"
            )
        elif isinstance(node, Footnote):
            body_parts.append(f"<text:p>{escape(f'[^{node.id}]: {node.text}')}</text:p>")
        elif isinstance(node, Revision):
            tag = "ins" if node.change == "insert" else "del"
            body_parts.append(
                f"<text:p>{escape(f'[{tag} {node.author}] {node.text}')}</text:p>"
            )
        elif isinstance(node, ContentControl):
            label = node.alias or node.tag or "control"
            ctype = node.control_type or "richText"
            body_parts.append(
                f"<text:p>{escape(f'[Control:{label}|{ctype}] {node.text}')}</text:p>"
            )
        elif isinstance(node, TextBox):
            src = f" ({node.source})" if node.source else ""
            body_parts.append(f"<text:p>{escape(f'[TextBox{src}] {node.text}')}</text:p>")
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            body_parts.append(
                f"<text:p>{escape(f'[PivotTable {node.name}] {fields}')}</text:p>"
            )
        else:
            text = getattr(node, "plain_text", lambda: "")()
            if text:
                body_parts.append(f"<text:p>{escape(str(text))}</text:p>")
            else:
                kind = node_type_name(node)
                warn_export(model, f"odt: unhandled node {kind} → placeholder")
                body_parts.append(
                    f"<text:p>{escape(placeholder_line(kind))}</text:p>"
                )

    if not body_parts:
        body_parts.append("<text:p></text:p>")

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink"
 xmlns:math="http://www.w3.org/1998/Math/MathML"
 xmlns:aduport="urn:aduport:odt"
 office:version="1.2">
 <office:body>
  <office:text>
   {"".join(body_parts)}
  </office:text>
 </office:body>
</office:document-content>
"""
    manifest_entries = [
        '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>',
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>',
    ]
    if media:
        manifest_entries.append(
            '<manifest:file-entry manifest:full-path="Pictures/" manifest:media-type="application/binary"/>'
        )
        for href, _data, mime in media:
            manifest_entries.append(
                f'<manifest:file-entry manifest:full-path="{escape(href)}" '
                f'manifest:media-type="{escape(mime)}"/>'
            )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">\n'
        + "\n".join(f" {e}" for e in manifest_entries)
        + "\n</manifest:manifest>\n"
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "mimetype",
            "application/vnd.oasis.opendocument.text",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/manifest.xml", manifest)
        zf.writestr("content.xml", content)
        for href, data, _mime in media:
            zf.writestr(href, data)
    return buf.getvalue()


def _table_xml(table: Table) -> str:
    if table.cells and _table_has_span_cells(table):
        return _table_xml_spans(table)
    rows_xml: list[str] = []
    for row in table.rows:
        cells = "".join(
            f"<table:table-cell office:value-type=\"string\"><text:p>{escape(str(c))}</text:p></table:table-cell>"
            for c in row
        )
        rows_xml.append(f"<table:table-row>{cells}</table:table-row>")
    return f'<table:table table:name="Table1">{"".join(rows_xml)}</table:table>'


def _table_has_span_cells(table: Table) -> bool:
    for row in table.cells:
        for cell in row:
            if cell.covered or cell.row_span > 1 or cell.col_span > 1:
                return True
    return False


def _odt_cell_xml(cell: TableCell) -> str:
    if cell.covered:
        return "<table:covered-table-cell/>"
    attrs = ['office:value-type="string"']
    if cell.col_span > 1:
        attrs.append(f'table:number-columns-spanned="{cell.col_span}"')
    if cell.row_span > 1:
        attrs.append(f'table:number-rows-spanned="{cell.row_span}"')
    attr_str = " ".join(attrs)
    return (
        f"<table:table-cell {attr_str}>"
        f"<text:p>{escape(cell.text)}</text:p></table:table-cell>"
    )


def _table_xml_spans(table: Table) -> str:
    rows_xml: list[str] = []
    for row in table.cells:
        cells = "".join(_odt_cell_xml(cell) for cell in row)
        rows_xml.append(f"<table:table-row>{cells}</table:table-row>")
    name = escape(table.name or "Table1")
    return f'<table:table table:name="{name}">{"".join(rows_xml)}</table:table>'
