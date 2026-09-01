"""Minimal XLSX (OOXML) table import/export without openpyxl."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from app.core.document_model import DocumentModel, Paragraph, Table
from app.core.exceptions import CorruptedFileError, InvalidDocumentError

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def export_xlsx(model: DocumentModel) -> bytes:
    tables = [n for n in model.nodes if isinstance(n, Table)]
    if not tables:
        # Flatten text as single column
        rows = []
        for node in model.nodes:
            text = getattr(node, "plain_text", None)
            if callable(text):
                rows.append([text()])
            elif isinstance(node, Paragraph):
                rows.append([node.plain_text()])
        tables = [Table(rows=rows or [[""]])]

    # First table only for simplicity (extra sheets for more)
    sheet_xmls: list[tuple[str, str]] = []
    for i, table in enumerate(tables[:8], start=1):
        sheet_xmls.append((f"xl/worksheets/sheet{i}.xml", _sheet_xml(table.rows)))

    content_types = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    for i in range(1, len(sheet_xmls) + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    content_types.append("</Types>")

    workbook_sheets = "".join(
        f'<sheet name="Sheet{i}" sheetId="{i}" r:id="rId{i}"/>'
        for i in range(1, len(sheet_xmls) + 1)
    )
    workbook = f"""<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets>{workbook_sheets}</sheets>
</workbook>
"""
    rels_inner = "".join(
        f'<Relationship Id="rId{i}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, len(sheet_xmls) + 1)
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
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "\n".join(content_types))
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        for path, xml in sheet_xmls:
            zf.writestr(path, xml)
    return buf.getvalue()


def import_xlsx(source_bytes: bytes, filename: str = "input.xlsx") -> DocumentModel:
    try:
        zf = zipfile.ZipFile(BytesIO(source_bytes))
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(f"XLSX 损坏: {filename}") from exc

    names = zf.namelist()
    if "xl/workbook.xml" not in names:
        zf.close()
        raise InvalidDocumentError(f"不是有效的 XLSX: {filename}")

    shared = _load_shared_strings(zf)
    sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
    nodes: list = []
    for sheet_path in sheets[:8]:
        try:
            root = ET.fromstring(zf.read(sheet_path))
        except ET.ParseError:
            continue
        rows = _parse_sheet(root, shared)
        if rows:
            nodes.append(Table(rows=rows))
    zf.close()
    if not nodes:
        raise CorruptedFileError(f"XLSX 无工作表数据: {filename}")
    return DocumentModel(title=Path(filename).stem, nodes=nodes)


def _sheet_xml(rows: list[list[str]]) -> str:
    row_xml: list[str] = []
    for r_idx, row in enumerate(rows, start=1):
        cells: list[str] = []
        for c_idx, val in enumerate(row):
            ref = f"{_col_name(c_idx)}{r_idx}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(val))}</t></is></c>'
            )
        row_xml.append(f'<row r="{r_idx}">{"".join(cells)}</row>')
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


def _parse_sheet(root: ET.Element, shared: list[str]) -> list[list[str]]:
    rows_map: dict[int, dict[int, str]] = {}
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
            rows_map.setdefault(r_idx, {})[col] = val
    if not rows_map:
        return []
    max_col = max((max(cols) for cols in rows_map.values() if cols), default=0)
    result: list[list[str]] = []
    for r in sorted(rows_map):
        result.append([rows_map[r].get(c, "") for c in range(max_col + 1)])
    return result


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
