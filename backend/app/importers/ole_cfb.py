"""Minimal OLE Compound File Binary (CFB) stream reader (no third-party deps)."""

from __future__ import annotations

import struct
from typing import Iterator


_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class CfbError(ValueError):
    pass


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _i32(data: bytes, off: int) -> int:
    return struct.unpack_from("<i", data, off)[0]


def read_cfb_stream(data: bytes, stream_name: str) -> bytes | None:
    """Return the named stream bytes from an OLE CFB container, or None."""
    if len(data) < 512 or not data.startswith(_SIG):
        raise CfbError("not a CFB/OLE compound document")

    sector_shift = _u16(data, 0x1E)
    sector_size = 1 << sector_shift
    mini_shift = _u16(data, 0x20)
    mini_size = 1 << mini_shift
    fat_count = _u32(data, 0x2C)
    dir_first = _u32(data, 0x30)
    mini_cutoff = _u32(data, 0x38)
    mini_fat_first = _u32(data, 0x3C)
    # DIFAT first 109 entries at 0x4C
    difat: list[int] = []
    for i in range(109):
        sec = _u32(data, 0x4C + 4 * i)
        if sec != 0xFFFFFFFF:
            difat.append(sec)
    # Extra DIFAT sectors ignored for typical small office files

    def sector_offset(sec: int) -> int:
        return 512 + sec * sector_size

    def read_sector(sec: int) -> bytes:
        off = sector_offset(sec)
        if off + sector_size > len(data):
            raise CfbError(f"sector {sec} out of range")
        return data[off : off + sector_size]

    # Build FAT
    fat: list[int] = []
    for fat_sec in difat[:fat_count]:
        blob = read_sector(fat_sec)
        for i in range(0, len(blob), 4):
            fat.append(_u32(blob, i))

    def chain(start: int) -> bytes:
        out = bytearray()
        seen: set[int] = set()
        sec = start
        while sec < 0xFFFFFFFA:
            if sec in seen or sec >= len(fat):
                break
            seen.add(sec)
            out.extend(read_sector(sec))
            sec = fat[sec]
        return bytes(out)

    # Directory entries are 128 bytes each
    dir_blob = chain(dir_first)
    entries = []
    for i in range(0, len(dir_blob), 128):
        ent = dir_blob[i : i + 128]
        if len(ent) < 128:
            break
        name_len = _u16(ent, 0x40)
        raw_name = ent[0:64][: max(0, name_len - 2)]
        try:
            name = raw_name.decode("utf-16-le", errors="ignore").rstrip("\x00")
        except Exception:
            name = ""
        obj_type = ent[0x42]
        start_sec = _u32(ent, 0x74)
        size = _u32(ent, 0x78)
        entries.append(
            {
                "name": name,
                "type": obj_type,  # 5=root, 1=storage, 2=stream
                "start": start_sec,
                "size": size,
            }
        )

    root = next((e for e in entries if e["type"] == 5), None)
    target = next(
        (e for e in entries if e["type"] == 2 and e["name"].lower() == stream_name.lower()),
        None,
    )
    if target is None:
        # Case-insensitive contains (Workbook / Book / PowerPoint Document)
        target = next(
            (
                e
                for e in entries
                if e["type"] == 2 and stream_name.lower() in (e["name"] or "").lower()
            ),
            None,
        )
    if target is None:
        return None

    size = int(target["size"])
    start = int(target["start"])
    if size >= mini_cutoff and root is not None:
        # Regular stream via FAT
        return chain(start)[:size]

    # Mini-stream: data lives in root's stream, addressed via MiniFAT
    if root is None:
        return chain(start)[:size]
    mini_data = chain(int(root["start"]))
    mini_fat_blob = chain(mini_fat_first) if mini_fat_first < 0xFFFFFFFA else b""
    mini_fat = [_u32(mini_fat_blob, i) for i in range(0, len(mini_fat_blob), 4)]
    out = bytearray()
    sec = start
    seen: set[int] = set()
    while sec < 0xFFFFFFFA and len(out) < size:
        if sec in seen:
            break
        seen.add(sec)
        off = sec * mini_size
        out.extend(mini_data[off : off + mini_size])
        if sec >= len(mini_fat):
            break
        sec = mini_fat[sec]
    return bytes(out[:size])


def iter_stream_names(data: bytes) -> Iterator[str]:
    if len(data) < 512 or not data.startswith(_SIG):
        return
    try:
        # Lightweight: reuse reader internals via probing common names
        for name in (
            "Workbook",
            "Book",
            "PowerPoint Document",
            "Current User",
            "Pictures",
        ):
            if read_cfb_stream(data, name) is not None:
                yield name
    except CfbError:
        return
