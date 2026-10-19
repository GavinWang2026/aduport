"""OpenDocument Presentation (ODP) — draw:page text + notes + embedded images."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from aduport.core import xml_safe
from aduport.core.document_model import (
    DocumentModel,
    Heading,
    Image,
    Paragraph,
    RichText,
    SpeakerNote,
    Table,
)
from aduport.core.exceptions import CorruptedFileError, FileTooLargeError, InvalidDocumentError
from aduport.core.safe_zip import ZipInflateBudget
from aduport.importers.import_guard import swallow_import_error

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _odf_table_rows(table: ET.Element) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in list(table):
        if _local(row.tag) != "table-row":
            continue
        cells: list[str] = []
        for cell in list(row):
            kind = _local(cell.tag)
            if kind == "covered-table-cell":
                continue
            if kind != "table-cell":
                continue
            text = "".join(cell.itertext()).strip()
            try:
                repeat = int(
                    cell.get(f"{{{_NS['table']}}}number-columns-repeated")
                    or cell.get("table:number-columns-repeated")
                    or "1"
                )
            except ValueError:
                repeat = 1
            repeat = max(1, min(repeat, 32))
            cells.extend([text] * repeat)
        if any(cell.strip() for cell in cells):
            rows.append(cells)
    return rows


def _page_body_items(
    page: ET.Element,
    note_els: set[int],
) -> list[tuple[str, object]]:
    """Visible text and native tables, in document order.

    Paragraphs inside a ``table:table`` stay in the table. Older files that
    stored a Markdown table in one ``text:p`` still come through as text.
    """
    items: list[tuple[str, object]] = []

    def walk(el: ET.Element) -> None:
        if id(el) in note_els:
            return
        kind = _local(el.tag)
        if kind == "notes":
            return
        if kind == "table":
            rows = _odf_table_rows(el)
            if rows:
                items.append(("table", rows))
            return
        if kind == "p":
            text = "".join(el.itertext()).strip()
            if text:
                items.append(("text", text))
            return
        for child in list(el):
            walk(child)

    walk(page)
    return items


def _append_body_items(nodes: list, items: list[tuple[str, object]], *, title: str) -> None:
    from aduport.importers.markdown_rehydrate import looks_structural, parse_markdown_nodes

    text_buf: list[str] = []

    def flush() -> None:
        if not text_buf:
            return
        blob = "\n\n".join(text_buf)
        text_buf.clear()
        if looks_structural(blob) or "**" in blob or "`" in blob or "*" in blob:
            nodes.extend(parse_markdown_nodes(blob))
            return
        for line in blob.split("\n\n"):
            if line.strip():
                nodes.append(Paragraph(text=RichText(text=line.strip())))

    for kind, payload in items:
        if kind == "text":
            text = str(payload).strip()
            if text and text != title.strip():
                text_buf.append(text)
            continue
        flush()
        rows = payload if isinstance(payload, list) else []
        if rows:
            nodes.append(Table(rows=rows, header_rows=1 if len(rows) >= 2 else 0))
    flush()


def try_extract_odp_model(data: bytes, *, title: str = "") -> DocumentModel | None:
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
        picture_blobs = _load_pictures(zf, budget)
    except (ET.ParseError, FileTooLargeError, KeyError):
        zf.close()
        return None
    zf.close()

    pres = root.find(".//office:presentation", _NS)
    if pres is None:
        pred = root.find(
            ".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}presentation"
        )
        if pred is not None:
            pres = pred
    if pres is not None:
        top_pages = [c for c in list(pres) if c.tag.rsplit("}", 1)[-1] == "page"]
    else:
        top_pages = root.findall(".//draw:page", _NS) or root.findall(
            ".//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}page"
        )

    nodes: list = []
    for i, page in enumerate(top_pages[:50]):
        name = (
            page.get(f"{{{_NS['draw']}}}name")
            or page.get("draw:name")
            or f"Slide {i + 1}"
        )

        note_els: set[int] = set()
        note_paras: list[str] = []
        for notes in page.findall("presentation:notes", _NS) or page.findall(
            "{urn:oasis:names:tc:opendocument:xmlns:presentation:1.0}notes"
        ):
            for p in notes.findall(".//text:p", _NS) or notes.findall(
                ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p"
            ):
                note_els.add(id(p))
                t = "".join(p.itertext()).strip()
                if t:
                    note_paras.append(t)

        items = _page_body_items(page, note_els)
        images = _page_images(page, picture_blobs)

        if not items and not note_paras and not images:
            continue
        if not re.fullmatch(r"Slide \d+", str(name)):
            level = 1 if i == 0 else 2
            nodes.append(Heading(level=level, text=RichText(text=str(name))))
        _append_body_items(nodes, items, title=str(name))
        nodes.extend(images)
        if note_paras:
            nodes.append(SpeakerNote(text="\n".join(note_paras), slide_index=i + 1))

    if not nodes:
        return None
    from aduport.core.title_source import resolve_title

    return DocumentModel(
        title=resolve_title(title if title not in ("odp",) else "", nodes, filename=title or ""),
        nodes=nodes,
        metadata={"importer": "odp_native"},
    )


def _load_pictures(zf: zipfile.ZipFile, budget: ZipInflateBudget) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for name in zf.namelist():
        if not name.startswith("Pictures/") or name.endswith("/"):
            continue
        try:
            out[name] = budget.read(zf, name)
        except Exception as exc:
            swallow_import_error(exc, where='odp_importer.py:112')
            continue
        if len(out) >= 64:
            break
    return out


def _page_images(page: ET.Element, picture_blobs: dict[str, bytes]) -> list[Image]:
    images: list[Image] = []
    for img in page.findall(".//draw:image", _NS) or page.findall(
        ".//{urn:oasis:names:tc:opendocument:xmlns:drawing:1.0}image"
    ):
        href = (
            img.get(f"{{{_NS['xlink']}}}href")
            or img.get("{http://www.w3.org/1999/xlink}href")
            or img.get("xlink:href")
            or ""
        )
        href = href.lstrip("./")
        blob = picture_blobs.get(href)
        if not blob:
            continue
        mime = "image/png"
        low = href.lower()
        if low.endswith(".jpg") or low.endswith(".jpeg"):
            mime = "image/jpeg"
        elif low.endswith(".gif"):
            mime = "image/gif"
        elif low.endswith(".svg"):
            mime = "image/svg+xml"
        alt = href.rsplit("/", 1)[-1]
        images.append(Image(data=blob, mime_type=mime, alt_text=alt))
        if len(images) >= 8:
            break
    return images


def import_odp(source_bytes: bytes, filename: str = "input.odp") -> DocumentModel:
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            if "content.xml" not in zf.namelist():
                raise InvalidDocumentError(f"文件不是有效的 ODP: {filename}")
    except zipfile.BadZipFile as exc:
        raise CorruptedFileError(f"ODP 文件已损坏: {filename}") from exc
    model = try_extract_odp_model(source_bytes, title=filename.rsplit(".", 1)[0])
    if model is None:
        raise CorruptedFileError(f"ODP 解析失败（无幻灯片文本）: {filename}")
    return model
