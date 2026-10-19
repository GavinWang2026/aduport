"""ODS (OpenDocument Spreadsheet) exporter — tables + typed cells + package meta."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape

from aduport.core.document_model import (
    Blockquote,
    Chart,
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
    Paragraph,
    PivotTable,
    Revision,
    Table,
    TableCell,
    TextBox,
)
from aduport.exporters.export_warnings import placeholder_line, warn_export
from aduport.exporters.odf_common import odf_manifest_xml, odf_meta_xml, odf_styles_xml
from aduport.exporters.rich_visible import render_rich_visible

_MIME = "application/vnd.oasis.opendocument.spreadsheet"


def export_ods(model: DocumentModel) -> bytes:
    """Export tables (+ searchable content sheet) as ODS."""
    tables = [n for n in model.nodes if isinstance(n, Table)]
    if not tables:
        rows = _content_rows(model)
        tables = [Table(name="Content", rows=rows or [[""]], header_rows=0)]
        warn_export(model, "ods: no Table nodes → single Content sheet from body text")

    sheet_parts: list[str] = []
    for i, table in enumerate(tables[:64], start=1):
        name = escape((table.name or f"Sheet{i}")[:31] or f"Sheet{i}")
        sheet_parts.append(_table_xml(table, name))

    # Non-table body nodes → searchable ContentExtra sheet (no silent drop)
    body_rows = [
        [line]
        for n in model.nodes
        if not isinstance(n, (Table, Chart, PivotTable))
        for line in [_searchable_line(n)]
        if line
    ]
    if tables and body_rows:
        sheet_parts.append(_matrix_sheet("ContentExtra", body_rows[:200]))

    # Charts / pivots as annotation sheets (searchable, not native chart objects)
    extras: list[list[str]] = []
    for ch in [n for n in model.nodes if isinstance(n, Chart)][:12]:
        extras.append([ch.plain_text()])
    for pv in [n for n in model.nodes if isinstance(n, PivotTable)][:12]:
        fields = ", ".join(pv.row_fields + pv.col_fields + pv.data_fields)
        extras.append([f"[Pivot {pv.name}] {fields}"])
    if extras:
        sheet_parts.append(_matrix_sheet("ChartsPivots", extras))
        warn_export(model, "ods: Chart/PivotTable → text sheet ChartsPivots")

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 office:version="1.2">
 <office:body>
  <office:spreadsheet>
   {"".join(sheet_parts)}
  </office:spreadsheet>
 </office:body>
</office:document-content>
"""
    from aduport.importers.zip_images import package_images

    pictures = package_images(model, "Pictures/")
    extra = [
        f'<manifest:file-entry manifest:full-path="{path}" manifest:media-type="{mime}"/>'
        for path, _blob, mime in pictures
    ]
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", _MIME, compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/manifest.xml", odf_manifest_xml(_MIME, extra_entries=extra or None))
        for path, blob, _mime in pictures:
            zf.writestr(path, blob)
        zf.writestr("content.xml", content)
        zf.writestr("meta.xml", odf_meta_xml(title=model.title or "ods"))
        zf.writestr("styles.xml", odf_styles_xml())
    return buf.getvalue()


def _content_rows(model: DocumentModel) -> list[list[str]]:
    rows: list[list[str]] = []
    for node in model.nodes:
        line = _searchable_line(node)
        if line:
            rows.append([line])
    return rows


