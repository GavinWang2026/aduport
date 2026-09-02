"""Lightweight ODT (OpenDocument Text) extract — content.xml → DocumentModel."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core.document_model import (
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    Paragraph,
    PivotTable,
    Revision,
    Table,
    TableCell,
    TextBox,
)

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
}


def try_extract_odt_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return None
    names = set(zf.namelist())
    if "content.xml" not in names:
        zf.close()
        return None
    try:
        root = ET.fromstring(zf.read("content.xml"))
    except ET.ParseError:
        zf.close()
        return None
    zf.close()

    body = root.find(".//office:body/office:text", _NS)
    if body is None:
        body = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}text")
    if body is None:
        return None

    nodes: list = []
    for el in list(body):
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "h":
            level = 1
            lvl = el.get(f"{{{_NS['text']}}}outline-level") or el.get("text:outline-level")
            if lvl and str(lvl).isdigit():
                level = max(1, min(6, int(lvl)))
            text = _elem_text(el).strip()
            if text:
                nodes.append(Heading(level=level, text=text))
        elif tag == "p":
            text = _elem_text(el).strip()
            if text:
                meta = _parse_meta_paragraph(text)
                nodes.append(meta if meta is not None else Paragraph(text=text))
        elif tag == "table":
            table = _parse_table(el)
            if table:
                nodes.append(table)
        elif tag == "list":
            for item in el.findall(f".//{{{_NS['text']}}}list-item"):
                text = _elem_text(item).strip()
                if text:
                    nodes.append(Paragraph(text=f"• {text}"))

    if not nodes:
        return None
    return DocumentModel(title=title or "odt", nodes=nodes)


def _parse_meta_paragraph(text: str):
    """Map ODT exporter bracket lines back to AST meta nodes."""
    if m := re.match(r"^\[Comment\s+(.+?)\]\s*(.*)$", text, re.DOTALL):
        head, body = m.group(1).strip(), m.group(2).strip()
        anchor = ""
        author = head
        if am := re.match(r"^(.+?)\s+\((.+)\)$", head):
            author, anchor = am.group(1).strip(), am.group(2).strip()
        return Comment(author=author or "reviewer", anchor=anchor, text=body)
    if m := re.match(r"^\[\^(?P<id>[^\]]+)\]\s*(?P<body>.*)$", text, re.DOTALL):
        return Footnote(id=m.group("id"), text=m.group("body").strip())
    if m := re.match(r"^\[(?P<tag>ins|del)\s+(?P<author>[^\]]*)\]\s*(?P<body>.*)$", text, re.DOTALL):
        change = "insert" if m.group("tag") == "ins" else "delete"
        return Revision(change=change, author=m.group("author").strip(), text=m.group("body").strip())
    if m := re.match(r"^\[Control:(?P<label>[^\]|]+)(?:\|(?P<ctype>[^\]]+))?\]\s*(?P<body>.*)$", text, re.DOTALL):
        label = m.group("label").strip()
        return ContentControl(
            tag=label.lower().replace(" ", "_"),
            alias=label,
            control_type=(m.group("ctype") or "richText").strip(),
            text=m.group("body").strip(),
        )
    if m := re.match(r"^\[TextBox(?:\s+\((?P<source>[^)]+)\))?\]\s*(?P<body>.*)$", text, re.DOTALL):
        return TextBox(text=m.group("body").strip(), source=(m.group("source") or "").strip())
    if m := re.match(r"^\[PivotTable\s+(?P<name>[^\]]+)\]\s*(?P<fields>.*)$", text, re.DOTALL):
        fields = [f.strip() for f in m.group("fields").split(",") if f.strip()]
        return PivotTable(name=m.group("name").strip(), row_fields=fields)
    return None


def _attr(el: ET.Element, ns_key: str, local: str) -> str:
    return el.get(f"{{{_NS[ns_key]}}}{local}") or el.get(f"{ns_key}:{local}") or ""


def _int_attr(el: ET.Element, ns_key: str, local: str, default: int = 1) -> int:
    raw = _attr(el, ns_key, local)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _parse_table(table_el: ET.Element) -> Table | None:
    name = _attr(table_el, "table", "name")
    row_els = table_el.findall(f".//{{{_NS['table']}}}table-row")
    if not row_els:
        return None

    cells_grid: list[list[TableCell]] = []
    rows: list[list[str]] = []

    for row_el in row_els:
        row_cells: list[TableCell] = []
        row_strs: list[str] = []
        for cell_el in row_el:
            tag = cell_el.tag.rsplit("}", 1)[-1]
            if tag == "covered-table-cell":
                row_cells.append(TableCell(covered=True))
                row_strs.append("")
            elif tag == "table-cell":
                text = _elem_text(cell_el).strip()
                col_span = _int_attr(cell_el, "table", "number-columns-spanned")
                row_span = _int_attr(cell_el, "table", "number-rows-spanned")
                row_cells.append(
                    TableCell(text=text, col_span=col_span, row_span=row_span)
                )
                row_strs.append(text)
        if row_cells:
            cells_grid.append(row_cells)
            rows.append(row_strs)

    if not rows:
        return None

    _resolve_covered_origins(cells_grid)
    for r_idx, row in enumerate(cells_grid):
        for c_idx, cell in enumerate(row):
            if not cell.covered:
                cell.origin_row = r_idx
                cell.origin_col = c_idx

    has_span = any(
        cell.covered or cell.col_span > 1 or cell.row_span > 1
        for row in cells_grid
        for cell in row
    )
    if has_span:
        return Table(rows=rows, cells=cells_grid, name=name)
    return Table(rows=rows, name=name)


def _find_span_origin(cells: list[list[TableCell]], r: int, c: int) -> tuple[int, int]:
    for cc in range(c - 1, -1, -1):
        if cc >= len(cells[r]):
            continue
        origin = cells[r][cc]
        if not origin.covered and origin.col_span > (c - cc):
            return (r, cc)
    for rr in range(r - 1, -1, -1):
        if c >= len(cells[rr]):
            continue
        origin = cells[rr][c]
        if not origin.covered and origin.row_span > (r - rr):
            return (rr, c)
    return (r, c)


def _resolve_covered_origins(cells: list[list[TableCell]]) -> None:
    for r_idx, row in enumerate(cells):
        for c_idx, cell in enumerate(row):
            if not cell.covered:
                continue
            origin_r, origin_c = _find_span_origin(cells, r_idx, c_idx)
            cell.origin_row = origin_r
            cell.origin_col = origin_c


def _elem_text(el: ET.Element) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        ctag = child.tag.rsplit("}", 1)[-1]
        if ctag == "line-break":
            parts.append("\n")
        elif ctag == "s":
            n = child.get(f"{{{_NS['text']}}}c") or child.get("text:c") or "1"
            parts.append(" " * max(1, int(n) if str(n).isdigit() else 1))
        elif ctag == "tab":
            parts.append("\t")
        else:
            parts.append(_elem_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _table_rows(table_el: ET.Element) -> list[list[str]]:
    """Legacy flat rows helper (span metadata discarded)."""
    table = _parse_table(table_el)
    return table.rows if table else []
