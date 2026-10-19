"""PowerPoint 97-2003 (PPT) atom walk — extract TextCharsAtom / TextBytesAtom."""

from __future__ import annotations

import struct


def _u16(b: bytes, i: int) -> int:
    return struct.unpack_from("<H", b, i)[0]


def _u32(b: bytes, i: int) -> int:
    return struct.unpack_from("<I", b, i)[0]


def extract_ppt_texts(stream: bytes, *, max_chunks: int = 400) -> list[str]:
    """Walk PPT records and collect slide text atoms in document order."""
    out: list[str] = []
    i = 0
    n = len(stream)
    while i + 8 <= n and len(out) < max_chunks:
        # recVer(4) | recInstance(12) in first u16; recType u16; recLen u32
        info = _u16(stream, i)
        rec_type = _u16(stream, i + 2)
        rec_len = _u32(stream, i + 4)
        i += 8
        if rec_len < 0 or i + rec_len > n:
            break
        payload = stream[i : i + rec_len]
        i += rec_len

        # Container records: descend by resetting cursor... we already advanced.
        # For containers, payload itself is nested records — recurse.
        # Known containers: 0x03E8 Document, 0x03EE Slide, 0x0FF0 SlideListWithText, etc.
        if rec_type in (
            0x03E8,
            0x03EE,
            0x03F0,
            0x03F8,
            0x0FF0,
            0x0FF1,
            0x03EF,
            0x040C,
        ):
            out.extend(extract_ppt_texts(payload, max_chunks=max_chunks - len(out)))
            continue

        if rec_type == 0x0FA0:  # TextCharsAtom (UTF-16LE)
            text = payload.decode("utf-16-le", errors="replace").replace("\x00", "").strip()
            if text:
                out.append(text)
        elif rec_type == 0x0FA8:  # TextBytesAtom (latin-1)
            text = payload.decode("latin-1", errors="replace").replace("\x00", "").strip()
            if text:
                out.append(text)
        elif rec_type == 0x0FBA:  # CString
            # may be unicode depending on instance bit
            unicode = bool(info & 0xF000)  # rough: high nibble of instance
            if unicode or (len(payload) >= 2 and payload[1] == 0):
                text = payload.decode("utf-16-le", errors="replace").replace("\x00", "").strip()
            else:
                text = payload.decode("latin-1", errors="replace").replace("\x00", "").strip()
            if text and len(text) >= 2:
                out.append(text)

    # de-dupe consecutive
    cleaned: list[str] = []
    for t in out:
        if cleaned and cleaned[-1] == t:
            continue
        cleaned.append(t)
    return cleaned
