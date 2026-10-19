"""OpenDocument Spreadsheet (ODS) — content.xml tables without external deps."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from aduport.core import xml_safe
from aduport.core.document_model import DocumentModel, Heading, RichText, Table
from aduport.core.exceptions import CorruptedFileError, FileTooLargeError, InvalidDocumentError
from aduport.core.safe_zip import ZipInflateBudget

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def _markdown_sheet(name: str, rows: list[list[str]]) -> str | None:
    """Content sheets store one Markdown line per cell. Data grids stay tables."""
    from aduport.importers.markdown_rehydrate import looks_structural

    if not rows or any(len(row) > 1 for row in rows):
        return None
    blob = "\n".join(row[0] if row else "" for row in rows)
    if name in ("Content", "ContentExtra") or looks_structural(blob):
        return blob
    return None


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
    prose: list = []
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
        prose_text = _markdown_sheet(name, rows)
        if prose_text is not None:
            from aduport.importers.markdown_rehydrate import parse_markdown_nodes

            prose.extend(parse_markdown_nodes(prose_text))
            continue
        # "Sheet1" is the exporter's grid name, not a document heading.
        if not re.fullmatch(r"Sheet\d+", str(name)):
            nodes.append(Heading(level=2, text=RichText(text=str(name))))
        nodes.append(Table(rows=rows, header_rows=1, name=str(name)))

    if prose and isinstance(prose[0], Heading):
        nodes = prose + nodes
    else:
        nodes = nodes + prose

    if not nodes:
        return None
    from aduport.core.title_source import resolve_title

    return DocumentModel(
        title=resolve_title(
            "" if (title or "").casefold() in {"ods", ""} else title,
            nodes,
            filename=f"{title or 'input'}.ods",
        ),
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
    from aduport.importers.zip_images import append_zip_images

    append_zip_images(model, source_bytes, ("Pictures/",))
    return model
