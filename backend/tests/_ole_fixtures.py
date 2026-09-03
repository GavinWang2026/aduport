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


def build_cfb_with_stream(stream_name: str, stream_data: bytes) -> bytes:
    """
    Minimal CFB v3 with a single named stream stored in regular FAT sectors.
    Pads stream to >= 4096 so mini-stream path is unused.
    """
    sector_size = 512
    data = bytearray(stream_data)
    if len(data) < 4096:
        data.extend(b"\x00" * (4096 - len(data)))
    size = len(stream_data)  # logical size before pad... use padded for chain simplicity
    # Use padded length as stored size so FAT chain covers all
    stored = bytes(data)
    size = len(stored)

    sectors_needed = (size + sector_size - 1) // sector_size
    # Layout: [header sec0][FAT sec1][dir sec2][data sec3..]
    fat_sec = 0
    dir_sec = 1
    data_start = 2
    fat = [0xFFFFFFFF] * 128  # one FAT sector holds 128 entries
    # chain data sectors
    for i in range(sectors_needed):
        sec = data_start + i
        if i + 1 < sectors_needed:
            fat[sec] = data_start + i + 1
        else:
            fat[sec] = 0xFFFFFFFE  # ENDOFCHAIN
    fat[fat_sec] = 0xFFFFFFFD  # FATSECT
    fat[dir_sec] = 0xFFFFFFFE

    def dir_entry(name: str, obj_type: int, start: int, sz: int, color: int = 1) -> bytes:
        nm = (name + "\x00").encode("utf-16-le")
        nm = nm[:64].ljust(64, b"\x00")
        name_len = min(len(name) * 2 + 2, 64)
        ent = bytearray(128)
        ent[0:64] = nm.ljust(64, b"\x00")
        ent[0x40:0x42] = _u16(name_len)
        ent[0x42] = obj_type
        ent[0x43] = color
        ent[0x44:0x48] = _u32(0xFFFFFFFF)  # left
        ent[0x48:0x4C] = _u32(0xFFFFFFFF)  # right
        ent[0x4C:0x50] = _u32(0xFFFFFFFF)  # child
        ent[0x74:0x78] = _u32(start)
        ent[0x78:0x7C] = _u32(sz)
        return bytes(ent)

    # Root (type 5) child = stream entry index 1
    root = bytearray(dir_entry("Root Entry", 5, 0xFFFFFFFE, 0))
    root[0x4C:0x50] = _u32(1)  # child sid = 1
    stream_ent = dir_entry(stream_name, 2, data_start, size)
    dir_blob = bytes(root) + stream_ent
    dir_blob = dir_blob.ljust(sector_size, b"\x00")

    fat_blob = b"".join(_u32(x) for x in fat).ljust(sector_size, b"\xff")

    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    header[0x18:0x1A] = _u16(0x003E)
    header[0x1A:0x1C] = _u16(0x0003)
    header[0x1C:0x1E] = _u16(0xFFFE)
    header[0x1E:0x20] = _u16(9)  # sector shift
    header[0x20:0x22] = _u16(6)  # mini shift
    header[0x2C:0x30] = _u32(1)  # FAT count
    header[0x30:0x34] = _u32(dir_sec)
    header[0x38:0x3C] = _u32(4096)  # mini cutoff
    header[0x3C:0x40] = _u32(0xFFFFFFFE)  # no minifat
    header[0x40:0x44] = _u32(0)
    header[0x44:0x48] = _u32(0xFFFFFFFE)
    header[0x48:0x4C] = _u32(0)
    header[0x4C:0x50] = _u32(fat_sec)  # first FAT in DIFAT
    for i in range(1, 109):
        header[0x4C + 4 * i : 0x50 + 4 * i] = _u32(0xFFFFFFFF)

    out = bytearray()
    out += header
    # sector 0 = FAT
    out += fat_blob
    # sector 1 = directory
    out += dir_blob
    # data sectors
    for i in range(sectors_needed):
        chunk = stored[i * sector_size : (i + 1) * sector_size]
        out += chunk.ljust(sector_size, b"\x00")
    return bytes(out)


def build_minimal_xls(**kwargs) -> bytes:
    wb = build_minimal_biff8_workbook(**kwargs)
    return build_cfb_with_stream("Workbook", wb)


def build_minimal_ppt(texts: list[str] | None = None) -> bytes:
    texts = texts or ["Slide One Title", "Bullet alpha", "Bullet beta"]
    return build_cfb_with_stream("PowerPoint Document", build_minimal_ppt_stream(texts))
