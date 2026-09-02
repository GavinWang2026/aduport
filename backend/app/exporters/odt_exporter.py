"""Minimal ODT (OpenDocument Text) exporter."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    ListItem,
    Paragraph,
    PivotTable,
    Revision,
    Table,
    TableCell,
    TextBox,
    ensure_rich_text,
)


def export_odt(model: DocumentModel) -> bytes:
    body_parts: list[str] = []
    for node in model.nodes:
        if isinstance(node, Heading):
            lv = max(1, min(6, node.level))
            body_parts.append(
                f'<text:h text:outline-level="{lv}">{escape(node.plain_text())}</text:h>'
            )
        elif isinstance(node, Paragraph):
            body_parts.append(f"<text:p>{escape(node.plain_text())}</text:p>")
        elif isinstance(node, ListItem):
            prefix = "• " if not node.ordered else f"{node.level}. "
            body_parts.append(f"<text:p>{escape(prefix + node.plain_text())}</text:p>")
        elif isinstance(node, Table):
            body_parts.append(_table_xml(node))
        elif isinstance(node, HorizontalRule):
            body_parts.append("<text:p>---</text:p>")
        elif isinstance(node, CodeBlock):
            body_parts.append(f"<text:p>{escape(node.content)}</text:p>")
        elif isinstance(node, Blockquote):
            for p in node.paragraphs:
                body_parts.append(f"<text:p>{escape('「' + ensure_rich_text(p).text + '」')}</text:p>")
        elif isinstance(node, Comment):
            anchor = f" ({node.anchor})" if node.anchor else ""
            author = node.author or "reviewer"
            body_parts.append(
                f"<text:p>{escape(f'[Comment {author}{anchor}] {node.text}')}</text:p>"
            )
        elif isinstance(node, Footnote):
            body_parts.append(f"<text:p>{escape(f'[^{node.id}] {node.text}')}</text:p>")
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

    if not body_parts:
        body_parts.append("<text:p></text:p>")

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 office:version="1.2">
 <office:body>
  <office:text>
   {"".join(body_parts)}
  </office:text>
 </office:body>
</office:document-content>
"""
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
 <manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
 <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>
"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/manifest.xml", manifest)
        zf.writestr("content.xml", content)
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
