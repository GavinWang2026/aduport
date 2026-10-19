"""Minimal XLSX (OOXML) table import/export without openpyxl."""

from __future__ import annotations

import base64
import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from aduport.core import xml_safe
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
    Table,
    TableCell,
    TextBox,
)
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError
from aduport.core.safe_zip import ZipInflateBudget

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_META_AST = "_AST"
_META_CONTENT = "Content"  # visible body sheet (not underscore-prefixed; counts as body)
# Legacy sheet names (import still supported)
_META_REVISIONS = "_Revisions"
_META_CONTROLS = "_Controls"
_META_TEXTBOXES = "_TextBoxes"
_META_COMMENTS = "_Comments"
_META_FOOTNOTES = "_Footnotes"
_META_MATH = "_Math"
_META_IMAGES = "_Images"
_META_HTML = "_HtmlBlocks"
_META_CODE = "_CodeBlocks"
_META_BLOCKQUOTE = "_Blockquotes"
_META_HEADINGS = "_Headings"
_META_PARAGRAPHS = "_Paragraphs"
_META_LIST_ITEMS = "_ListItems"
_META_RULES = "_Rules"
_META_CELL_SPANS = "_CellSpans"
_AST_HEADER = ["node_type", "p1", "p2", "p3", "p4", "p5", "p6"]


def _safe_sheet_name(name: str, index: int) -> str:
    raw = (name or "").strip() or f"Sheet{index}"
    cleaned = re.sub(r"[\[\]\*\/\\?:]", "_", raw)[:31]
    return cleaned or f"Sheet{index}"


def export_xlsx(model: DocumentModel) -> bytes:
    """Export tables (+ formulas), PivotTable, Revision/ContentControl meta sheets."""
    from aduport.config import settings
    from aduport.exporters.export_warnings import warn_export

    tables = [n for n in model.nodes if isinstance(n, Table)]
    pivots = [n for n in model.nodes if isinstance(n, PivotTable)]
    charts_all = [n for n in model.nodes if isinstance(n, Chart)]
    chart_cap = 8
    if len(charts_all) > chart_cap:
        warn_export(
            model,
            f"xlsx: truncated {len(charts_all) - chart_cap} Chart(s) (cap {chart_cap})",
        )
    charts = charts_all[:chart_cap]
    if any((c.chart_type or "").lower().strip() in ("scatter", "xy") for c in charts):
        warn_export(
            model,
            "xlsx: scatter/xy Chart degraded to lineChart (literal category axis)",
        )
    emit_ast = bool(getattr(settings, "xlsx_ast_sheet", False))
    meta_sheets = _meta_ast_sheets(model) if emit_ast else []
    non_table = [
        n
        for n in model.nodes
        if not isinstance(n, Table)
    ]
    content_sheet = _content_sheet(model, emit_ast=emit_ast) if non_table else None
    if non_table and meta_sheets and not content_sheet:
        warn_export(
            model,
            f"xlsx: {len(non_table)} non-table node(s) parked on _AST sheet (not body cells)",
        )
    elif non_table and not emit_ast and not content_sheet:
        warn_export(
            model,
            f"xlsx: {len(non_table)} non-table node(s); _AST sheet disabled "
            "(ADUPORT_XLSX_AST_SHEET=0) — body cells / Content sheet only",
        )
    sheet_cap = max(1, min(255, int(getattr(settings, "xlsx_max_sheets", 64) or 64)))
    reserved = len(meta_sheets) + (1 if content_sheet else 0)
    max_data_sheets = max(1, sheet_cap - reserved)
    if len(tables) > max_data_sheets:
        warn_export(
            model,
            f"xlsx: truncated {len(tables) - max_data_sheets} table sheet(s) (cap {max_data_sheets})",
        )
    if not tables and content_sheet is None:
        # Last resort: still a kind|text sheet so import does not reject the file.
        vis_rows = _visible_body_rows(model)
        rows = [["kind", "text"]]
        for row in vis_rows:
            rows.append(["paragraph", row[0] if row else ""])
        if len(rows) == 1:
            rows.append(["paragraph", ""])
        tables = [Table(rows=rows, name="Content", header_rows=1)]
    elif content_sheet is None and non_table:
        # Mixed doc but content builder returned None — still warn
        warn_export(
            model,
            f"xlsx: {len(non_table)} non-table node(s) without Content sheet",
        )

    sheet_xmls: list[tuple[str, str, str]] = []  # path, xml, name
    used_names: set[str] = set()
    for i, table in enumerate(tables[:max_data_sheets], start=1):
        name = _safe_sheet_name(table.name, i)
        base = name
        n = 2
        while name.lower() in used_names:
            suffix = f"_{n}"
            name = (base[: 31 - len(suffix)] + suffix) if len(base) + len(suffix) > 31 else base + suffix
            n += 1
        used_names.add(name.lower())
        drawing_r_id = "rId1" if (i == 1 and charts) else None
        sheet_xmls.append(
            (f"xl/worksheets/sheet{i}.xml", _sheet_xml(table, drawing_r_id=drawing_r_id), name)
        )

    if content_sheet is not None:
        path, xml, name = content_sheet
        idx = len(sheet_xmls) + 1
        sheet_xmls.append((f"xl/worksheets/sheet{idx}.xml", xml, name))

    base_idx = len(sheet_xmls)
    for j, (path, xml, name) in enumerate(meta_sheets, start=1):
        sheet_xmls.append((f"xl/worksheets/sheet{base_idx + j}.xml", xml, name))

    sheet_count = len(sheet_xmls)
    pivot_count = min(len(pivots), 8)
    style_rid = sheet_count + 1
    pivot_start_rid = style_rid + 1

    content_types = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for i in range(1, sheet_count + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    for i in range(1, pivot_count + 1):
        content_types.append(
            f'<Override PartName="/xl/pivotTables/pivotTable{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.pivotTable+xml"/>'
        )
        content_types.append(
            f'<Override PartName="/xl/pivotCache/pivotCacheDefinition{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"/>'
        )
        content_types.append(
            f'<Override PartName="/xl/pivotCache/pivotCacheRecords{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheRecords+xml"/>'
        )
    from aduport.importers.zip_images import package_images

    xlsx_images = package_images(model, "xl/media/")
    seen_ext: set[str] = set()
    for path, _blob, mime in xlsx_images:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext in seen_ext:
            continue
        seen_ext.add(ext)
        content_types.append(f'<Default Extension="{ext}" ContentType="{mime}"/>')
    if charts:
        content_types.append(
            '<Override PartName="/xl/drawings/drawing1.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
        )
        for i in range(1, len(charts) + 1):
            content_types.append(
                f'<Override PartName="/xl/charts/chart{i}.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
            )
    content_types.append("</Types>")

    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
        for i, (_, _, name) in enumerate(sheet_xmls, start=1)
    )
    pivot_caches_xml = ""
    if pivot_count:
        entries = "".join(
            f'<pivotCache cacheId="{i}" r:id="rId{pivot_start_rid + i - 1}"/>'
            for i in range(1, pivot_count + 1)
        )
        pivot_caches_xml = f'<pivotCaches count="{pivot_count}">{entries}</pivotCaches>'

    workbook = f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="{_M}"
 xmlns:r="{_R}">
 <sheets>{workbook_sheets}</sheets>
 {pivot_caches_xml}
