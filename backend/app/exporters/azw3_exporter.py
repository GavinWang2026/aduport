"""KF8 / AZW3 exporter — PalmDB MOBI with EXTH + embedded images (rich media)."""

from __future__ import annotations

import html as html_lib
import struct
import time
from io import BytesIO

from app.core.document_model import DocumentModel, Image
from app.exporters.html_render import render_body_html, render_ebook_css


def export_azw3(model: DocumentModel) -> bytes:
    """
    Build a Kindle KF8-oriented PalmDB (AZW3-compatible container).

    Embeds Image node bytes as MOBI image records and rewrites HTML to
    ``<img recindex="N"/>`` so Kindle-class readers can show figures.
    """
    title = (model.title or "Untitled")[:31]
    images: list[tuple[bytes, str]] = []
    # Clone-ish: rewrite image nodes in HTML via custom body with recindex
    body_html, images = _render_body_with_images(model)

    html = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        "<head><meta http-equiv='Content-Type' content='text/html; charset=utf-8'/>"
        f"<title>{html_lib.escape(title)}</title>"
        f"<style>{render_ebook_css()} img{{max-width:100%}}</style>"
        "</head><body>\n"
        f"{body_html}\n</body></html>\n"
    )
    payload = html.encode("utf-8")

    rec_size = 4096
    text_records = [payload[i : i + rec_size] for i in range(0, max(len(payload), 1), rec_size)]
    image_records = [data for data, _mime in images]

    first_image = 1 + len(text_records) if image_records else 0xFFFFFFFF
    first_non_book = 1 + len(text_records) + len(image_records)

    exth = _build_exth(
        title=title,
        author="md-bridge",
        has_images=bool(image_records),
    )
    record0 = _build_record0(
        title=title,
        text_length=len(payload),
        text_record_count=len(text_records),
        first_image=first_image if image_records else 0xFFFFFFFF,
        first_non_book=first_non_book,
        exth=exth,
    )
    records = [record0, *text_records, *image_records]
    return _build_palmdb(title=title, records=records, creator=b"MOBI")


def export_kf8(model: DocumentModel) -> bytes:
    """Alias of export_azw3 (KF8 markup inside MOBI/AZW3 container)."""
    return export_azw3(model)


def _render_body_with_images(model: DocumentModel) -> tuple[str, list[tuple[bytes, str]]]:
    images: list[tuple[bytes, str]] = []
    # Temporarily replace Image nodes with placeholder Paragraphs carrying recindex,
    # by building HTML ourselves for images and delegating the rest.
    from app.core.document_model import (
        Blockquote,
        CodeBlock,
        Comment,
        Footnote,
        Heading,
        HorizontalRule,
        HtmlBlock,
        ListItem,
        MathBlock,
        Paragraph,
        Table,
        ensure_rich_text,
    )
    from app.exporters.html_render import build_toc_html  # noqa: F401

    parts: list[str] = []
    if model.title:
        parts.append(f"<h1>{html_lib.escape(model.title)}</h1>")

    i = 0
    nodes = model.nodes
    while i < len(nodes):
        node = nodes[i]
        if isinstance(node, Image) and node.data:
            images.append((node.data, node.mime_type or "image/jpeg"))
            idx = len(images)  # 1-based recindex for Kindle
            alt = html_lib.escape(node.alt_text.split("|", 1)[0])
            parts.append(f'<img recindex="{idx}" alt="{alt}"/>')
            i += 1
            continue
        if isinstance(node, Image):
            alt = html_lib.escape(node.alt_text)
            parts.append(f"<p>[Image: {alt}]</p>")
            i += 1
            continue
        # Reuse shared renderer for a single-node model slice
        slice_model = DocumentModel(title="", nodes=[node])
        chunk = render_body_html(slice_model, include_title=False)
        parts.append(chunk)
        i += 1
    return "\n".join(parts), images


def _build_exth(*, title: str, author: str, has_images: bool) -> bytes:
    """EXTH header with title/author and KF8-ish tip."""
    records: list[bytes] = []

    def add(exth_type: int, data: bytes) -> None:
        records.append(struct.pack(">II", exth_type, 8 + len(data)) + data)

    add(100, author.encode("utf-8"))  # author
    add(503, title.encode("utf-8"))  # updated title
    add(501, b"md-bridge")  # cde type / app
    if has_images:
        add(204, struct.pack(">I", 0))  # Fake cover offset tip (none)
    # 108 = ASIN-like id
    add(113, b"mdbridge-local")

    body = b"".join(records)
    # EXTH + length + count + records + pad to 4
    header = b"EXTH" + struct.pack(">II", 12 + len(body), len(records)) + body
    pad = (4 - (len(header) % 4)) % 4
    return header + (b"\x00" * pad)


def _build_record0(
    *,
    title: str,
    text_length: int,
    text_record_count: int,
    first_image: int,
    first_non_book: int,
    exth: bytes,
) -> bytes:
    palmdoc = struct.pack(
        ">HHIHHHH",
        1,
        0,
        text_length & 0xFFFFFFFF,
        text_record_count & 0xFFFF,
        0,
        4096,
        0,
    )
    fullname = title.encode("utf-8")[:64]
    # Longer MOBI header to include EXTH flag region
    header_length = 232
    mobi = bytearray(header_length)
    struct.pack_into(">4sIIII", mobi, 0, b"MOBI", header_length, 2, 65001, 1)
    struct.pack_into(">I", mobi, 20, 8)  # file version (KF8-era)
    struct.pack_into(">I", mobi, 0x50, 1)  # first content
    struct.pack_into(">I", mobi, 0x54, first_non_book)
    fullname_offset = len(palmdoc) + header_length + len(exth)
    struct.pack_into(">II", mobi, 0x58, fullname_offset, len(fullname))
    struct.pack_into(">I", mobi, 0x60, 0x409)
    struct.pack_into(">I", mobi, 0x6C, first_image & 0xFFFFFFFF)
    # EXTH flags: bit 6 (0x40) = EXTH present
    struct.pack_into(">I", mobi, 0x80, 0x40)
    return palmdoc + bytes(mobi) + exth + fullname


def _build_palmdb(*, title: str, records: list[bytes], creator: bytes = b"MOBI") -> bytes:
    name = title.encode("latin-1", errors="replace")[:32].ljust(32, b"\x00")
    now = int(time.time()) + 2082844800
    num = len(records)
    header = bytearray(78)
    header[0:32] = name
    struct.pack_into(">HHIIIIII", header, 32, 0, 0, now, now, now, 0, 0, 0)
    header[60:64] = b"BOOK"
    header[64:68] = creator[:4].ljust(4, b"\x00")
    struct.pack_into(">IIH", header, 68, 0, 0, num)

    record_info = bytearray()
    data_start = 78 + 8 * num + 2
    offset = data_start
    for i, rec in enumerate(records):
        record_info += struct.pack(">I", offset)
        record_info += bytes([0])
        record_info += struct.pack(">I", i)[1:]
        offset += len(rec)
    return bytes(header) + bytes(record_info) + b"\x00\x00" + b"".join(records)
