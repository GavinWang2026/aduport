"""Test helpers: build minimal CFB + BIFF8 / PPT atom fixtures."""

from __future__ import annotations

import struct


def _u16(n: int) -> bytes:
    return struct.pack("<H", n & 0xFFFF)


def _u32(n: int) -> bytes:
    return struct.pack("<I", n & 0xFFFFFFFF)


def _rec(code: int, payload: bytes) -> bytes:
    return _u16(code) + _u16(len(payload)) + payload


def _xl_unicode(text: str, *, short: bool = False) -> bytes:
    raw = text.encode("utf-16-le")
    cch = len(text)
    if short:
        return bytes([cch & 0xFF, 0x01]) + raw
    return _u16(cch) + bytes([0x01]) + raw


def build_minimal_biff8_workbook(
    sheet_name: str = "Data",
    cells: list[tuple[int, int, str]] | None = None,
) -> bytes:
    """Raw Workbook stream: globals + one worksheet with LABEL cells."""
    cells = cells or [(0, 0, "Hello"), (0, 1, "World"), (1, 0, "42")]
    # Globals
    out = bytearray()
    out += _rec(0x0809, _u16(0x0600) + _u16(0x0005) + _u16(0) + _u16(0) + _u32(0x0106) + _u32(0x06))  # BOF workbook
    # BOUNDSHEET — lbPlyPos patched after globals
    name_bytes = _xl_unicode(sheet_name, short=True)
    bs_payload = _u32(0) + bytes([0x00, 0x00]) + name_bytes
    bs_start = len(out)
    out += _rec(0x0085, bs_payload)
    out += _rec(0x000A, b"")  # EOF globals

    sheet_pos = len(out)
    struct.pack_into("<I", out, bs_start + 4, sheet_pos)

    out += _rec(0x0809, _u16(0x0600) + _u16(0x0010) + _u16(0) + _u16(0) + _u32(0x0106) + _u32(0x06))  # BOF sheet
    for row, col, text in cells:
        payload = _u16(row) + _u16(col) + _u16(0x000F) + _xl_unicode(text)
        out += _rec(0x0204, payload)
    out += _rec(0x000A, b"")
    return bytes(out)


def build_minimal_ppt_stream(texts: list[str]) -> bytes:
    """PowerPoint Document stream with nested TextCharsAtom records."""
    body = bytearray()
    for t in texts:
        raw = t.encode("utf-16-le")
        # TextCharsAtom 0x0FA0
        body += _u16(0x0000) + _u16(0x0FA0) + _u32(len(raw)) + raw
    # Wrap in SlideListWithText container 0x0FF0
    return _u16(0x000F) + _u16(0x0FF0) + _u32(len(body)) + bytes(body)


