"""Lightweight PalmDB/MOBI text extraction (no docling) for HTML/plain payloads."""

from __future__ import annotations

from aduport.importers.import_guard import swallow_import_error
import re
import struct

from aduport.core.document_model import DocumentModel, Image, Paragraph


def try_extract_mobi_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    """
    Best-effort extract when records hold uncompressed (or lightly compressed) HTML/text.

    Returns None if the container cannot be parsed or yields no usable text.
    """
    if len(data) < 78:
        return None
    try:
        num_records = struct.unpack_from(">H", data, 76)[0]
    except struct.error:
        return None
    if num_records < 2 or 78 + num_records * 8 > len(data):
        return None

    offsets: list[int] = []
    for i in range(num_records):
        off = struct.unpack_from(">I", data, 78 + i * 8)[0]
        offsets.append(off)
    offsets.append(len(data))

    # Record 0 is PalmDOC/MOBI header; text usually starts at record 1.
    # JPEG/PNG records are the embedded figures (AZW3/KF8 recindex), not junk.
    chunks: list[bytes] = []
    images: list[Image] = []
    for i in range(1, min(num_records, 64)):
        start, end = offsets[i], offsets[i + 1]
        if start >= end or end > len(data):
            break
        rec = data[start:end]
        if rec[:3] == b"\xff\xd8\xff":
            images.append(Image(data=rec, alt_text="image", mime_type="image/jpeg"))
            continue
        if rec[:8] == b"\x89PNG\r\n\x1a\n":
            images.append(Image(data=rec, alt_text="image", mime_type="image/png"))
            continue
        chunks.append(rec)

    if not chunks and not images:
        return None
    if not chunks:
        return DocumentModel(title=title or "mobi", nodes=list(images))

    raw = b"".join(chunks)
    # Try PalmDOC decompress if it looks binary-heavy; else decode as utf-8
    text = _decode_payload(raw)
    if not text or len(text.strip()) < 8:
        return None

    # Prefer HTML body
    lower = text.lower()
    if "<html" in lower or "<body" in lower or "<p" in lower:
        from aduport.importers.html_importer import import_html

        model = import_html(text.encode("utf-8", errors="replace"))
        if title and not model.title:
            model.title = title
        if images:
            model.nodes.extend(images)
        return model

    # Plain paragraphs
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paras:
        paras = [text.strip()]
    nodes = [Paragraph(text=p[:8000]) for p in paras[:500]]
    nodes.extend(images)
    return DocumentModel(title=title or "mobi", nodes=nodes)


def _decode_payload(raw: bytes) -> str:
    # Heuristic: if mostly printable UTF-8, use as-is
    sample = raw[:4096]
    printable = sum(1 for b in sample if 9 <= b <= 13 or 32 <= b < 127 or b >= 128)
    if sample and printable / len(sample) > 0.85:
        return raw.decode("utf-8", errors="replace")

    # PalmDOC LZ77-ish decompress (subset)
    try:
        out = _palmdoc_decompress(raw)
        return out.decode("utf-8", errors="replace")
    except Exception as exc:
        return swallow_import_error(exc, where='mobi_text.py:82', default=raw.decode("utf-8", errors="replace"))


def _palmdoc_decompress(data: bytes) -> bytes:
    """PalmDOC compression (as used by MOBI text records)."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        b = data[i]
        i += 1
        if b == 0x00:
            out.append(0)
        elif 0x01 <= b <= 0x08:
            out.extend(data[i : i + b])
            i += b
        elif 0x09 <= b <= 0x7F:
            out.append(b)
        elif 0x80 <= b <= 0xBF:
            if i >= n:
                break
            b2 = data[i]
            i += 1
            distance = ((b << 8) | b2) & 0x3FFF
            distance >>= 3
            length = (b2 & 0x07) + 3
            if distance == 0 or distance > len(out):
                continue
            for _ in range(length):
                out.append(out[-distance])
        else:
            # 0xC0–0xFF: space + (b & 0x7F)
            out.append(0x20)
            out.append(b & 0x7F)
    return bytes(out)
