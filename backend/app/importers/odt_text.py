"""Lightweight ODT (OpenDocument Text) extract — content.xml → DocumentModel."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core.document_model import DocumentModel, Heading, Paragraph, Table

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
                nodes.append(Paragraph(text=text))
        elif tag == "table":
            rows = _table_rows(el)
            if rows:
                nodes.append(Table(rows=rows))
        elif tag == "list":
            for item in el.findall(f".//{{{_NS['text']}}}list-item"):
                text = _elem_text(item).strip()
                if text:
                    nodes.append(Paragraph(text=f"• {text}"))

    if not nodes:
        return None
    return DocumentModel(title=title or "odt", nodes=nodes)


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
    rows: list[list[str]] = []
    for row in table_el.findall(f".//{{{_NS['table']}}}table-row"):
        cells: list[str] = []
        for cell in row:
            ctag = cell.tag.rsplit("}", 1)[-1]
            if ctag not in ("table-cell", "covered-table-cell"):
                continue
            cells.append(_elem_text(cell).strip())
        if cells:
            rows.append(cells)
    return rows