def build_cfb_with_streams(streams: dict[str, bytes]) -> bytes:
    """Minimal CFB v3 with multiple named streams under the root (sibling chain).

    Used for Outlook .msg–like fixtures (``__substg1.0_*`` / ``__properties_*``).
    """
    if not streams:
        raise ValueError("streams must be non-empty")
    sector_size = 512
    # Pad each stream to >= 4096 so mini-stream path is unused (same as single-stream helper).
    prepared: list[tuple[str, bytes, int]] = []
    for name, raw in streams.items():
        actual = len(raw)
        data = bytearray(raw)
        # Pad storage sectors (>=4096) so mini-stream is unused; dir size stays actual.
        if len(data) < 4096:
            data.extend(b"\x00" * (4096 - len(data)))
        prepared.append((name, bytes(data), actual))

    # Layout: [header][FAT][dir…][data…]
    fat_sec = 0
    dir_sec = 1
    # Directory: root + N stream entries; may need multiple dir sectors.
    n_entries = 1 + len(prepared)
    dir_bytes_needed = n_entries * 128
    dir_sectors = max(1, (dir_bytes_needed + sector_size - 1) // sector_size)
    data_start = 2 + (dir_sectors - 1)

    fat = [0xFFFFFFFF] * (128 * 4)  # up to 4 FAT sectors worth of slots
    fat[fat_sec] = 0xFFFFFFFD
    # dir chain
    for i in range(dir_sectors):
        sec = dir_sec + i
        if i + 1 < dir_sectors:
            fat[sec] = dir_sec + i + 1
        else:
            fat[sec] = 0xFFFFFFFE

    data_cursor = data_start
    stream_meta: list[tuple[str, int, int]] = []  # name, start_sec, size
    for name, stored, size in prepared:
        # Chain FAT by padded storage length; directory size stays ``size`` (actual).
        sectors_needed = (len(stored) + sector_size - 1) // sector_size
        start = data_cursor
        for i in range(sectors_needed):
            sec = start + i
            if i + 1 < sectors_needed:
                fat[sec] = start + i + 1
            else:
                fat[sec] = 0xFFFFFFFE
        stream_meta.append((name, start, size))
        data_cursor += sectors_needed

    fat_count = 1
    fat_blob = b"".join(_u32(x) for x in fat[:128]).ljust(sector_size, b"\xff")

    def dir_entry(
        name: str,
        obj_type: int,
        start: int,
        sz: int,
        *,
        left: int = 0xFFFFFFFF,
        right: int = 0xFFFFFFFF,
        child: int = 0xFFFFFFFF,
        color: int = 1,
    ) -> bytes:
        nm = (name + "\x00").encode("utf-16-le")
        nm = nm[:64].ljust(64, b"\x00")
        name_len = min(len(name) * 2 + 2, 64)
        ent = bytearray(128)
        ent[0:64] = nm
        ent[0x40:0x42] = _u16(name_len)
        ent[0x42] = obj_type
        ent[0x43] = color
        ent[0x44:0x48] = _u32(left)
        ent[0x48:0x4C] = _u32(right)
        ent[0x4C:0x50] = _u32(child)
        ent[0x74:0x78] = _u32(start)
        ent[0x78:0x7C] = _u32(sz)
        return bytes(ent)

    # Root child = first stream (sid 1); streams form a right-linked list.
    entries = [dir_entry("Root Entry", 5, 0xFFFFFFFE, 0, child=1)]
    for i, (name, start, size) in enumerate(stream_meta):
        sid = i + 1
        right = (sid + 1) if i + 1 < len(stream_meta) else 0xFFFFFFFF
        entries.append(dir_entry(name, 2, start, size, right=right))
    dir_blob = b"".join(entries).ljust(dir_sectors * sector_size, b"\x00")

    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    header[0x18:0x1A] = _u16(0x003E)
    header[0x1A:0x1C] = _u16(0x0003)
    header[0x1C:0x1E] = _u16(0xFFFE)
    header[0x1E:0x20] = _u16(9)
    header[0x20:0x22] = _u16(6)
    header[0x2C:0x30] = _u32(fat_count)
    header[0x30:0x34] = _u32(dir_sec)
    header[0x38:0x3C] = _u32(4096)
    header[0x3C:0x40] = _u32(0xFFFFFFFE)
    header[0x40:0x44] = _u32(0)
    header[0x44:0x48] = _u32(0xFFFFFFFE)
    header[0x48:0x4C] = _u32(0)
    header[0x4C:0x50] = _u32(fat_sec)
    for i in range(1, 109):
        header[0x4C + 4 * i : 0x50 + 4 * i] = _u32(0xFFFFFFFF)

    out = bytearray()
    out += header
    out += fat_blob
    out += dir_blob
    for _name, stored, _size in prepared:
        for i in range(0, len(stored), sector_size):
            chunk = stored[i : i + sector_size]
            out += chunk.ljust(sector_size, b"\x00")
    return bytes(out)


def build_minimal_msg(
    *,
    subject: str = "Hello Subject",
    body: str = "Body line one",
    sender: str = "alice@example.com",
) -> bytes:
    """Hand-rolled Outlook .msg–like CFB (no monkeypatch) for importer tests."""

    def _u16le(text: str) -> bytes:
        return text.encode("utf-16-le")

    streams = {
        "__substg1.0_0037001F": _u16le(subject),  # PR_SUBJECT
        "__substg1.0_1000001F": _u16le(body),  # PR_BODY
        "__substg1.0_0C1F001F": _u16le(sender),  # PR_SENDER_EMAIL_ADDRESS-ish
        "__properties_version1.0": b"\x00" * 32,
    }
    return build_cfb_with_streams(streams)


def build_cfb_with_stream(stream_name: str, stream_data: bytes) -> bytes:
    """
    Minimal CFB v3 with a single named stream stored in regular FAT sectors.
    Pads stream to >= 4096 so mini-stream path is unused.
    """
    return build_cfb_with_streams({stream_name: stream_data})


def build_minimal_xls(**kwargs) -> bytes:
    wb = build_minimal_biff8_workbook(**kwargs)
    return build_cfb_with_stream("Workbook", wb)


def build_minimal_ppt(texts: list[str] | None = None) -> bytes:
    texts = texts or ["Slide One Title", "Bullet alpha", "Bullet beta"]
    return build_cfb_with_stream("PowerPoint Document", build_minimal_ppt_stream(texts))