def _searchable_line(node) -> str | None:
    """Map any AST node to a searchable cell string (breadth/depth gate)."""
    if isinstance(node, Heading):
        return "#" * node.level + " " + node.plain_text()
    if isinstance(node, Paragraph):
        return render_rich_visible(node.text)
    if isinstance(node, ListItem):
        if node.checked is not None:
            mark = "[x] " if node.checked else "[ ] "
        else:
            mark = "- " if not node.ordered else "1. "
        return mark + render_rich_visible(node.text)
    if isinstance(node, Image):
        alt = (node.alt_text or "image").split("|", 1)[0]
        return f"[Image: {alt}]"
    if isinstance(node, CodeBlock):
        return f"```{node.lang or ''}\n{node.content or ''}\n```"
    if isinstance(node, Blockquote):
        parts = []
        for p in node.paragraphs:
            rendered = render_rich_visible(p) if not isinstance(p, str) else p
            for line in str(rendered).splitlines() or [""]:
                parts.append(f"> {line}")
        return "\n".join(parts)
    if isinstance(node, MathBlock):
        return node.content or ""
    if isinstance(node, Footnote):
        return f"[^{node.id}]: {node.text}"
    if isinstance(node, HtmlBlock):
        return placeholder_line("HtmlBlock", (node.content or "")[:200])
    if isinstance(node, Comment):
        return f"[Comment {node.author}] {node.text}"
    if isinstance(node, Revision):
        tag = "ins" if node.change == "insert" else "del"
        return f"[{tag} {node.author}] {node.text}"
    if isinstance(node, TextBox):
        return f"[TextBox] {node.text}"
    if isinstance(node, HorizontalRule):
        return "---"
    if isinstance(node, PivotTable):
        fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
        return f"[Pivot {node.name}] {fields}"
    plain = getattr(node, "plain_text", None)
    if callable(plain):
        t = plain()
        return str(t) if t else None
    return None


def _cell_xml(value: object) -> str:
    s = str(value) if value is not None else ""
    # Prefer float typing for plain numerics (ISO ODF value-type).
    try:
        if s and s.strip() and s.strip()[0] in "0123456789+-.":
            num = float(s.strip().replace(",", ""))
            if abs(num) < 1e15:
                return (
                    f'<table:table-cell office:value-type="float" office:value="{num}">'
                    f"<text:p>{escape(s)}</text:p></table:table-cell>"
                )
    except ValueError:
        pass
    return (
        '<table:table-cell office:value-type="string">'
        f"<text:p>{escape(s)}</text:p></table:table-cell>"
    )


def _table_xml(table: Table, name: str) -> str:
    # Prefer cell-grid with ODF row/col spans when present (align with odt).
    if table.cells and _table_has_span_cells(table):
        return _table_xml_spans(table, name)
    matrix = table.rows or []
    if not matrix and table.cells:
        matrix = [[c.text for c in row] for row in table.cells]
    rows_xml: list[str] = []
    for row in matrix[:500]:
        cells = "".join(_cell_xml(c) for c in row)
        rows_xml.append(f"<table:table-row>{cells}</table:table-row>")
    if not rows_xml:
        rows_xml.append(
            '<table:table-row><table:table-cell office:value-type="string">'
            "<text:p></text:p></table:table-cell></table:table-row>"
        )
    return f'<table:table table:name="{name}">{"".join(rows_xml)}</table:table>'


def _table_has_span_cells(table: Table) -> bool:
    for row in table.cells or []:
        for cell in row:
            if cell.covered or cell.row_span > 1 or cell.col_span > 1:
                return True
    return False


def _ods_cell_xml(cell: TableCell) -> str:
    if cell.covered:
        return "<table:covered-table-cell/>"
    # Reuse typed numeric path when plain text looks numeric and no span attrs needed beyond type
    base = _cell_xml(cell.text)
    if cell.col_span <= 1 and cell.row_span <= 1:
        return base
    # Inject span attributes into opening tag
    attrs = []
    if cell.col_span > 1:
        attrs.append(f'table:number-columns-spanned="{cell.col_span}"')
    if cell.row_span > 1:
        attrs.append(f'table:number-rows-spanned="{cell.row_span}"')
    insert = " " + " ".join(attrs)
    return base.replace("<table:table-cell ", f"<table:table-cell{insert} ", 1)


def _table_xml_spans(table: Table, name: str) -> str:
    rows_xml: list[str] = []
    for row in (table.cells or [])[:500]:
        cells = "".join(_ods_cell_xml(cell) for cell in row)
        rows_xml.append(f"<table:table-row>{cells}</table:table-row>")
    if not rows_xml:
        rows_xml.append(
            '<table:table-row><table:table-cell office:value-type="string">'
            "<text:p></text:p></table:table-cell></table:table-row>"
        )
    return f'<table:table table:name="{name}">{"".join(rows_xml)}</table:table>'


def _matrix_sheet(name: str, rows: list[list[str]]) -> str:
    return _table_xml(Table(name=name, rows=rows, header_rows=0), escape(name))
