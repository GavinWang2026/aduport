"""Minimal ODT (OpenDocument Text) exporter."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

from app.core.document_model import (
    DocumentModel,
    Heading,
    HorizontalRule,
    ListItem,
    Paragraph,
    Table,
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
    rows_xml: list[str] = []
    for row in table.rows:
        cells = "".join(
            f"<table:table-cell office:value-type=\"string\"><text:p>{escape(str(c))}</text:p></table:table-cell>"
            for c in row
        )
        rows_xml.append(f"<table:table-row>{cells}</table:table-row>")
    return f'<table:table table:name="Table1">{"".join(rows_xml)}</table:table>'