</workbook>
"""
    rels_inner = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    rels_inner += (
        f'<Relationship Id="rId{style_rid}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    for i in range(1, pivot_count + 1):
        rid = pivot_start_rid + i - 1
        rels_inner += (
            f'<Relationship Id="rId{rid}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/pivotCacheDefinition" '
            f'Target="pivotCache/pivotCacheDefinition{i}.xml"/>'
        )
    workbook_rels = f"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{rels_inner}
</Relationships>
"""
    root_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    styles = """<?xml version="1.0" encoding="UTF-8"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <fonts count="2">
  <font><sz val="11"/><name val="Calibri"/></font>
  <font><b/><sz val="11"/><name val="Calibri"/></font>
 </fonts>
 <fills count="1"><fill><patternFill patternType="none"/></fill></fills>
 <borders count="1"><border/></borders>
 <cellStyleXfs count="1"><xf/></cellStyleXfs>
 <cellXfs count="2">
  <xf xfId="0"/>
  <xf xfId="0" fontId="1" applyFont="1"/>
 </cellXfs>
</styleSheet>
"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "\n".join(content_types))
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", styles)
        for path, blob, _mime in xlsx_images:
            zf.writestr(path, blob)
        for path, xml, _name in sheet_xmls:
            zf.writestr(path, xml)
        for i, pivot in enumerate(pivots[:8], start=1):
            zf.writestr(
                f"xl/pivotTables/pivotTable{i}.xml",
                _pivot_table_xml(pivot, cache_id=i),
            )
            zf.writestr(
                f"xl/pivotCache/pivotCacheDefinition{i}.xml",
                _pivot_cache_xml(pivot, cache_id=i),
            )
            zf.writestr(
                f"xl/pivotCache/pivotCacheRecords{i}.xml",
                _pivot_cache_records_xml(pivot, tables=tables),
            )
        if charts:
            warn_export(model, f"xlsx: embedded {len(charts)} DrawingML chart(s) on sheet1")
            zf.writestr(
                "xl/worksheets/_rels/sheet1.xml.rels",
                _sheet_drawing_rels(),
            )
            zf.writestr("xl/drawings/drawing1.xml", _charts_drawing_xml(len(charts)))
            zf.writestr(
                "xl/drawings/_rels/drawing1.xml.rels",
                _drawing_chart_rels(len(charts)),
            )
            for i, ch in enumerate(charts, start=1):
                zf.writestr(f"xl/charts/chart{i}.xml", _chart_space_xml(ch))
    return buf.getvalue()


def _pivot_table_xml(p: PivotTable, cache_id: int) -> str:
    fields: list[str] = []
    for fname in p.row_fields:
        fields.append(f'<pivotField name="{escape(fname)}" axis="axisRow"/>')
    for fname in p.col_fields:
        fields.append(f'<pivotField name="{escape(fname)}" axis="axisCol"/>')
    for fname in p.data_fields:
        fields.append(f'<pivotField name="{escape(fname)}" axis="axisValues"/>')
    loc = escape(p.location or "A1")
    name = escape(p.name or f"Pivot{cache_id}")
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<pivotTable xmlns="{_M}" name="{name}" cacheId="{cache_id}">'
        f'<location ref="{loc}" firstHeaderRow="1" firstDataRow="2" firstDataCol="1"/>'
        f'<pivotFields count="{len(fields)}">{"".join(fields)}</pivotFields>'
        f"</pivotTable>"
    )


def _pivot_cache_xml(p: PivotTable, cache_id: int) -> str:
    ref = escape(p.source_range or "A1:D10")
    field_names = list(dict.fromkeys(p.row_fields + p.col_fields + p.data_fields))
    cache_fields = "".join(
        f'<cacheField name="{escape(fname)}" numFmtId="0"/>' for fname in field_names
    )
    count = len(field_names)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<pivotCacheDefinition xmlns="{_M}" xmlns:r="{_R}" r:id="rId1">'
        f'<cacheSource type="worksheet">'
        f'<worksheetSource ref="{ref}" sheet="1"/>'
        f"</cacheSource>"
        f'<cacheFields count="{count}">{cache_fields}</cacheFields>'
        f"</pivotCacheDefinition>"
    )


def _pivot_cache_records_xml(p: PivotTable, *, tables: list[Table]) -> str:
    field_names = list(dict.fromkeys(p.row_fields + p.col_fields + p.data_fields))
    rows = _pivot_source_rows(p, tables)
    if not field_names or not rows:
        return (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<pivotCacheRecords xmlns="{_M}" count="0"/>'
        )
    record_xml: list[str] = []
    for row in rows:
        cells = "".join(
            f'<x v="{escape(str(row[i] if i < len(row) else ""))}"/>'
            for i in range(len(field_names))
        )
        record_xml.append(f"<r>{cells}</r>")
    count = len(record_xml)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<pivotCacheRecords xmlns="{_M}" count="{count}">'
        f"{''.join(record_xml)}</pivotCacheRecords>"
    )


def _pivot_source_rows(p: PivotTable, tables: list[Table]) -> list[list[str]]:
    """Best-effort data rows for pivot cache from first table (header + body)."""
    if not tables:
        return []
    tbl = tables[0]
    matrix = _table_value_matrix(tbl)
    if len(matrix) < 2:
        return []
    header = [str(c) for c in matrix[0]]
    names = (
        list(dict.fromkeys(p.row_fields + p.col_fields + p.data_fields))
        or header
    )
    col_idx = {name: i for i, name in enumerate(header)}
    out: list[list[str]] = []
    for row in matrix[1:]:
        mapped = []
        for fname in names:
            idx = col_idx.get(fname)
            mapped.append(str(row[idx]) if idx is not None and idx < len(row) else "")
        if any(mapped):
            out.append(mapped)
    return out[:500]


def _visible_body_rows(model: DocumentModel) -> list[list[str]]:
    """Flatten all nodes to searchable single-column rows for the primary sheet."""
    from aduport.exporters.rich_visible import render_rich_visible

    rows: list[list[str]] = []
    for node in model.nodes:
        if isinstance(node, Table):
            for row in node.rows or []:
                rows.append([str(c) for c in row])
            continue
        if isinstance(node, Heading):
            rows.append(["#" * node.level + " " + node.plain_text()])
        elif isinstance(node, Paragraph):
            rows.append([render_rich_visible(node.text)])
        elif isinstance(node, ListItem):
            indent = "  " * max(0, int(node.level or 0))
            if node.checked is not None:
                mark = "[x]" if node.checked else "[ ]"
            else:
                mark = "1." if node.ordered else "-"
            rows.append([f"{indent}{mark} {render_rich_visible(node.text)}"])
        elif isinstance(node, Blockquote):
            rows.append(["> " + _blockquote_text(node)])
        elif isinstance(node, CodeBlock):
            rows.append([f"```{node.lang or ''}\n{node.content}\n```"])
        elif isinstance(node, MathBlock):
            rows.append([f"$$ {node.content} $$"])
        elif isinstance(node, HorizontalRule):
            rows.append(["---"])
        elif isinstance(node, Footnote):
            rows.append([f"[^{node.id}]: {node.text}"])
        elif isinstance(node, Image):
            alt = (node.alt_text or "image").split("|", 1)[0]
            rows.append([f"[Image: {alt}]"])
        elif isinstance(node, HtmlBlock):
            import re

            text = re.sub(r"<[^>]+>", "", node.content or "").strip()
            if text:
                rows.append([text])
        elif isinstance(node, Comment):
            rows.append([f"[Comment {node.author}] {node.text}"])
        elif isinstance(node, Revision):
            tag = "ins" if node.change == "insert" else "del"
            rows.append([f"[{tag} {node.author}] {node.text}"])
        elif isinstance(node, TextBox):
            rows.append([f"[TextBox] {node.text}"])
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            rows.append([f"[PivotTable {node.name}] {fields}"])
        else:
            plain = getattr(node, "plain_text", None)
            if callable(plain):
                text = plain()
                if text:
                    rows.append([str(text)])
    return rows


def _content_sheet(
    model: DocumentModel, *, emit_ast: bool = False
) -> tuple[str, str, str] | None:
    """Visible body sheet for mixed docs (headings/lists/rich text searchable).

    When ``emit_ast`` is False, typed meta (revision/comment/…) is parked as
    ``paragraph`` text so round-trip does not rehydrate AST nodes without ``_AST``.
    When ``emit_ast`` is True, Content remains a human-searchable mirror; import
    prefers ``_AST`` and skips Content rehydration to avoid duplicates.
    """
    from aduport.exporters.rich_visible import render_rich_visible

    rows: list[list[str]] = [["kind", "text"]]
    for node in model.nodes:
        if isinstance(node, Table):
            continue
        if isinstance(node, Heading):
            rows.append(["heading", "#" * node.level + " " + node.plain_text()])
        elif isinstance(node, Paragraph):
            rows.append(["paragraph", render_rich_visible(node.text)])
        elif isinstance(node, ListItem):
            indent = "  " * max(0, int(node.level or 0))
            if node.checked is not None:
                mark = "[x]" if node.checked else "[ ]"
            else:
                mark = "1." if node.ordered else "-"
            rows.append(["list_item", f"{indent}{mark} {render_rich_visible(node.text)}"])
        elif isinstance(node, Blockquote):
            rows.append(["blockquote", "> " + _blockquote_text(node)])
        elif isinstance(node, CodeBlock):
            rows.append(["code_block", f"```{node.lang or ''}\n{node.content}\n```"])
        elif isinstance(node, MathBlock):
            if emit_ast:
                rows.append(["math_block", f"$$ {node.content} $$"])
            else:
                rows.append(["paragraph", f"$$ {node.content} $$"])
        elif isinstance(node, HorizontalRule):
            rows.append(["horizontal_rule", "---"])
        elif isinstance(node, Footnote):
            if emit_ast:
                rows.append(["footnote", f"[^{node.id}]: {node.text}"])
            else:
                rows.append(["paragraph", f"[^{node.id}]: {node.text}"])
        elif isinstance(node, Image):
            alt = (node.alt_text or "image").split("|", 1)[0]
            if emit_ast:
                rows.append(["image", f"[Image: {alt}]"])
            else:
                rows.append(["paragraph", f"[Image: {alt}]"])
        elif isinstance(node, HtmlBlock):
            import re

            text = re.sub(r"<[^>]+>", "", node.content or "").strip()
            if text:
                rows.append(["html_block" if emit_ast else "paragraph", text])
        elif isinstance(node, Comment):
            tip = f"[Comment {node.author}] {node.text}"
            rows.append(["comment" if emit_ast else "paragraph", tip])
        elif isinstance(node, Revision):
            tag = "ins" if node.change == "insert" else "del"
            tip = f"[{tag} {node.author}] {node.text}"
            rows.append(["revision" if emit_ast else "paragraph", tip])
        elif isinstance(node, ContentControl):
            tip = (
                f"[ContentControl {node.tag}|{node.alias}|{node.control_type}] {node.text}"
            )
            rows.append(["content_control" if emit_ast else "paragraph", tip])
        elif isinstance(node, TextBox):
            src = (node.source or "").strip()
            prefix = f"[TextBox|{src}]" if src else "[TextBox]"
            tip = f"{prefix} {node.text}"
            rows.append(["text_box" if emit_ast else "paragraph", tip])
        elif isinstance(node, PivotTable):
            fields = ", ".join(node.row_fields + node.col_fields + node.data_fields)
            tip = (
                f"[PivotTable {node.name}|{node.source_range}|{node.location}] {fields}"
            )
            rows.append(["pivot_table" if emit_ast else "paragraph", tip])
        else:
            plain = getattr(node, "plain_text", None)
            if callable(plain):
                text = plain()
                if text:
                    rows.append([type(node).__name__.lower(), str(text)])
    if len(rows) <= 1:
        return None
    return (
        "xl/worksheets/sheet_content.xml",
        _sheet_xml(Table(rows=rows, header_rows=1)),
        _META_CONTENT,
    )


def _meta_ast_sheets(model: DocumentModel) -> list[tuple[str, str, str]]:
    """Single _AST worksheet for document meta AST nodes (revision…math)."""
    revs = [n for n in model.nodes if isinstance(n, Revision)]
    ctrls = [n for n in model.nodes if isinstance(n, ContentControl)]
    boxes = [n for n in model.nodes if isinstance(n, TextBox)]
    pivots = [n for n in model.nodes if isinstance(n, PivotTable)]
    comments = [n for n in model.nodes if isinstance(n, Comment)]
    footnotes = [n for n in model.nodes if isinstance(n, Footnote)]
    maths = [n for n in model.nodes if isinstance(n, MathBlock)]
    images = [n for n in model.nodes if isinstance(n, Image)]
    html_blocks = [n for n in model.nodes if isinstance(n, HtmlBlock)]
    codes = [n for n in model.nodes if isinstance(n, CodeBlock)]
    blockquotes = [n for n in model.nodes if isinstance(n, Blockquote)]
    headings = [n for n in model.nodes if isinstance(n, Heading)]
    paragraphs = [n for n in model.nodes if isinstance(n, Paragraph)]
    list_items = [n for n in model.nodes if isinstance(n, ListItem)]
    rules = [n for n in model.nodes if isinstance(n, HorizontalRule)]
    from aduport.core.document_model import (
        Bookmark,
        FieldCode,
        HeaderFooter,
        Numbering,
        PageSetup,
        RunStyle,
        Section,
    )

    sections = [n for n in model.nodes if isinstance(n, Section)]
    page_setups = [n for n in model.nodes if isinstance(n, PageSetup)]
    headers = [n for n in model.nodes if isinstance(n, HeaderFooter)]
    fields = [n for n in model.nodes if isinstance(n, FieldCode)]
    bookmarks = [n for n in model.nodes if isinstance(n, Bookmark)]
    styles = [n for n in model.nodes if isinstance(n, RunStyle)]
    numberings = [n for n in model.nodes if isinstance(n, Numbering)]
    span_cell_rows = _table_cell_ast_rows(model)
    if not (
        revs or ctrls or boxes or pivots or comments or footnotes or maths
        or images or html_blocks or codes or blockquotes or headings
        or paragraphs or list_items or rules or span_cell_rows
        or sections or page_setups or headers or fields or bookmarks or styles or numberings
    ):
        return []

    matrix: list[list[str]] = [list(_AST_HEADER)]
    for r in revs[:500]:
        matrix.append(["revision", r.change, r.author, r.anchor, r.text, "", ""])
    for c in ctrls[:500]:
        matrix.append(
            ["content_control", c.tag, c.alias, c.control_type, c.text, "", ""]
        )
    for b in boxes[:500]:
        matrix.append(
            [
                "text_box",
                b.source,
                b.text,
                _fmt_mm(b.x_mm),
                _fmt_mm(b.y_mm),
                _fmt_mm(b.width_mm),
                _fmt_mm(b.height_mm),
            ]
        )
    for p in pivots[:500]:
        matrix.append(
            [
                "pivot_table",
                p.name,
                p.source_range,
                p.location,
                _join_fields(p.row_fields),
                _join_fields(p.col_fields),
                _join_fields(p.data_fields),
            ]
        )
    for cm in comments[:500]:
        matrix.append(["comment", cm.author, cm.anchor, cm.text, "", "", ""])
    for fn in footnotes[:500]:
        matrix.append(["footnote", fn.id, fn.text, "", "", "", ""])
    for m in maths[:500]:
        matrix.append(
            [
                "math_block",
                m.content,
                "true" if m.display else "false",
                "",
                "",
                "",
                "",
            ]
        )
    for img in images[:200]:
        matrix.append(
            [
                "image",
                img.alt_text,
                img.mime_type,
                img.wrap,
                _b64_encode(img.data),
                img.url or "",
            ]
        )
    for hb in html_blocks[:500]:
        matrix.append(["html_block", hb.content, "", "", "", "", ""])
    for cb in codes[:500]:
        matrix.append(["code_block", cb.lang, cb.content, "", "", "", ""])
    for bq in blockquotes[:500]:
        matrix.append(["blockquote", _blockquote_text(bq), "", "", "", "", ""])
    for h in headings[:500]:
        matrix.append(
            [
                "heading",
                str(h.level),
                h.plain_text(),
                h.anchor or "",
                "",
                "",
                "",
            ]
        )
    for para in paragraphs[:500]:
        from aduport.exporters.rich_visible import render_rich_visible

        matrix.append(["paragraph", render_rich_visible(para.text), "", "", "", "", ""])
    for li in list_items[:500]:
        from aduport.exporters.rich_visible import render_rich_visible

        checked = ""
        if li.checked is not None:
            checked = "true" if li.checked else "false"
        children = "\n".join(c.plain_text() for c in li.children)
        matrix.append(
            [
                "list_item",
                render_rich_visible(li.text),
                "true" if li.ordered else "false",
                str(li.level),
                checked,
                children,
                "",
            ]
        )
    for _hr in rules[:500]:
        matrix.append(["horizontal_rule", "", "", "", "", "", ""])
    for s in sections[:200]:
        matrix.append(["section", s.title, str(s.props), "", "", "", ""])
    for ps in page_setups[:200]:
        matrix.append(
            ["page_setup", ps.page_size, ps.orientation, str(ps.margins_mm), "", "", ""]
        )
    for hf in headers[:200]:
        matrix.append(["header_footer", hf.kind, hf.text, "", "", "", ""])
    for f in fields[:200]:
        matrix.append(
            ["field", f.field_type, f.instruction, f.result_text, "", "", ""]
        )
    for bm in bookmarks[:200]:
        matrix.append(["bookmark", bm.name, bm.anchor, "", "", "", ""])
    for st in styles[:200]:
        matrix.append(["run_style", st.style_id, st.name, str(st.props), "", "", ""])
    for num in numberings[:200]:
        matrix.append(
            [
                "numbering",
                num.num_id,
                num.abstract_num_id,
                str(len(num.levels)),
                "",
                "",
                "",
            ]
        )
    matrix.extend(span_cell_rows[:2000])
    return [
        (
            "xl/worksheets/sheet_meta_ast.xml",
            _sheet_xml(Table(rows=matrix, header_rows=1)),
            _META_AST,
        )
    ]


def _blockquote_text(bq: Blockquote) -> str:
    parts: list[str] = []
    for p in bq.paragraphs:
        if isinstance(p, str):
            parts.append(p)
        else:
            parts.append(getattr(p, "text", str(p)))
    return "\n".join(parts)


def _join_fields(fields: list[str]) -> str:
    return ", ".join(str(f) for f in fields if str(f).strip())


def _table_has_span_cells(table: Table) -> bool:
    if not table.cells:
        return False
    for row in table.cells:
        for cell in row:
            if cell.covered or cell.row_span > 1 or cell.col_span > 1:
                return True
            if cell.origin_row or cell.origin_col:
                return True
    return False


def _table_cell_ast_rows(model: DocumentModel) -> list[list[str]]:
    rows: list[list[str]] = []
    for node in model.nodes:
        if not isinstance(node, Table) or not _table_has_span_cells(node):
            continue
        tname = node.name or "Sheet1"
        for r_idx, row in enumerate(node.cells):
            for c_idx, cell in enumerate(row):
                rows.append(
                    [
                        "table_cell",
                        tname,
                        str(r_idx),
                        str(c_idx),
                        cell.text,
                        (
                            f"{cell.row_span},{cell.col_span},"
                            f"{1 if cell.covered else 0},"
                            f"{cell.origin_row},{cell.origin_col}"
                        ),
                        f"{cell.formula}:::{cell.cell_kind}",
                    ]
                )
    return rows


def _parse_span_meta(raw: str) -> tuple[int, int, bool, int, int]:
    parts = (raw or "1,1,0,0,0").split(",")
    while len(parts) < 5:
        parts.append("0")
    try:
        row_span = max(1, int(parts[0] or 1))
        col_span = max(1, int(parts[1] or 1))
    except ValueError:
        row_span, col_span = 1, 1
    covered = parts[2].strip().lower() in ("1", "true", "yes")
    try:
        origin_row = int(parts[3] or 0)
        origin_col = int(parts[4] or 0)
    except ValueError:
        origin_row, origin_col = 0, 0
    return row_span, col_span, covered, origin_row, origin_col


def _parse_cell_meta(raw: str) -> tuple[str, str]:
    if ":::" in raw:
        formula, kind = raw.split(":::", 1)
    else:
        formula, kind = raw, "value"
    kind = kind if kind in ("value", "formula", "pivot", "chart") else "value"
    return formula, kind


def _parse_table_cell_ast_row(row: list[str]) -> tuple[str, int, int, TableCell] | None:
    try:
        r_idx = int(_row_cell(row, 2, "0"))
        c_idx = int(_row_cell(row, 3, "0"))
    except ValueError:
        return None
    tname = _row_cell(row, 1, "") or "Sheet1"
    row_span, col_span, covered, origin_row, origin_col = _parse_span_meta(
        _row_cell(row, 5, "")
    )
    formula, cell_kind = _parse_cell_meta(_row_cell(row, 6, ""))
    return (
        tname,
        r_idx,
        c_idx,
        TableCell(
            text=_row_cell(row, 4, ""),
            row_span=row_span,
            col_span=col_span,
            covered=covered,
            origin_row=origin_row,
            origin_col=origin_col,
            formula=formula,
            cell_kind=cell_kind,  # type: ignore[arg-type]
        ),
    )


def _parse_table_cell_legacy_row(row: list[str]) -> tuple[str, int, int, TableCell] | None:
    try:
        r_idx = int(_row_cell(row, 1, "0"))
        c_idx = int(_row_cell(row, 2, "0"))
    except ValueError:
        return None
    tname = _row_cell(row, 0, "") or "Sheet1"
    try:
        row_span = max(1, int(_row_cell(row, 4, "1") or 1))
        col_span = max(1, int(_row_cell(row, 5, "1") or 1))
    except ValueError:
        row_span, col_span = 1, 1
    covered = _row_cell(row, 6, "").lower() in ("1", "true", "yes")
    try:
        origin_row = int(_row_cell(row, 7, "0") or 0)
        origin_col = int(_row_cell(row, 8, "0") or 0)
    except ValueError:
        origin_row, origin_col = 0, 0
    formula = _row_cell(row, 9, "")
    kind = _row_cell(row, 10, "value")
    if kind not in ("value", "formula", "pivot", "chart"):
        kind = "value"
    return (
        tname,
        r_idx,
        c_idx,
        TableCell(
            text=_row_cell(row, 3, ""),
            row_span=row_span,
            col_span=col_span,
            covered=covered,
            origin_row=origin_row,
            origin_col=origin_col,
            formula=formula,
            cell_kind=kind,  # type: ignore[arg-type]
        ),
    )


def _apply_table_cell_ast(
    nodes: list,
    pending: dict[str, list[tuple[int, int, TableCell]]],
) -> None:
    if not pending:
        return
    matched: set[str] = set()
    for node in nodes:
        if not isinstance(node, Table):
            continue
        entries = pending.get(node.name)
        if not entries:
            continue
        matched.add(node.name)
        max_r = max(len(node.rows) - 1 if node.rows else 0, max(e[0] for e in entries))
        max_c = max(
            max((len(r) - 1 for r in node.rows), default=0) if node.rows else 0,
            max(e[1] for e in entries),
        )
        grid: list[list[TableCell]] = []
        for r in range(max_r + 1):
            row_cells: list[TableCell] = []
            for c in range(max_c + 1):
                cell = TableCell()
                if node.cells and r < len(node.cells) and c < len(node.cells[r]):
                    cell = node.cells[r][c].model_copy(deep=True)
                elif node.rows and r < len(node.rows) and c < len(node.rows[r]):
                    cell.text = str(node.rows[r][c])
                row_cells.append(cell)
            grid.append(row_cells)
        for r, c, cell in entries:
            if r < len(grid) and c < len(grid[r]):
                grid[r][c] = cell
        node.cells = grid
        node.rows = [
            [
                (grid[r][c].text if not grid[r][c].covered else "")
                for c in range(len(grid[r]))
            ]
            for r in range(len(grid))
        ]
    for tname, entries in pending.items():
        if tname in matched:
            continue
        max_r = max(e[0] for e in entries)
        max_c = max(e[1] for e in entries)
        grid = [[TableCell() for _ in range(max_c + 1)] for _ in range(max_r + 1)]
        for r, c, cell in entries:
            grid[r][c] = cell
        rows = [
            [
                (grid[r][c].text if not grid[r][c].covered else "")
                for c in range(len(grid[r]))
            ]
            for r in range(len(grid))
        ]
        nodes.insert(0, Table(rows=rows, cells=grid, name=tname))


def _split_fields(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _fmt_mm(val: float | None) -> str:
    if val is None:
        return ""
    return str(val)


def _b64_encode(data: bytes) -> str:
    if not data:
        return ""
    return base64.b64encode(data).decode("ascii")


def _b64_decode(raw: str) -> bytes:
    raw = (raw or "").strip()
    if not raw:
        return b""
    try:
        return base64.b64decode(raw, validate=False)
    except Exception:
        return b""


def import_xlsx(source_bytes: bytes, filename: str = "input.xlsx") -> DocumentModel:
    try:
        zf = zipfile.ZipFile(BytesIO(source_bytes))
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(f"XLSX 损坏: {filename}") from exc

    names = zf.namelist()
    if "xl/workbook.xml" not in names:
        zf.close()
        raise InvalidDocumentError(f"不是有效的 XLSX: {filename}")

    from aduport.config import settings
    from aduport.exporters.export_warnings import warn_export

    budget = ZipInflateBudget()
    sheet_names = _workbook_sheet_names(zf, budget)
    shared = _load_shared_strings(zf, budget)
    num_fmts, xf_num_fmts = _load_xlsx_style_maps(zf, budget)
    sheets = [p for p in _workbook_sheet_paths(zf, budget) if p in names]
    if not sheets:
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    sheet_cap = max(1, min(255, int(getattr(settings, "xlsx_max_sheets", 64) or 64)))
    truncated = max(0, len(sheets) - sheet_cap)
    sheets = sheets[:sheet_cap]
    nodes: list = []
    revision_count = 0
    control_count = 0
    textbox_count = 0
    ast_pivot_count = 0
    comment_count = 0
    footnote_count = 0
    math_count = 0
    image_count = 0
    html_count = 0
    code_count = 0
    blockquote_count = 0
    heading_count = 0
    paragraph_count = 0
    list_item_count = 0
    rule_count = 0
    table_cell_count = 0
    pending_table_cells: dict[str, list[tuple[int, int, TableCell]]] = {}
    has_ast_sheet = _META_AST in sheet_names
    deferred_content_rows: list[list[str]] | None = None
    for idx, sheet_path in enumerate(sheets):
        try:
            root = xml_safe.fromstring(budget.read(zf, sheet_path))
        except (ET.ParseError, Exception):
            continue
        rows, cells, has_formula, col_widths, row_heights = _parse_sheet(
            root, shared, num_fmts, xf_num_fmts
        )
        if not rows:
            continue
        sname = sheet_names[idx] if idx < len(sheet_names) else f"Sheet{idx + 1}"
        if sname == _META_AST or _rows_match_header(rows[0], _AST_HEADER):
            has_ast_sheet = True
            for row in rows[1:]:
                ntype = _row_cell(row, 0, "").lower().replace("-", "_")
                if ntype in ("table_cell", "cell"):
                    parsed = _parse_table_cell_ast_row(row)
                    if parsed:
                        tname, r_idx, c_idx, cell = parsed
                        pending_table_cells.setdefault(tname, []).append(
                            (r_idx, c_idx, cell)
                        )
                        table_cell_count += 1
                    continue
                node = _parse_ast_row(row)
                if node is None:
                    continue
                nodes.append(node)
                if isinstance(node, Revision):
                    revision_count += 1
                elif isinstance(node, ContentControl):
                    control_count += 1
                elif isinstance(node, TextBox):
                    textbox_count += 1
                elif isinstance(node, PivotTable):
                    ast_pivot_count += 1
                elif isinstance(node, Comment):
                    comment_count += 1
                elif isinstance(node, Footnote):
                    footnote_count += 1
                elif isinstance(node, MathBlock):
                    math_count += 1
                elif isinstance(node, Image):
                    image_count += 1
                elif isinstance(node, HtmlBlock):
                    html_count += 1
                elif isinstance(node, CodeBlock):
                    code_count += 1
                elif isinstance(node, Blockquote):
                    blockquote_count += 1
                elif isinstance(node, Heading):
                    heading_count += 1
                elif isinstance(node, Paragraph):
                    paragraph_count += 1
                elif isinstance(node, ListItem):
                    list_item_count += 1
                elif isinstance(node, HorizontalRule):
                    rule_count += 1
            continue
        if sname == _META_CELL_SPANS or _rows_match_header(
            rows[0],
            ["table", "row", "col", "text", "row_span", "col_span"],
        ):
            data_rows = (
                rows[1:]
                if _rows_match_header(
                    rows[0],
                    ["table", "row", "col", "text", "row_span", "col_span"],
                )
                else rows
            )
            for row in data_rows:
                parsed = _parse_table_cell_legacy_row(row)
                if parsed:
                    tname, r_idx, c_idx, cell = parsed
                    pending_table_cells.setdefault(tname, []).append(
                        (r_idx, c_idx, cell)
                    )
                    table_cell_count += 1
            continue
        if sname == _META_HEADINGS or _rows_match_header(rows[0], ["level", "text", "anchor"]):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                level = max(1, min(6, int(float(_row_cell(row, 0, "1") or 1))))
                text = _row_cell(row, 1, "")
                if not text.strip():
                    continue
                anchor = _row_cell(row, 2, "") or None
                nodes.append(Heading(level=level, text=text, anchor=anchor))
                heading_count += 1
            continue
        if sname == _META_PARAGRAPHS:
            data_rows = rows
            if data_rows and _rows_match_header(data_rows[0], ["text", "content"]):
                data_rows = rows[1:]
            for row in data_rows:
                text = _row_cell(row, 0, "")
                if not text.strip():
                    continue
                nodes.append(Paragraph(text=text))
                paragraph_count += 1
            continue
        if sname == _META_LIST_ITEMS or _rows_match_header(
            rows[0], ["text", "ordered", "level", "checked"]
        ):
            data_rows = rows[1:] if _rows_match_header(rows[0], ["text", "ordered", "level", "checked"]) else rows
            for row in data_rows:
                text = _row_cell(row, 0, "")
                if not text.strip():
                    continue
                children_text = _row_cell(row, 4, "") if len(row) > 4 else ""
                children = [
                    Paragraph(text=p)
                    for p in children_text.split("\n")
                    if p.strip()
                ]
                nodes.append(
                    ListItem(
                        text=text,
                        ordered=_row_bool(row, 1, False),
                        level=max(0, int(float(_row_cell(row, 2, "0") or 0))),
                        checked=_row_bool_opt(row, 3),
                        children=children,
                    )
                )
                list_item_count += 1
            continue
        if sname == _META_RULES:
            data_rows = rows
            if data_rows and _rows_match_header(data_rows[0], ["rule", "marker"]):
                data_rows = rows[1:]
            for row in data_rows:
                nodes.append(HorizontalRule())
                rule_count += 1
            continue
        if sname == _META_CODE or _rows_match_header(rows[0], ["lang", "content"]):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                nodes.append(
                    CodeBlock(
                        lang=_row_cell(row, 0, ""),
                        content=_row_cell(row, 1, ""),
                    )
                )
                code_count += 1
            continue
        if sname == _META_BLOCKQUOTE:
            data_rows = rows
            if data_rows and _rows_match_header(data_rows[0], ["text", "content"]):
                data_rows = rows[1:]
            for row in data_rows:
                text = _row_cell(row, 0, "")
                if not text.strip():
                    continue
                paras = [p.strip() for p in text.split("\n") if p.strip()]
                nodes.append(Blockquote(paragraphs=paras or [text]))
                blockquote_count += 1
            continue
        if sname == _META_IMAGES or _rows_match_header(
            rows[0], ["alt_text", "mime_type", "wrap", "data_b64"]
        ):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                nodes.append(_image_from_legacy_row(row))
                image_count += 1
            continue
        if sname == _META_HTML:
            for row in rows[1:]:
                text = _row_cell(row, 0, "")
                if not text.strip():
                    continue
                nodes.append(HtmlBlock(content=text))
                html_count += 1
            continue
        if sname == _META_MATH or _rows_match_header(rows[0], ["content", "display"]):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                nodes.append(
                    MathBlock(
                        content=_row_cell(row, 0, ""),
                        display=_row_bool(row, 1, True),
                    )
                )
                math_count += 1
            continue
        if sname == _META_FOOTNOTES or _rows_match_header(rows[0], ["id", "text"]):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                fid = _row_cell(row, 0, "")
                if not fid:
                    fid = str(footnote_count + 1)
                nodes.append(
                    Footnote(
                        id=fid,
                        text=_row_cell(row, 1, ""),
                    )
                )
                footnote_count += 1
            continue
        if sname == _META_COMMENTS or _rows_match_header(
            rows[0], ["author", "anchor", "text"]
        ):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                nodes.append(
                    Comment(
                        author=_row_cell(row, 0, "reviewer"),
                        anchor=_row_cell(row, 1, ""),
                        text=_row_cell(row, 2, ""),
                    )
                )
                comment_count += 1
            continue
        if sname == _META_REVISIONS or _rows_match_header(
            rows[0], ["change", "author", "anchor", "text"]
        ):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                nodes.append(
                    Revision(
                        change=(
                            raw_change
                            if (raw_change := _row_cell(row, 0, "insert")) in ("insert", "delete")
                            else "insert"
                        ),
                        author=_row_cell(row, 1, ""),
                        anchor=_row_cell(row, 2, ""),
                        text=_row_cell(row, 3, ""),
                    )
                )
                revision_count += 1
            continue
        if sname == _META_CONTROLS or _rows_match_header(
            rows[0], ["tag", "alias", "control_type", "text"]
        ):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                nodes.append(
                    ContentControl(
                        tag=_row_cell(row, 0, ""),
                        alias=_row_cell(row, 1, ""),
                        control_type=_row_cell(row, 2, ""),
                        text=_row_cell(row, 3, ""),
                    )
                )
                control_count += 1
            continue
        if sname == _META_TEXTBOXES or _rows_match_header(
            rows[0], ["source", "text", "x_mm", "y_mm", "width_mm", "height_mm"]
        ):
            for row in rows[1:]:
                if not any(str(c).strip() for c in row):
                    continue
                nodes.append(
                    TextBox(
                        source=_row_cell(row, 0, ""),
                        text=_row_cell(row, 1, ""),
                        x_mm=_row_float(row, 2),
                        y_mm=_row_float(row, 3),
                        width_mm=_row_float(row, 4),
                        height_mm=_row_float(row, 5),
                    )
                )
                textbox_count += 1
            continue
        if sname == _META_CONTENT or (
            rows
            and _rows_match_header(rows[0], ["kind", "text"])
        ):
            # Prefer _AST as typed source of truth; Content is a searchable mirror.
            deferred_content_rows = rows
            continue
        use_cells = bool(cells) and (
            has_formula
            or any(
                c.covered or c.row_span > 1 or c.col_span > 1 or (c.formula or "").strip()
                for row in cells
                for c in row
            )
        )
        tbl = Table(
            rows=rows,
            cells=cells if use_cells else [],
            name=sname,
            header_rows=1 if len(rows) > 1 else 0,
            column_widths=col_widths,
            row_heights=row_heights,
        )
        nodes.append(tbl)
    if deferred_content_rows is not None and not has_ast_sheet:
        content_nodes = _nodes_from_content_sheet(deferred_content_rows)
        if (
            content_nodes
            and isinstance(content_nodes[0], Heading)
            and nodes
            and not isinstance(nodes[0], Heading)
        ):
            nodes[0:0] = content_nodes
        else:
            nodes.extend(content_nodes)
        for node in content_nodes:
            if isinstance(node, Heading):
                heading_count += 1
            elif isinstance(node, Paragraph):
                paragraph_count += 1
            elif isinstance(node, ListItem):
                list_item_count += 1
            elif isinstance(node, CodeBlock):
                code_count += 1
            elif isinstance(node, Blockquote):
                blockquote_count += 1
            elif isinstance(node, Footnote):
                footnote_count += 1
            elif isinstance(node, Comment):
                comment_count += 1
            elif isinstance(node, Revision):
                revision_count += 1
            elif isinstance(node, ContentControl):
                control_count += 1
            elif isinstance(node, TextBox):
                textbox_count += 1
            elif isinstance(node, HorizontalRule):
                rule_count += 1
            elif isinstance(node, MathBlock):
                math_count += 1
            elif isinstance(node, Image):
                image_count += 1
            elif isinstance(node, HtmlBlock):
                html_count += 1
    existing_pivot_names = {n.name for n in nodes if isinstance(n, PivotTable) and n.name}
    for p in _parse_pivot_tables(zf, budget):
        if p.name and p.name in existing_pivot_names:
            continue
        nodes.append(p)
        if p.name:
            existing_pivot_names.add(p.name)
    for ch in _parse_charts(zf, budget):
        nodes.append(ch)
    _apply_table_cell_ast(nodes, pending_table_cells)
    zf.close()
    pivot_names = [n.name for n in nodes if isinstance(n, PivotTable) and n.name]
    if not pivot_names:
        pivot_names = _pivot_table_names(zf_names=names)
    policy = "values_and_formulas" if any(
        isinstance(n, Table) and n.cells for n in nodes
    ) else "values_only"
    note = "公式文本保留在 TableCell.formula；透视表为 PivotTable AST 节点"
    if pivot_names:
        note += f"；透视表: {', '.join(pivot_names[:5])}"
    if revision_count:
        note += f"；修订: {revision_count} 行"
    if control_count:
        note += f"；内容控件: {control_count} 行"
    if textbox_count:
        note += f"；文本框: {textbox_count} 行"
    if ast_pivot_count:
        note += f"；透视(_AST): {ast_pivot_count} 行"
    if comment_count:
        note += f"；批注: {comment_count} 行"
    if footnote_count:
        note += f"；脚注: {footnote_count} 行"
    if math_count:
        note += f"；公式: {math_count} 行"
    if image_count:
        note += f"；图片: {image_count} 行"
    if html_count:
        note += f"；HTML: {html_count} 行"
    if code_count:
        note += f"；代码: {code_count} 行"
    if blockquote_count:
        note += f"；引用: {blockquote_count} 行"
    if heading_count:
        note += f"；标题: {heading_count} 行"
    if paragraph_count:
        note += f"；段落: {paragraph_count} 行"
    if list_item_count:
        note += f"；列表: {list_item_count} 行"
    if rule_count:
        note += f"；分隔线: {rule_count} 行"
    if table_cell_count:
        note += f"；单元格 span: {table_cell_count} 行"
    if not nodes:
        raise CorruptedFileError(f"XLSX 无工作表数据: {filename}")
    meta: dict = {
            "importer": "xlsx",
            "export_policy": policy,
            "note": note,
            "sheet_names": [t.name for t in nodes if isinstance(t, Table)],
            "pivot_tables": pivot_names,
            "revisions_imported": revision_count,
            "content_controls_imported": control_count,
            "textboxes_imported": textbox_count,
            "pivots_ast_imported": ast_pivot_count,
            "comments_imported": comment_count,
            "footnotes_imported": footnote_count,
            "math_imported": math_count,
            "images_imported": image_count,
            "html_blocks_imported": html_count,
            "code_blocks_imported": code_count,
            "blockquotes_imported": blockquote_count,
            "headings_imported": heading_count,
            "paragraphs_imported": paragraph_count,
            "list_items_imported": list_item_count,
            "horizontal_rules_imported": rule_count,
            "table_cells_imported": table_cell_count,
            "xlsx_max_sheets": sheet_cap,
    }
    if truncated:
        meta["sheets_truncated"] = truncated
        meta["sheets_total"] = sheet_cap + truncated
    from aduport.core.title_source import resolve_title

    model = DocumentModel(
        title=resolve_title("", nodes, filename=filename),
        nodes=nodes,
        metadata=meta,
    )
    if truncated:
        warn_export(
            model,
            f"xlsx import: truncated {truncated} sheet(s) beyond xlsx_max_sheets={sheet_cap}",
        )
    from aduport.importers.zip_images import append_zip_images

    append_zip_images(model, source_bytes, ("xl/media/",))
    return model


def _parse_ast_row(row: list[str]):
    """Parse unified _AST row into AST meta nodes."""
    ntype = _row_cell(row, 0, "").lower().replace("-", "_")
    if not ntype:
        return None
    if ntype == "revision":
        raw_change = _row_cell(row, 1, "insert")
        return Revision(
            change=raw_change if raw_change in ("insert", "delete") else "insert",
            author=_row_cell(row, 2, ""),
            anchor=_row_cell(row, 3, ""),
            text=_row_cell(row, 4, ""),
        )
    if ntype in ("content_control", "control"):
        return ContentControl(
            tag=_row_cell(row, 1, ""),
            alias=_row_cell(row, 2, ""),
            control_type=_row_cell(row, 3, ""),
            text=_row_cell(row, 4, ""),
        )
    if ntype in ("text_box", "textbox"):
        return TextBox(
            source=_row_cell(row, 1, ""),
            text=_row_cell(row, 2, ""),
            x_mm=_row_float(row, 3),
            y_mm=_row_float(row, 4),
            width_mm=_row_float(row, 5),
            height_mm=_row_float(row, 6),
        )
    if ntype in ("pivot_table", "pivot"):
        return PivotTable(
            name=_row_cell(row, 1, ""),
            source_range=_row_cell(row, 2, ""),
            location=_row_cell(row, 3, ""),
            row_fields=_split_fields(_row_cell(row, 4, "")),
            col_fields=_split_fields(_row_cell(row, 5, "")),
            data_fields=_split_fields(_row_cell(row, 6, "")),
        )
    if ntype == "comment":
        return Comment(
            author=_row_cell(row, 1, "reviewer"),
            anchor=_row_cell(row, 2, ""),
            text=_row_cell(row, 3, ""),
        )
    if ntype == "footnote":
        fid = _row_cell(row, 1, "") or "1"
        return Footnote(id=fid, text=_row_cell(row, 2, ""))
    if ntype in ("math_block", "math"):
        return MathBlock(
            content=_row_cell(row, 1, ""),
            display=_row_bool(row, 2, True),
        )
    if ntype == "image":
        return _image_from_ast_row(row)
    if ntype in ("html_block", "html"):
        content = _row_cell(row, 1, "")
        return HtmlBlock(content=content) if content else None
    if ntype in ("code_block", "code"):
        return CodeBlock(
            lang=_row_cell(row, 1, ""),
            content=_row_cell(row, 2, ""),
        )
    if ntype == "blockquote":
        text = _row_cell(row, 1, "")
        if not text.strip():
            return None
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        return Blockquote(paragraphs=paras or [text])
    if ntype == "heading":
        level = max(1, min(6, int(float(_row_cell(row, 1, "1") or 1))))
        text = _row_cell(row, 2, "")
        if not text.strip():
            return None
        anchor = _row_cell(row, 3, "") or None
        return Heading(level=level, text=text, anchor=anchor)
    if ntype == "paragraph":
        text = _row_cell(row, 1, "")
        return Paragraph(text=text) if text.strip() else None
    if ntype in ("list_item", "list"):
        text = _row_cell(row, 1, "")
        if not text.strip():
            return None
        children_text = _row_cell(row, 5, "")
        children = [
            Paragraph(text=p) for p in children_text.split("\n") if p.strip()
        ]
        return ListItem(
            text=text,
            ordered=_row_bool(row, 2, False),
            level=max(0, int(float(_row_cell(row, 3, "0") or 0))),
            checked=_row_bool_opt(row, 4),
            children=children,
        )
    if ntype in ("horizontal_rule", "hr", "rule"):
        return HorizontalRule()
    return None


def _image_wrap(raw: str) -> str:
    val = (raw or "inline").strip().lower()
    if val in ("left", "right"):
        return val
    return "inline"


def _image_from_ast_row(row: list[str]) -> Image:
    data = _b64_decode(_row_cell(row, 4, ""))
    url = _row_cell(row, 5, "") or None
    return Image(
        data=data or b"\x00",
        alt_text=_row_cell(row, 1, ""),
        mime_type=_row_cell(row, 2, "image/png") or "image/png",
        wrap=_image_wrap(_row_cell(row, 3, "inline")),
        url=url,
    )


def _image_from_legacy_row(row: list[str]) -> Image:
    data = _b64_decode(_row_cell(row, 3, ""))
    return Image(
        data=data or b"\x00",
        alt_text=_row_cell(row, 0, ""),
        mime_type=_row_cell(row, 1, "image/png") or "image/png",
        wrap=_image_wrap(_row_cell(row, 2, "inline")),
    )


def _rows_match_header(row: list[str], header: list[str]) -> bool:
    if len(row) < len(header):
        return False
    return [str(c).strip().lower() for c in row[: len(header)]] == [
        h.lower() for h in header
    ]


def _row_raw(row: list[str], idx: int) -> str:
    """Cell text with leading spaces kept. List depth is stored as indent."""
    if idx >= len(row) or row[idx] is None:
        return ""
    return str(row[idx])


def _row_cell(row: list[str], idx: int, default: str) -> str:
    if idx >= len(row):
        return default
    val = str(row[idx]).strip()
    return val if val else default


def _row_float(row: list[str], idx: int) -> float | None:
    raw = _row_cell(row, idx, "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _row_bool_opt(row: list[str], idx: int) -> bool | None:
    raw = _row_cell(row, idx, "").lower()
    if not raw:
        return None
    if raw in ("1", "true", "yes", "y"):
        return True
    if raw in ("0", "false", "no", "n"):
        return False
    return None


def _row_bool(row: list[str], idx: int, default: bool) -> bool:
    raw = _row_cell(row, idx, "").lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "y"):
        return True
    if raw in ("0", "false", "no", "n"):
        return False
    return default


def _workbook_sheet_names(zf: zipfile.ZipFile, budget: ZipInflateBudget | None = None) -> list[str]:
    try:
        raw = budget.read(zf, "xl/workbook.xml") if budget else zf.read("xl/workbook.xml")
        root = xml_safe.fromstring(raw)
    except (ET.ParseError, KeyError, Exception):
        return []
    out: list[str] = []
    for sheet in root.findall("m:sheets/m:sheet", _NS):
        name = sheet.get("name") or ""
        if name:
            out.append(name)
    return out


_WS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
)


def _workbook_sheet_paths(
    zf: zipfile.ZipFile, budget: ZipInflateBudget | None = None
) -> list[str]:
    """Worksheet part paths in workbook.xml order (P2-5; not sorted sheetN.xml)."""
    names = set(zf.namelist())
    fallback = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    try:
        rels_raw = (
            budget.read(zf, "xl/_rels/workbook.xml.rels")
            if budget
            else zf.read("xl/_rels/workbook.xml.rels")
        )
        wb_raw = budget.read(zf, "xl/workbook.xml") if budget else zf.read("xl/workbook.xml")
        rels_root = xml_safe.fromstring(rels_raw)
        wb_root = xml_safe.fromstring(wb_raw)
    except (KeyError, ET.ParseError, Exception):
        return fallback

    rid_to_path: dict[str, str] = {}
    for rel in rels_root:
        if rel.get("Type") != _WS_REL_TYPE:
            continue
        rid = rel.get("Id") or ""
        target = (rel.get("Target") or "").replace("\\", "/")
        if not rid or not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        elif target.startswith("xl/"):
            path = target
        else:
            path = f"xl/{target.lstrip('/')}"
        if path in names:
            rid_to_path[rid] = path

    ordered: list[str] = []
    for sheet in wb_root.findall("m:sheets/m:sheet", _NS):
        rid = sheet.get(f"{{{_R}}}id") or sheet.get("r:id") or ""
        path = rid_to_path.get(rid)
        if path and path not in ordered:
            ordered.append(path)
    return ordered or fallback


def _table_value_matrix(table: Table) -> list[list[str]]:
    """Prefer span grid with anchor resolution; else plain rows."""
    if table.cells:
        grid = table.cells
        out: list[list[str]] = []
        for r_idx, row in enumerate(grid):
            out_row: list[str] = []
            for c_idx, cell in enumerate(row):
                if cell is None:
                    out_row.append("")
                    continue
                if cell.covered:
                    ar, ac = int(cell.origin_row or 0), int(cell.origin_col or 0)
                    if 0 <= ar < len(grid) and 0 <= ac < len(grid[ar]):
                        anchor = grid[ar][ac]
                        out_row.append(anchor.text if anchor is not None else "")
                    else:
                        out_row.append("")
                else:
                    out_row.append(cell.text or "")
            out.append(out_row)
        return out
    if table.rows:
        return [[str(c) for c in row] for row in table.rows]
    return [[""]]


_NUM_CELL_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")
_DATE_CELL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")


def _sheet_cell_xml(ref: str, val: object, *, formula: str, style: str) -> str:
    """Emit typed OOXML cell: formula / number / date-as-text / inlineStr."""
    if formula:
        return (
            f'<c r="{ref}"{style}>'
            f"<f>{escape(formula)}</f>"
            f"<v>{escape(str(val))}</v></c>"
        )
    text = "" if val is None else str(val)
    stripped = text.strip()
    if stripped and _NUM_CELL_RE.match(stripped):
        # Keep leading-zero IDs (011) as text; allow 0 / decimals / scientific.
        leading_zero_id = (
            stripped.startswith("0")
            and len(stripped) > 1
            and "." not in stripped
            and "e" not in stripped.lower()
        )
        # Excel stores <v> numbers as IEEE double: integers above 15 significant
        # digits are silently rounded on open (IDs / 银行卡号 / 身份证号 → 数据损坏,
        # report P1-8). Emit them as inline strings instead.
        long_int = False
        if not leading_zero_id and "." not in stripped and "e" not in stripped.lower():
            long_int = len(stripped.lstrip("-")) > 15
        if not leading_zero_id and not long_int:
            return f'<c r="{ref}"{style}><v>{escape(stripped)}</v></c>'
    if stripped and _DATE_CELL_RE.match(stripped):
        return (
            f'<c r="{ref}"{style} t="inlineStr">'
            f"<is><t>{escape(stripped)}</t></is></c>"
        )
    return (
        f'<c r="{ref}"{style} t="inlineStr"><is><t>{escape(text)}</t></is></c>'
    )


def _sheet_xml(table: Table, *, drawing_r_id: str | None = None) -> str:
    matrix = _table_value_matrix(table)
    header_rows = max(0, int(table.header_rows or 0))
    cell_grid = table.cells if table.cells else None
    ncols = max((len(r) for r in matrix), default=1)
    # Prefer explicit column_widths (import round-trip); else heuristic.
    col_widths = [10.0] * ncols
    if table.column_widths:
        for i, w in enumerate(table.column_widths[:ncols]):
            try:
                col_widths[i] = max(1.0, min(255.0, float(w)))
            except (TypeError, ValueError):
                pass
    else:
        for row in matrix:
            for c_idx, val in enumerate(row):
                if c_idx >= ncols:
                    break
                w = min(48.0, max(8.0, len(str(val)) * 1.15 + 2.0))
                if w > col_widths[c_idx]:
                    col_widths[c_idx] = w
    cols_xml = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="{w:.2f}" customWidth="1"/>'
        for i, w in enumerate(col_widths)
    )

    row_xml: list[str] = []
    merges: list[str] = []
    for r_idx, row in enumerate(matrix, start=1):
        cells_xml: list[str] = []
        style = ' s="1"' if r_idx <= header_rows else ""
        for c_idx, val in enumerate(row):
            ref = f"{_col_name(c_idx)}{r_idx}"
            formula = ""
            tc = None
            if cell_grid and r_idx - 1 < len(cell_grid) and c_idx < len(cell_grid[r_idx - 1]):
                tc = cell_grid[r_idx - 1][c_idx]
                if tc is not None and tc.covered:
                    continue
                formula = (tc.formula or "").strip() if tc is not None else ""
                if tc is not None:
                    rs = max(1, int(tc.row_span or 1))
                    cs = max(1, int(tc.col_span or 1))
                    if rs > 1 or cs > 1:
                        end = f"{_col_name(c_idx + cs - 1)}{r_idx + rs - 1}"
                        merges.append(f"{ref}:{end}")
            cells_xml.append(_sheet_cell_xml(ref, val, formula=formula, style=style))
        ht_attr = ""
        if table.row_heights and r_idx - 1 < len(table.row_heights):
            try:
                ht = float(table.row_heights[r_idx - 1])
                if ht > 0:
                    ht_attr = f' ht="{ht:.2f}" customHeight="1"'
            except (TypeError, ValueError):
                pass
        row_xml.append(f'<row r="{r_idx}"{ht_attr}>{"".join(cells_xml)}</row>')

    merge_xml = ""
    if merges:
        # de-dupe while preserving order
        seen: set[str] = set()
        uniq: list[str] = []
        for m in merges:
            if m not in seen:
                seen.add(m)
                uniq.append(m)
        merge_xml = (
            f'<mergeCells count="{len(uniq)}">'
            + "".join(f'<mergeCell ref="{m}"/>' for m in uniq)
            + "</mergeCells>"
        )
    drawing_xml = ""
    ns_r = ""
    if drawing_r_id:
        ns_r = f' xmlns:r="{_R}"'
        drawing_xml = f'<drawing r:id="{escape(drawing_r_id)}"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<worksheet xmlns="{_M}"{ns_r}>'
        f"<cols>{cols_xml}</cols>"
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        f"{merge_xml}"
        f"{drawing_xml}"
        "</worksheet>"
    )


def _sheet_drawing_rels() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
        'Target="../drawings/drawing1.xml"/>'
        "</Relationships>"
    )


def _drawing_chart_rels(n: int) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    ]
    for i in range(1, n + 1):
        parts.append(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" '
            f'Target="../charts/chart{i}.xml"/>'
        )
    parts.append("</Relationships>")
    return "".join(parts)


def _charts_drawing_xml(n: int) -> str:
    """Stack chart graphicFrames vertically on sheet1."""
    xdr = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    c = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    anchors: list[str] = []
    for i in range(1, n + 1):
        row0 = (i - 1) * 16
        row1 = row0 + 14
        anchors.append(
            f'<xdr:twoCellAnchor>'
            f'<xdr:from><xdr:col>0</xdr:col><xdr:colOff>0</xdr:colOff>'
            f"<xdr:row>{row0}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
            f'<xdr:to><xdr:col>8</xdr:col><xdr:colOff>0</xdr:colOff>'
            f"<xdr:row>{row1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
            f'<xdr:graphicFrame>'
            f'<xdr:nvGraphicFramePr>'
            f'<xdr:cNvPr id="{i}" name="Chart {i}"/>'
            f"<xdr:cNvGraphicFramePr/>"
            f"</xdr:nvGraphicFramePr>"
            f"<xdr:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/></xdr:xfrm>"
            f'<a:graphic><a:graphicData uri="{c}">'
            f'<c:chart xmlns:c="{c}" xmlns:r="{_R}" r:id="rId{i}"/>'
            f"</a:graphicData></a:graphic>"
            f"</xdr:graphicFrame>"
            f"<xdr:clientData/>"
            f"</xdr:twoCellAnchor>"
        )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<xdr:wsDr xmlns:xdr="{xdr}" xmlns:a="{a}">'
        f'{"".join(anchors)}</xdr:wsDr>'
    )


def _chart_series_values(ser: dict) -> list[str]:
    raw = ser.get("values") if isinstance(ser, dict) else None
    if raw is None and isinstance(ser, dict):
        raw = ser.get("data")
    if not isinstance(raw, (list, tuple)):
        return ["0"]
    out: list[str] = []
    for v in raw:
        try:
            out.append(str(float(v)))
        except (TypeError, ValueError):
            out.append("0")
    return out or ["0"]


def _chart_space_xml(ch: Chart) -> str:
    """Minimal DrawingML chart with literal caches (opens in Excel / LibreOffice)."""
    c_ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    title = escape((ch.title or ch.chart_type or "Chart")[:120])
    ctype = (ch.chart_type or "bar").lower().strip()
    cats = [str(x) for x in (ch.categories or [])] or ["A", "B"]
    series_list = list(ch.series or []) or [{"name": "Series1", "values": [1, 2]}]

    cat_pts = "".join(
        f'<c:pt idx="{i}"><c:v>{escape(cats[i] if i < len(cats) else f"C{i + 1}")}</c:v></c:pt>'
        for i in range(len(cats))
    )
    cat_block = (
        f"<c:cat><c:strLit><c:ptCount val=\"{len(cats)}\"/>{cat_pts}</c:strLit></c:cat>"
    )

    ser_xml: list[str] = []
    for si, ser in enumerate(series_list[:12]):
        if not isinstance(ser, dict):
            continue
        name = escape(str(ser.get("name") or f"S{si + 1}")[:80])
        vals = _chart_series_values(ser)
        # Align length with categories
        while len(vals) < len(cats):
            vals.append("0")
        vals = vals[: max(len(cats), 1)]
        val_pts = "".join(
            f'<c:pt idx="{i}"><c:v>{escape(vals[i])}</c:v></c:pt>' for i in range(len(vals))
        )
        ser_xml.append(
            f"<c:ser>"
            f'<c:idx val="{si}"/><c:order val="{si}"/>'
            f"<c:tx><c:v>{name}</c:v></c:tx>"
            f"{cat_block}"
            f"<c:val><c:numLit><c:ptCount val=\"{len(vals)}\"/>{val_pts}</c:numLit></c:val>"
            f"</c:ser>"
        )

    grouping = "clustered"
    if ctype in ("line",):
        plot = (
            f"<c:lineChart><c:grouping val=\"standard\"/>"
            f'{"".join(ser_xml)}'
            f'<c:axId val="1"/><c:axId val="2"/></c:lineChart>'
        )
    elif ctype in ("area",):
        plot = (
            f"<c:areaChart><c:grouping val=\"standard\"/>"
            f'{"".join(ser_xml)}'
            f'<c:axId val="1"/><c:axId val="2"/></c:areaChart>'
        )
    elif ctype in ("pie", "doughnut"):
        # pie uses first series only
        plot = (
            f"<c:pieChart>{ser_xml[0] if ser_xml else ''}"
            f"<c:firstSliceAng val=\"0\"/></c:pieChart>"
        )
    elif ctype in ("scatter", "xy"):
        # degrade scatter to line with markers for literal cats
        plot = (
            f"<c:lineChart><c:grouping val=\"standard\"/>"
            f'{"".join(ser_xml)}'
            f'<c:axId val="1"/><c:axId val="2"/></c:lineChart>'
        )
    else:
        plot = (
            f'<c:barChart><c:barDir val="col"/><c:grouping val="{grouping}"/>'
            f'{"".join(ser_xml)}'
            f'<c:axId val="1"/><c:axId val="2"/></c:barChart>'
        )

    axes = ""
    if ctype not in ("pie", "doughnut"):
        x_title = escape((ch.x_axis_title or "").strip())
        y_title = escape((ch.y_axis_title or "").strip())
        x_title_xml = (
            f"<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>"
            f'<a:p><a:r><a:t>{x_title}</a:t></a:r></a:p>'
            f"</c:rich></c:tx><c:overlay val=\"0\"/></c:title>"
            if x_title
            else ""
        )
        y_title_xml = (
            f"<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>"
            f'<a:p><a:r><a:t>{y_title}</a:t></a:r></a:p>'
            f"</c:rich></c:tx><c:overlay val=\"0\"/></c:title>"
            if y_title
            else ""
        )
        axes = (
            f'<c:catAx><c:axId val="1"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
            f'<c:axPos val="b"/><c:crossAx val="2"/>{x_title_xml}</c:catAx>'
            f'<c:valAx><c:axId val="2"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
            f'<c:axPos val="l"/><c:crossAx val="1"/>{y_title_xml}</c:valAx>'
        )

    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<c:chartSpace xmlns:c="{c_ns}" xmlns:a="{a_ns}" xmlns:r="{_R}">'
        f"<c:chart>"
        f"<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>"
        f'<a:p><a:pPr><a:defRPr/></a:pPr><a:r><a:t>{title}</a:t></a:r></a:p>'
        f"</c:rich></c:tx><c:overlay val=\"0\"/></c:title>"
        f"<c:plotArea><c:layout/>{plot}{axes}</c:plotArea>"
        f"</c:chart>"
        f"</c:chartSpace>"
    )


def _col_name(idx: int) -> str:
    n = idx + 1
    letters = []
    while n:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def _load_shared_strings(zf: zipfile.ZipFile, budget: ZipInflateBudget | None = None) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    try:
        raw = (
            budget.read(zf, "xl/sharedStrings.xml")
            if budget
            else zf.read("xl/sharedStrings.xml")
        )
        root = xml_safe.fromstring(raw)
    except (ET.ParseError, KeyError, Exception):
        return []
    out: list[str] = []
    for si in root.findall("m:si", _NS):
        texts = [t.text or "" for t in si.findall(".//m:t", _NS)]
        out.append("".join(texts))
    return out


def _promote_inline(text: str):
    from aduport.importers.markdown_rehydrate import promote_inline

    return promote_inline(text)


def _nodes_from_content_sheet(rows: list[list[str]]) -> list:
    """Rebuild AST nodes from the visible Content sheet (kind/text columns)."""
    data = rows
    if data and _rows_match_header(data[0], ["kind", "text"]):
        data = data[1:]
    out: list = []
    for row in data:
        kind = _row_cell(row, 0, "").strip().lower()
        text = _row_raw(row, 1)
        if not kind and not text.strip():
            continue
        if kind in ("heading", "h1", "h2", "h3", "h4", "h5", "h6"):
            level = 2
            body = text
            m = re.match(r"^(#{1,6})\s+(.*)$", text.strip())
            if m:
                level = len(m.group(1))
                body = m.group(2)
            elif kind.startswith("h") and kind[1:].isdigit():
                level = max(1, min(6, int(kind[1:])))
            out.append(Heading(level=level, text=_promote_inline(body)))
        elif kind in ("paragraph", "p", ""):
            if text.strip():
                out.append(Paragraph(text=_promote_inline(text)))
        elif kind in ("list_item", "list", "li"):
            checked = None
            ordered = False
            level = 0
            body = text.strip()
            m = re.match(r"^(\s*)(\[x\]|\[ \]|[-*+]|\d+\.)\s+(.*)$", text, re.I)
            if m:
                mark = m.group(2).lower()
                body = m.group(3).strip()
                level = len(m.group(1).replace("\t", "    ")) // 2
                if mark in ("[x]", "[ ]"):
                    checked = mark == "[x]"
                elif mark.endswith("."):
                    ordered = True
            out.append(
                ListItem(
                    text=_promote_inline(body),
                    ordered=ordered,
                    checked=checked,
                    level=level,
                )
            )
        elif kind in ("code_block", "code"):
            lang = ""
            content = text
            if text.startswith("```"):
                parts = text.split("\n", 1)
                lang = parts[0].lstrip("`").strip()
                content = parts[1] if len(parts) > 1 else ""
                if content.endswith("```"):
                    content = content[: -3].rstrip("\n")
            out.append(CodeBlock(lang=lang, content=content))
        elif kind in ("blockquote", "quote"):
            body = re.sub(r"(?m)^>\s?", "", text).strip()
            out.append(Blockquote(paragraphs=[_promote_inline(body)] if body else []))
        elif kind in ("footnote",):
            m = re.match(r"^\[\^([^\]]+)\]:\s*(.*)$", text.strip())
            if m:
                out.append(Footnote(id=m.group(1), text=m.group(2)))
            elif text.strip():
                out.append(Footnote(id="1", text=text.strip()))
        elif kind in ("comment",):
            m = re.match(r"^\[Comment\s+([^\]]*)\]\s*(.*)$", text.strip())
            if m:
                out.append(Comment(author=m.group(1).strip(), text=m.group(2)))
            elif text.strip():
                out.append(Comment(author="", text=text.strip()))
        elif kind in ("revision",):
            m = re.match(r"^\[(ins|del)\s+([^\]]*)\]\s*(.*)$", text.strip())
            if m:
                out.append(
                    Revision(
                        change="insert" if m.group(1) == "ins" else "delete",
                        author=m.group(2).strip(),
                        text=m.group(3),
                    )
                )
        elif kind in ("text_box", "textbox"):
            body = text.strip()
            source = ""
            m = re.match(r"^\[TextBox(?:\|([^\]]*))?\]\s*(.*)$", body)
            if m:
                source = (m.group(1) or "").strip()
                body = m.group(2)
            else:
                body = re.sub(r"^\[TextBox\]\s*", "", body)
            out.append(TextBox(text=body, source=source))
        elif kind in ("horizontal_rule", "hr", "rule"):
            out.append(HorizontalRule())
        elif kind in ("math_block", "math"):
            body = text.strip()
            body = re.sub(r"^\$\$\s*", "", body)
            body = re.sub(r"\s*\$\$$", "", body)
            out.append(MathBlock(content=body))
        elif kind in ("image",):
            alt = text
            m = re.match(r"^\[Image:\s*(.*)\]$", text.strip())
            if m:
                alt = m.group(1)
            out.append(Image(data=b"", alt_text=alt, mime_type="image/png"))
        elif kind in ("html_block", "html"):
            out.append(HtmlBlock(content=text))
        elif kind in ("content_control", "contentcontrol"):
            body = text.strip()
            tag, alias, ctype = "", "", ""
            m = re.match(
                r"^\[ContentControl\s+([^|\]]*)\|([^|\]]*)\|([^\]]*)\]\s*(.*)$",
                body,
            )
            if m:
                tag, alias, ctype, body = (
                    m.group(1).strip(),
                    m.group(2).strip(),
                    m.group(3).strip(),
                    m.group(4),
                )
            out.append(
                ContentControl(tag=tag, alias=alias, control_type=ctype, text=body)
            )
        elif kind in ("pivot_table", "pivottable"):
            # Pivot OOXML parts are authoritative; Content row is a searchable hint only.
            continue
        elif text.strip():
            out.append(Paragraph(text=text))
    return out


def _parse_sheet(
    root: ET.Element,
    shared: list[str],
    num_fmts: dict[int, str] | None = None,
    xf_num_fmts: list[int] | None = None,
) -> tuple[list[list[str]], list[list[TableCell]], bool, list[float], list[float]]:
    rows_map: dict[int, dict[int, tuple[str, str]]] = {}
    row_heights_map: dict[int, float] = {}
    for row in root.findall(".//m:row", _NS):
        r_attr = row.get("r")
        r_idx = int(r_attr) if r_attr and r_attr.isdigit() else (max(rows_map) + 1 if rows_map else 1)
        ht = row.get("ht")
        if ht:
            try:
                row_heights_map[r_idx] = float(ht)
            except ValueError:
                pass
        next_col = 0
        for c in row.findall("m:c", _NS):
            ref = c.get("r")
            if ref:
                m = re.match(r"([A-Z]+)", ref)
                col = _col_letters_to_index(m.group(1)) if m else next_col
            else:
                col = next_col
            next_col = col + 1
            val = _cell_value(c, shared, num_fmts=num_fmts, xf_num_fmts=xf_num_fmts)
            formula_el = c.find("m:f", _NS)
            formula = (formula_el.text or "").strip() if formula_el is not None else ""
            rows_map.setdefault(r_idx, {})[col] = (val, formula)
    if not rows_map:
        return [], [], False, [], []
    max_col = max((max(cols) for cols in rows_map.values() if cols), default=0)
    result: list[list[str]] = []
    cell_grid: list[list[TableCell]] = []
    has_formula = False
    for r in sorted(rows_map):
        row_vals: list[str] = []
        row_cells: list[TableCell] = []
        for c in range(max_col + 1):
            val, formula = rows_map[r].get(c, ("", ""))
            row_vals.append(val)
            kind = "formula" if formula else "value"
            if formula:
                has_formula = True
            row_cells.append(TableCell(text=val, formula=formula, cell_kind=kind))
        result.append(row_vals)
        cell_grid.append(row_cells)

    # Apply native <mergeCells> so span survives without _AST sidecar (v8 P1).
    _apply_merge_cells(root, result, cell_grid)

    col_widths = _parse_col_widths(root, max_col + 1)
    row_heights = [
        row_heights_map.get(r, 0.0) for r in sorted(rows_map)
    ]
    return result, cell_grid, has_formula, col_widths, row_heights


def _parse_col_widths(root: ET.Element, ncols: int) -> list[float]:
    """Read ``<cols><col width=…/>`` into a per-column list (1-based OOXML)."""
    widths = [0.0] * max(1, ncols)
    for col in root.findall(".//m:col", _NS) or root.findall(
        ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}col"
    ):
        try:
            mn = int(col.get("min") or "1")
            mx = int(col.get("max") or mn)
            w = float(col.get("width") or "0")
        except ValueError:
            continue
        if w <= 0:
            continue
        for i in range(mn - 1, mx):
            if 0 <= i < len(widths):
                widths[i] = w
            elif i >= len(widths):
                while len(widths) <= i:
                    widths.append(0.0)
                widths[i] = w
    return widths


def _col_letters_to_index(letters: str) -> int:
    col = 0
    for ch in letters.upper():
        if "A" <= ch <= "Z":
            col = col * 26 + (ord(ch) - 64)
    return max(0, col - 1)


def _parse_a1_cell(ref: str) -> tuple[int, int] | None:
    m = re.match(r"^([A-Za-z]+)(\d+)$", (ref or "").strip())
    if not m:
        return None
    return int(m.group(2)) - 1, _col_letters_to_index(m.group(1))


def _apply_merge_cells(
    root: ET.Element,
    rows: list[list[str]],
    cell_grid: list[list[TableCell]],
) -> None:
    """Mutate ``cell_grid`` / ``rows`` from worksheet ``<mergeCells>``."""
    merges = root.findall(".//m:mergeCell", _NS) or root.findall(
        ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}mergeCell"
    )
    if not merges:
        return
    # Expand grid if merge refs go past current bounds
    for mc in merges:
        ref = mc.get("ref") or ""
        if ":" not in ref:
            continue
        start_s, end_s = ref.split(":", 1)
        start = _parse_a1_cell(start_s)
        end = _parse_a1_cell(end_s)
        if not start or not end:
            continue
        r0, c0 = start
        r1, c1 = end
        if r1 < r0:
            r0, r1 = r1, r0
        if c1 < c0:
            c0, c1 = c1, c0
        while len(cell_grid) <= r1:
            width = max((len(r) for r in cell_grid), default=c1 + 1)
            cell_grid.append([TableCell() for _ in range(width)])
            rows.append([""] * width)
        for r in range(len(cell_grid)):
            while len(cell_grid[r]) <= c1:
                cell_grid[r].append(TableCell())
            while len(rows[r]) <= c1:
                rows[r].append("")

        row_span = r1 - r0 + 1
        col_span = c1 - c0 + 1
        origin = cell_grid[r0][c0]
        origin.row_span = max(1, row_span)
        origin.col_span = max(1, col_span)
        origin.covered = False
        origin.origin_row = 0
        origin.origin_col = 0
        for rr in range(r0, r1 + 1):
            for cc in range(c0, c1 + 1):
                if rr == r0 and cc == c0:
                    continue
                cell_grid[rr][cc].covered = True
                cell_grid[rr][cc].row_span = 1
                cell_grid[rr][cc].col_span = 1
                cell_grid[rr][cc].origin_row = r0
                cell_grid[rr][cc].origin_col = c0
                # Prefer empty text on covered cells (Excel stores value on anchor)
                if not (cell_grid[rr][cc].text or "").strip():
                    rows[rr][cc] = ""


def _pivot_table_names(*, zf_names: list[str]) -> list[str]:
    out: list[str] = []
    for path in zf_names:
        if not path.startswith("xl/pivotTables/") or not path.endswith(".xml"):
            continue
        out.append(Path(path).stem)
    return sorted(out)[:20]


def _parse_pivot_tables(
    zf: zipfile.ZipFile, budget: ZipInflateBudget | None = None
) -> list[PivotTable]:
    """Parse xl/pivotTables/*.xml into PivotTable AST nodes."""
    cache_sources = _pivot_cache_sources(zf, budget)
    out: list[PivotTable] = []
    for path in sorted(zf.namelist()):
        if not path.startswith("xl/pivotTables/") or not path.endswith(".xml"):
            continue
        try:
            raw = budget.read(zf, path) if budget else zf.read(path)
            root = xml_safe.fromstring(raw)
        except (ET.ParseError, KeyError, Exception):
            continue
        name = root.get("name") or Path(path).stem
        cache_id = root.get("cacheId") or ""
        location = ""
        loc_el = root.find("m:location", _NS)
        if loc_el is None:
            loc_el = root.find(
                "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}location"
            )
        if loc_el is not None:
            location = loc_el.get("ref") or ""
        row_fields: list[str] = []
        col_fields: list[str] = []
        data_fields: list[str] = []
        for pf in root.iter():
            if not pf.tag.endswith("}pivotField"):
                continue
            fname = pf.get("name") or ""
            axis = pf.get("axis") or ""
            if not fname:
                continue
            if axis == "axisRow":
                row_fields.append(fname)
            elif axis == "axisCol":
                col_fields.append(fname)
            elif axis == "axisValues":
                data_fields.append(fname)
        source_range = cache_sources.get(str(cache_id), "")
        out.append(
            PivotTable(
                name=name,
                cache_id=str(cache_id),
                source_range=source_range,
                location=location,
                row_fields=row_fields,
                col_fields=col_fields,
                data_fields=data_fields,
            )
        )
    return out[:20]


def _pivot_cache_sources(
    zf: zipfile.ZipFile, budget: ZipInflateBudget | None = None
) -> dict[str, str]:
    """Map pivot cacheId → worksheet source ref from pivotCacheDefinition parts."""
    out: dict[str, str] = {}
    for path in zf.namelist():
        if not path.startswith("xl/pivotCache/pivotCacheDefinition") or not path.endswith(".xml"):
            continue
        try:
            raw = budget.read(zf, path) if budget else zf.read(path)
            root = xml_safe.fromstring(raw)
        except (ET.ParseError, KeyError, Exception):
            continue
        m = re.search(r"pivotCacheDefinition(\d+)", path)
        if not m:
            continue
        cache_key = m.group(1)
        for el in root.iter():
            if el.tag.endswith("}worksheetSource"):
                ref = el.get("ref") or ""
                if ref:
                    out[cache_key] = ref
                    break
    return out


def _load_xlsx_style_maps(
    zf: zipfile.ZipFile, budget: ZipInflateBudget | None = None
) -> tuple[dict[int, str], list[int]]:
    """numFmtId→formatCode and cellXfs index→numFmtId (P1-8 import semantics)."""
    num_fmts: dict[int, str] = dict(_BUILTIN_NUM_FMTS)
    xf_num_fmts: list[int] = [0]
    if "xl/styles.xml" not in zf.namelist():
        return num_fmts, xf_num_fmts
    try:
        raw = budget.read(zf, "xl/styles.xml") if budget else zf.read("xl/styles.xml")
        root = xml_safe.fromstring(raw)
    except (ET.ParseError, KeyError, Exception):
        return num_fmts, xf_num_fmts
    for nf in root.findall(".//m:numFmt", _NS):
        try:
            num_fmts[int(nf.get("numFmtId") or 0)] = nf.get("formatCode") or ""
        except ValueError:
            continue
    xfs = root.find("m:cellXfs", _NS)
    if xfs is not None:
        xf_num_fmts = []
        for xf in xfs.findall("m:xf", _NS):
            try:
                xf_num_fmts.append(int(xf.get("numFmtId") or 0))
            except ValueError:
                xf_num_fmts.append(0)
    return num_fmts, xf_num_fmts


_BUILTIN_NUM_FMTS: dict[int, str] = {
    14: "mm-dd-yy",
    15: "d-mmm-yy",
    16: "d-mmm",
    17: "mmm-yy",
    18: "h:mm AM/PM",
    19: "h:mm:ss AM/PM",
    20: "h:mm",
    21: "h:mm:ss",
    22: "m/d/yy h:mm",
}

_DATE_NUM_FMT_IDS = frozenset({14, 15, 16, 17, 18, 19, 20, 21, 22, 27, 28, 45, 46, 47})

# OOXML error cell ``t="e"`` stores a numeric error index in ``<v>``.
_EXCEL_ERROR_BY_INDEX: dict[str, str] = {
    "0": "#NULL!",
    "1": "#DIV/0!",
    "2": "#VALUE!",
    "3": "#REF!",
    "4": "#NAME?",
    "5": "#NUM!",
    "6": "#N/A",
    "7": "#GETTING_DATA",
}


def _is_date_num_fmt(num_fmt_id: int, fmt: str) -> bool:
    if num_fmt_id in _DATE_NUM_FMT_IDS:
        return True
    low = (fmt or "").lower()
    return any(ch in low for ch in "ymdhs") and "general" not in low


def _excel_serial_to_iso(serial: str) -> str:
    from datetime import datetime, timedelta

    try:
        days = float(serial)
        base = datetime(1899, 12, 30)
        dt = base + timedelta(days=days)
        if dt.hour or dt.minute or dt.second:
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return serial


def _format_num_by_excel_fmt(raw: str, fmt: str) -> str | None:
    """Best-effort render for common custom/builtin numFmt patterns."""
    if not fmt or fmt.lower() == "general":
        return None
    try:
        val = float(raw)
    except ValueError:
        return None
    low = fmt.lower()
    if "%" in fmt and "$" not in fmt and "[$" not in fmt:
        pct = val * 100.0
        dec = low.count("0") + low.count("#")
        places = max(0, dec - 1) if dec else 0
        return f"{pct:.{min(places, 6)}f}%"
    if "$" in fmt or "[$" in fmt:
        sym = "$"
        m = re.search(r"\[\$([^\]-]+)", fmt)
        if m:
            sym = m.group(1)
        dec = len(re.findall(r"\.0+", fmt))
        return f"{sym}{val:,.{dec}f}" if dec else f"{sym}{val:,.0f}"
    if _is_date_num_fmt(0, fmt):
        return _excel_serial_to_iso(raw)
    return None


def _cell_value(
    cell: ET.Element,
    shared: list[str],
    *,
    num_fmts: dict[int, str] | None = None,
    xf_num_fmts: list[int] | None = None,
) -> str:
    t = cell.get("t")
    if t == "inlineStr":
        texts = [el.text or "" for el in cell.findall(".//m:t", _NS)]
        return "".join(texts)
    if t == "b":
        v = cell.find("m:v", _NS)
        if v is not None and (v.text or "").strip() == "1":
            return "TRUE"
        return "FALSE"
    if t == "e":
        v = cell.find("m:v", _NS)
        code = (v.text or "").strip() if v is not None else ""
        if code in _EXCEL_ERROR_BY_INDEX:
            return _EXCEL_ERROR_BY_INDEX[code]
        if code.startswith("#"):
            return code
        return code
    v = cell.find("m:v", _NS)
    if v is None or v.text is None:
        return ""
    raw = v.text
    if t == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return raw
    if num_fmts and xf_num_fmts:
        s_attr = cell.get("s")
        if s_attr is not None and s_attr.isdigit():
            try:
                nf_id = xf_num_fmts[int(s_attr)]
                fmt = num_fmts.get(nf_id, "") or _BUILTIN_NUM_FMTS.get(nf_id, "")
                if _is_date_num_fmt(nf_id, fmt):
                    return _excel_serial_to_iso(raw)
                rendered = _format_num_by_excel_fmt(raw, fmt)
                if rendered is not None:
                    return rendered
            except (IndexError, ValueError):
                pass
    return raw


def _parse_charts(
    zf: zipfile.ZipFile, budget: ZipInflateBudget | None = None
) -> list[Chart]:
    """Best-effort Chart AST from xl/charts/chart*.xml (literal caches + title)."""
    out: list[Chart] = []
    paths = sorted(
        p
        for p in zf.namelist()
        if p.startswith("xl/charts/chart") and p.endswith(".xml")
    )
    for path in paths[:20]:
        try:
            raw_b = budget.read(zf, path) if budget else zf.read(path)
            raw = raw_b.decode("utf-8", "ignore")
        except Exception:
            continue
        title = ""
        tm = re.search(r"<a:t>([^<]*)</a:t>", raw)
        if tm:
            title = tm.group(1).strip()
        ctype = "bar"
        if "<c:lineChart" in raw:
            ctype = "line"
        elif "<c:pieChart" in raw or "<c:doughnutChart" in raw:
            ctype = "pie"
        elif "<c:areaChart" in raw:
            ctype = "area"
        elif "<c:scatterChart" in raw:
            ctype = "scatter"
        elif "<c:barChart" in raw:
            ctype = "bar"
        cats = re.findall(r"<c:strLit>.*?</c:strLit>", raw, flags=re.DOTALL)
        categories: list[str] = []
        if cats:
            categories = re.findall(r"<c:v>([^<]*)</c:v>", cats[0])
        series: list[dict] = []
        for ser in re.findall(r"<c:ser>.*?</c:ser>", raw, flags=re.DOTALL)[:12]:
            nm = re.search(r"<c:tx>.*?<c:v>([^<]*)</c:v>", ser, flags=re.DOTALL)
            name = nm.group(1) if nm else f"S{len(series) + 1}"
            num = re.search(r"<c:numLit>(.*?)</c:numLit>", ser, flags=re.DOTALL)
            values: list[float] = []
            if num:
                for v in re.findall(r"<c:v>([^<]*)</c:v>", num.group(1)):
                    try:
                        values.append(float(v))
                    except ValueError:
                        values.append(0.0)
            series.append({"name": name, "values": values})
        x_axis, y_axis = "", ""
        cat_ax = re.search(r"<c:catAx\b[^>]*>(.*?)</c:catAx>", raw, flags=re.DOTALL)
        if cat_ax:
            xm = re.search(
                r"<c:title\b[^>]*>.*?<a:t>([^<]*)</a:t>",
                cat_ax.group(1),
                flags=re.DOTALL,
            )
            if xm:
                x_axis = xm.group(1).strip()
        val_ax = re.search(r"<c:valAx\b[^>]*>(.*?)</c:valAx>", raw, flags=re.DOTALL)
        if val_ax:
            ym = re.search(
                r"<c:title\b[^>]*>.*?<a:t>([^<]*)</a:t>",
                val_ax.group(1),
                flags=re.DOTALL,
            )
            if ym:
                y_axis = ym.group(1).strip()
        out.append(
            Chart(
                title=title or Path(path).stem,
                chart_type=ctype,
                categories=categories,
                series=series,
                x_axis_title=x_axis,
                y_axis_title=y_axis,
            )
        )
    return out
