"""OpenDocument Presentation (ODP) — draw:page text + notes + embedded images."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core import xml_safe
from app.core.document_model import DocumentModel, Heading, Image, Paragraph, RichText, SpeakerNote
from app.core.exceptions import CorruptedFileError, FileTooLargeError, InvalidDocumentError
from app.core.safe_zip import ZipInflateBudget

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
}


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

        paras: list[str] = []
        for p in page.findall(".//text:p", _NS) or page.findall(
            ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p"
        ):
            if id(p) in note_els:
                continue
            t = "".join(p.itertext()).strip()
            if t:
                paras.append(t)

        images = _page_images(page, picture_blobs)

        if not paras and not note_paras and not images:
            continue
        nodes.append(Heading(level=2, text=RichText(text=str(name))))
        for t in paras:
            nodes.append(Paragraph(text=RichText(text=t)))
        nodes.extend(images)
        if note_paras:
            nodes.append(SpeakerNote(text="\n".join(note_paras), slide_index=i + 1))

    if not nodes:
        return None
    return DocumentModel(
        title=title or "odp",
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
        except Exception:
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
