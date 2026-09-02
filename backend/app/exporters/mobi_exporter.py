"""Minimal Mobipocket (MOBI) exporter — PalmDOC uncompressed HTML payload."""

from __future__ import annotations

import struct
import time

from app.core.document_model import DocumentModel
from app.exporters.html_render import render_body_html, render_ebook_css


def export_mobi(model: DocumentModel) -> bytes:
    """
    Build a minimal PalmDB/MOBI file (uncompressed PalmDOC + MOBI header).

    Suitable for local conversion pipelines; advanced KF8/AZW3 features are out of scope.
    """
    title = (model.title or "Untitled")[:31]
    body = render_body_html(model, include_title=True)
    html = (
        "<html><head><meta http-equiv='Content-Type' content='text/html; charset=utf-8'/>"
        f"<title>{_esc(title)}</title>"
        f"<style>{render_ebook_css()}</style></head><body>\n{body}\n</body></html>\n"
    )
    payload = html.encode("utf-8")

    rec_size = 4096
    text_records = [payload[i : i + rec_size] for i in range(0, max(len(payload), 1), rec_size)]
    record0 = _build_record0(
        title=title,
        text_length=len(payload),
        text_record_count=len(text_records),
    )
    return _build_palmdb(title=title, records=[record0, *text_records])


def _esc(s: str) -> str:
    import html as h

    return h.escape(s)


def _build_record0(*, title: str, text_length: int, text_record_count: int) -> bytes:
    # PalmDOC header — 16 bytes
    palmdoc = struct.pack(
        ">HHIHHHH",
        1,  # no compression
        0,
        text_length & 0xFFFFFFFF,
        text_record_count & 0xFFFF,
        0,
        4096,
        0,  # encryption
    )

    fullname = title.encode("utf-8")[:64]
    header_length = 232
    mobi = bytearray(header_length)
    # identifier, headerLength, mobiType(2=book), encoding(65001=utf8), uid, fileVersion
    struct.pack_into(
        ">4sIIII",
        mobi,
        0,
        b"MOBI",
        header_length,
        2,
        65001,
        1,
    )
    struct.pack_into(">I", mobi, 20, 6)  # file version
    first_content = 1
    first_non_book = 1 + text_record_count
    struct.pack_into(">I", mobi, 0x50, first_content)
    struct.pack_into(">I", mobi, 0x54, first_non_book)
    fullname_offset = len(palmdoc) + header_length
    struct.pack_into(">II", mobi, 0x58, fullname_offset, len(fullname))
    struct.pack_into(">I", mobi, 0x60, 0x409)  # en-US
    struct.pack_into(">I", mobi, 0x6C, 0xFFFFFFFF)  # first image = none

    return palmdoc + bytes(mobi) + fullname


def _build_palmdb(*, title: str, records: list[bytes]) -> bytes:
    name = title.encode("latin-1", errors="replace")[:32].ljust(32, b"\x00")
    now = int(time.time()) + 2082844800
    num = len(records)
    # Fixed 78-byte header before record directory
    header = bytearray(78)
    header[0:32] = name
    struct.pack_into(">HHIIIIII", header, 32, 0, 0, now, now, now, 0, 0, 0)
    header[60:64] = b"BOOK"
    header[64:68] = b"MOBI"
    struct.pack_into(">IIH", header, 68, 0, 0, num)

    record_info = bytearray()
    data_start = 78 + 8 * num + 2
    offset = data_start
    for i, rec in enumerate(records):
        record_info += struct.pack(">I", offset)
        record_info += bytes([0])  # attributes
        record_info += struct.pack(">I", i)[1:]  # 3-byte unique id
        offset += len(rec)

    return bytes(header) + bytes(record_info) + b"\x00\x00" + b"".join(records)
