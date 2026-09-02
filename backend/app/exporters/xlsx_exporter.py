"""Minimal XLSX (OOXML) table import/export without openpyxl."""

from __future__ import annotations

import base64
import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
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
from app.core.exceptions import CorruptedFileError, InvalidDocumentError

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}
_M = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_META_AST = "_AST"
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
    tables = [n for n in model.nodes if isinstance(n, Table)]
    pivots = [n for n in model.nodes if isinstance(n, PivotTable)]
    meta_sheets = _meta_ast_sheets(model)
    max_data_sheets = max(1, 8 - len(meta_sheets))
    if not tables:
        rows = []
        for node in model.nodes:
            text = getattr(node, "plain_text", None)
            if callable(text):
                rows.append([text()])
            elif isinstance(node, Paragraph):
                rows.append([node.plain_text()])
        tables = [Table(rows=rows or [[""]])]

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
        sheet_xmls.append(
            (f"xl/worksheets/sheet{i}.xml", _sheet_xml(table), name)
        )

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
    span_cell_rows = _table_cell_ast_rows(model)
    if not (
        revs or ctrls or boxes or pivots or comments or footnotes or maths
        or images or html_blocks or codes or blockquotes or headings
        or paragraphs or list_items or rules or span_cell_rows
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
        matrix.append(["paragraph", para.plain_text(), "", "", "", "", ""])
    for li in list_items[:500]:
        checked = ""
        if li.checked is not None:
            checked = "true" if li.checked else "false"
        children = "\n".join(c.plain_text() for c in li.children)
        matrix.append(
            [
                "list_item",
                li.plain_text(),
                "true" if li.ordered else "false",
                str(li.level),
                checked,
                children,
                "",
            ]
        )
    for _hr in rules[:500]:
        matrix.append(["horizontal_rule", "", "", "", "", "", ""])
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

    sheet_names = _workbook_sheet_names(zf)
    shared = _load_shared_strings(zf)
    sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
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
    for idx, sheet_path in enumerate(sheets[:8]):
        try:
            root = ET.fromstring(zf.read(sheet_path))
        except ET.ParseError:
            continue
        rows, cells, has_formula = _parse_sheet(root, shared)
        if not rows:
            continue
        sname = sheet_names[idx] if idx < len(sheet_names) else f"Sheet{idx + 1}"
        if sname == _META_AST or _rows_match_header(rows[0], _AST_HEADER):
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
        tbl = Table(
            rows=rows,
            cells=cells if has_formula else [],
            name=sname,
            header_rows=1 if len(rows) > 1 else 0,
        )
        nodes.append(tbl)
    existing_pivot_names = {n.name for n in nodes if isinstance(n, PivotTable) and n.name}
    for p in _parse_pivot_tables(zf):
        if p.name and p.name in existing_pivot_names:
            continue
        nodes.append(p)
        if p.name:
            existing_pivot_names.add(p.name)
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
    return DocumentModel(
        title=Path(filename).stem,
        nodes=nodes,
        metadata={
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
        },
    )


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


def _workbook_sheet_names(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/workbook.xml"))
    except ET.ParseError:
        return []
    out: list[str] = []
    for sheet in root.findall("m:sheets/m:sheet", _NS):
        name = sheet.get("name") or ""
        if name:
            out.append(name)
    return out


def _table_value_matrix(table: Table) -> list[list[str]]:
    """Prefer rows; fall back to cells.text (anydoc span grid). Values only."""
    if table.rows:
        return [[str(c) for c in row] for row in table.rows]
    if table.cells:
        return [[(c.text if c is not None else "") for c in row] for row in table.cells]
    return [[""]]


def _sheet_xml(table: Table) -> str:
    matrix = _table_value_matrix(table)
    header_rows = max(0, int(table.header_rows or 0))
    cell_grid = table.cells if table.cells else None
    row_xml: list[str] = []
    for r_idx, row in enumerate(matrix, start=1):
        cells_xml: list[str] = []
        style = ' s="1"' if r_idx <= header_rows else ""
        for c_idx, val in enumerate(row):
            ref = f"{_col_name(c_idx)}{r_idx}"
            formula = ""
            if cell_grid and r_idx - 1 < len(cell_grid) and c_idx < len(cell_grid[r_idx - 1]):
                tc = cell_grid[r_idx - 1][c_idx]
                formula = (tc.formula or "").strip()
            if formula:
                cells_xml.append(
                    f'<c r="{ref}"{style}>'
                    f"<f>{escape(formula)}</f>"
                    f"<v>{escape(str(val))}</v></c>"
                )
            else:
                cells_xml.append(
                    f'<c r="{ref}"{style} t="inlineStr"><is><t>{escape(str(val))}</t></is></c>'
                )
        row_xml.append(f'<row r="{r_idx}">{"".join(cells_xml)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )


def _col_name(idx: int) -> str:
    n = idx + 1
    letters = []
    while n:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def _load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except ET.ParseError:
        return []
    out: list[str] = []
    for si in root.findall("m:si", _NS):
        texts = [t.text or "" for t in si.findall(".//m:t", _NS)]
        out.append("".join(texts))
    return out


def _parse_sheet(
    root: ET.Element, shared: list[str]
) -> tuple[list[list[str]], list[list[TableCell]], bool]:
    rows_map: dict[int, dict[int, tuple[str, str]]] = {}
    for row in root.findall(".//m:row", _NS):
        r_attr = row.get("r")
        r_idx = int(r_attr) if r_attr and r_attr.isdigit() else (max(rows_map) + 1 if rows_map else 1)
        for c in row.findall("m:c", _NS):
            ref = c.get("r") or "A1"
            col = 0
            m = re.match(r"([A-Z]+)", ref)
            if m:
                col = 0
                for ch in m.group(1):
                    col = col * 26 + (ord(ch) - 64)
                col -= 1
            val = _cell_value(c, shared)
            formula_el = c.find("m:f", _NS)
            formula = (formula_el.text or "").strip() if formula_el is not None else ""
            rows_map.setdefault(r_idx, {})[col] = (val, formula)
    if not rows_map:
        return [], [], False
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
    return result, cell_grid, has_formula


def _pivot_table_names(*, zf_names: list[str]) -> list[str]:
    out: list[str] = []
    for path in zf_names:
        if not path.startswith("xl/pivotTables/") or not path.endswith(".xml"):
            continue
        out.append(Path(path).stem)
    return sorted(out)[:20]


def _parse_pivot_tables(zf: zipfile.ZipFile) -> list[PivotTable]:
    """Parse xl/pivotTables/*.xml into PivotTable AST nodes."""
    cache_sources = _pivot_cache_sources(zf)
    out: list[PivotTable] = []
    for path in sorted(zf.namelist()):
        if not path.startswith("xl/pivotTables/") or not path.endswith(".xml"):
            continue
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError:
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


def _pivot_cache_sources(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map pivot cacheId → worksheet source ref from pivotCacheDefinition parts."""
    out: dict[str, str] = {}
    for path in zf.namelist():
        if not path.startswith("xl/pivotCache/pivotCacheDefinition") or not path.endswith(".xml"):
            continue
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError:
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


def _cell_value(cell: ET.Element, shared: list[str]) -> str:
    t = cell.get("t")
    if t == "inlineStr":
        texts = [el.text or "" for el in cell.findall(".//m:t", _NS)]
        return "".join(texts)
    v = cell.find("m:v", _NS)
    if v is None or v.text is None:
        return ""
    if t == "s":
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError):
            return v.text
    return v.text
