"""OpenDocument Spreadsheet (ODS) — content.xml tables without external deps."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core import xml_safe
from app.core.document_model import DocumentModel, Heading, Paragraph, RichText, Table
from app.core.exceptions import CorruptedFileError, FileTooLargeError, InvalidDocumentError
from app.core.safe_zip import ZipInflateBudget

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def try_extract_ods_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return None
    if "content.xml" not in zf.namelist():
        zf.close()
        return None
    try:
        budget = ZipInflateBudget()
        root = xml_safe.fromstring(budget.read(zf, "content.xml"))
    except (ET.ParseError, FileTooLargeError, KeyError):
        zf.close()
        return None
    zf.close()

    nodes: list = []
    sheets = root.findall(".//table:table", _NS)
    if not sheets:
        sheets = root.findall(
            ".//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table"
        )
    for idx, table_el in enumerate(sheets[:20]):
        name = (
            table_el.get(f"{{{_NS['table']}}}name")
            or table_el.get("table:name")
            or f"Sheet{idx + 1}"
        )
        rows: list[list[str]] = []
        for row_el in table_el.findall("table:table-row", _NS) or table_el.findall(
            "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row"
        ):
            cells: list[str] = []
            for cell in list(row_el):
                tag = cell.tag.rsplit("}", 1)[-1]
                if tag not in ("table-cell", "covered-table-cell"):
                    continue
                repeat = int(
                    cell.get(f"{{{_NS['table']}}}number-columns-repeated")
                    or cell.get("table:number-columns-repeated")
                    or "1"
                )
                text = "".join(cell.itertext()).strip()
                for _ in range(min(repeat, 64)):
                    cells.append(text)
            # trim trailing empty
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                rows.append(cells)
            row_rep = int(
                row_el.get(f"{{{_NS['table']}}}number-rows-repeated")
                or row_el.get("table:number-rows-repeated")
                or "1"
            )
            if row_rep > 1 and cells:
                for _ in range(min(row_rep - 1, 32)):
                    rows.append(list(cells))
        if not rows:
            continue
        nodes.append(Heading(level=2, text=RichText(text=str(name))))
        nodes.append(Table(rows=rows, header_rows=1, name=str(name)))

    if not nodes:
        return None
    return DocumentModel(
        title=title or "ods",
        nodes=nodes,
        metadata={"importer": "ods_native"},
    )


def import_ods(source_bytes: bytes, filename: str = "input.ods") -> DocumentModel:
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            if "content.xml" not in zf.namelist():
                raise InvalidDocumentError(f"文件不是有效的 ODS: {filename}")
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(f"ODS 文件已损坏: {filename}") from exc
    model = try_extract_ods_model(source_bytes, title=filename.rsplit(".", 1)[0])
    if model is None:
        raise CorruptedFileError(f"ODS 解析失败（无表格）: {filename}")
    return model
