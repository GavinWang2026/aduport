"""Minimal OLE Compound File Binary (CFB) stream reader (no third-party deps)."""

from __future__ import annotations

import struct
from typing import Any, Iterator

from aduport.importers.import_guard import swallow_import_error

_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_NOSTREAM = 0xFFFFFFFF


class CfbError(ValueError):
    pass


# Directory and stream names that a raw UTF-16 scrape otherwise returns as body text.
_CFB_DIRECTORY_NAMES = frozenset(
    {
        "root entry",
        "worddocument",
        "1table",
        "0table",
        "summaryinformation",
        "documentsummaryinformation",
        "compobj",
        "workbook",
        "powerpoint document",
        "current user",
        "pictures",
        "objectpool",
    }
)
_PRIMARY_STREAMS = frozenset(
    {"worddocument", "workbook", "book", "powerpoint document"}
)


def is_cfb_directory_name(text: str) -> bool:
    """True when ``text`` is only a CFB directory entry, not document text."""
    key = "".join(ch for ch in text if ch >= " ").strip().casefold()
    if key.startswith("\x05"):
        key = key[1:].strip()
    return key in _CFB_DIRECTORY_NAMES


def cfb_is_truncated(data: bytes) -> bool:
    """True when a CFB header is present but a primary stream does not fit.

    A half-cut ``.doc`` / ``.xls`` / ``.ppt`` still starts with the OLE magic.
    The text scrape then treats directory names (``Root Entry``, ``WordDocument``)
    as paragraphs. Callers must fail that file instead of returning the scrape.
    """
    if not data.startswith(_SIG):
        return False
    if len(data) < 512:
        return True
    try:
        pkg = _load_cfb(data)
    except CfbError:
        return True
    reader = pkg["read_entry_bytes"]
    for ent in pkg["entries"]:
        if ent.get("type") != 2:
            continue
        name = (ent.get("name") or "").casefold()
        if name not in _PRIMARY_STREAMS:
            continue
        size = int(ent.get("size") or 0)
        if size <= 0:
            continue
        try:
            blob = reader(ent)
        except CfbError:
            return True
        if len(blob) < size:
            return True
    return False


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def _load_cfb(data: bytes) -> dict[str, Any]:
    """Parse CFB header, FAT, and directory entries (with sibling/child links)."""
    if len(data) < 512 or not data.startswith(_SIG):
        raise CfbError("not a CFB/OLE compound document")

    sector_shift = _u16(data, 0x1E)
    # MS-CFB: sector_shift ≥ 9 (512-byte sectors). Tiny shifts make FAT I/O pathological.
    if sector_shift < 9 or sector_shift > 15:
        raise CfbError(f"invalid sector_shift {sector_shift}")
    sector_size = 1 << sector_shift
    mini_shift = _u16(data, 0x20)
    if mini_shift < 6 or mini_shift > 12:
        raise CfbError(f"invalid mini_sector_shift {mini_shift}")
    mini_size = 1 << mini_shift
    fat_count = _u32(data, 0x2C)
    dir_first = _u32(data, 0x30)
    mini_cutoff = _u32(data, 0x38)
    mini_fat_first = _u32(data, 0x3C)
    difat: list[int] = []
    for i in range(109):
        sec = _u32(data, 0x4C + 4 * i)
        if sec != 0xFFFFFFFF:
            difat.append(sec)

    def read_sector(sec: int) -> bytes:
        off = 512 + sec * sector_size
        if off + sector_size > len(data):
            raise CfbError(f"sector {sec} out of range")
        return data[off : off + sector_size]

    fat: list[int] = []
    try:
        for fat_sec in difat[:fat_count]:
            blob = read_sector(fat_sec)
            for i in range(0, len(blob), 4):
                fat.append(_u32(blob, i))
    except struct.error as exc:
        raise CfbError("corrupt FAT sector") from exc

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

    dir_blob = chain(dir_first)
    entries: list[dict[str, Any]] = []
    for i in range(0, len(dir_blob), 128):
        ent = dir_blob[i : i + 128]
        if len(ent) < 128:
            break
        name_len = _u16(ent, 0x40)
        raw_name = ent[0:64][: max(0, name_len - 2)]
        try:
            name = raw_name.decode("utf-16-le", errors="ignore").rstrip("\x00")
        except Exception as exc:
            name = ""
            swallow_import_error(exc, where="ole_cfb.py:_load_cfb")
        entries.append(
            {
                "index": len(entries),
                "name": name,
                "type": ent[0x42],  # 0 empty, 1 storage, 2 stream, 5 root
                "left": _u32(ent, 0x44),
                "right": _u32(ent, 0x48),
                "child": _u32(ent, 0x4C),
                "start": _u32(ent, 0x74),
                "size": _u32(ent, 0x78),
            }
        )

    root = next((e for e in entries if e["type"] == 5), None)
    mini_data = b""
    mini_fat: list[int] = []
    if root is not None and mini_fat_first < 0xFFFFFFFA:
        mini_data = chain(int(root["start"]))
        mini_fat_blob = chain(mini_fat_first)
        mini_fat = [_u32(mini_fat_blob, i) for i in range(0, len(mini_fat_blob), 4)]

    def read_entry_bytes(entry: dict[str, Any]) -> bytes:
        size = int(entry["size"])
        start = int(entry["start"])
        if size <= 0:
            return b""
        if size >= mini_cutoff or root is None or not mini_fat:
            return chain(start)[:size]
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

    return {
        "entries": entries,
        "root": root,
        "read_entry_bytes": read_entry_bytes,
    }


def _iter_sib_tree(entries: list[dict[str, Any]], node_id: int) -> Iterator[dict[str, Any]]:
    """In-order walk of a CFB red-black sibling tree rooted at ``node_id``.

    Malicious left/right self-loops must not hang (report P1-1).
    """
    if node_id == _NOSTREAM or node_id < 0 or node_id >= len(entries):
        return
    stack: list[int] = []
    seen: set[int] = set()
    cur: int | None = node_id
    while stack or cur is not None:
        while cur is not None and cur != _NOSTREAM and 0 <= cur < len(entries):
            if cur in seen:
                return
            seen.add(cur)
            stack.append(cur)
            left = int(entries[cur]["left"])
            cur = left if left != _NOSTREAM else None
        if not stack:
            break
        cur_i = stack.pop()
        yield entries[cur_i]
        right = int(entries[cur_i]["right"])
        cur = right if right != _NOSTREAM else None


def _find_child(entries: list[dict[str, Any]], parent: dict[str, Any], name: str) -> dict[str, Any] | None:
    child = int(parent.get("child", _NOSTREAM))
    want = name.lower()
    for ent in _iter_sib_tree(entries, child):
        if (ent.get("name") or "").lower() == want:
            return ent
    return None


def read_cfb_stream(data: bytes, stream_name: str) -> bytes | None:
    """Return a top-level (or unique) named stream, or None."""
    pkg = _load_cfb(data)
    entries = pkg["entries"]
    root = pkg["root"]
    # Prefer root children (correct for MSG attachments living under storages)
    if root is not None:
        hit = _find_child(entries, root, stream_name)
        if hit is not None and hit["type"] == 2:
            return pkg["read_entry_bytes"](hit)
    # Legacy fallback: first matching stream name anywhere (Workbook / WordDocument)
    target = next(
        (e for e in entries if e["type"] == 2 and e["name"].lower() == stream_name.lower()),
        None,
    )
    if target is None:
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
    return pkg["read_entry_bytes"](target)


def peek_cfb_path_size(data: bytes, *parts: str) -> int | None:
    """Return directory size for a stream path without reading stream bytes."""
    if not parts:
        return None
    try:
        pkg = _load_cfb(data)
    except CfbError:
        return None
    entries = pkg["entries"]
    root = pkg["root"]
    if root is None:
        return None
    cur: dict[str, Any] = root
    for i, part in enumerate(parts):
        nxt = _find_child(entries, cur, part)
        if nxt is None:
            return None
        if i == len(parts) - 1:
            if nxt["type"] != 2:
                return None
            return int(nxt.get("size") or 0)
        if nxt["type"] not in (1, 5):
            return None
        cur = nxt
    return None


def read_cfb_path(data: bytes, *parts: str) -> bytes | None:
    """Read a stream by storage path, e.g. ``('__attach_version1.0_#00000000', '__substg1.0_37010102')``."""
    if not parts:
        return None
    pkg = _load_cfb(data)
    entries = pkg["entries"]
    root = pkg["root"]
    if root is None:
        return None
    cur: dict[str, Any] = root
    for i, part in enumerate(parts):
        nxt = _find_child(entries, cur, part)
        if nxt is None:
            return None
        if i == len(parts) - 1:
            if nxt["type"] != 2:
                return None
            return pkg["read_entry_bytes"](nxt)
        if nxt["type"] not in (1, 5):  # storage or root
            return None
        cur = nxt
    return None


def list_cfb_child_names(data: bytes, *storage_path: str) -> list[str]:
    """List direct child entry names under a storage path (empty = root)."""
    pkg = _load_cfb(data)
    entries = pkg["entries"]
    root = pkg["root"]
    if root is None:
        return []
    cur: dict[str, Any] = root
    for part in storage_path:
        nxt = _find_child(entries, cur, part)
        if nxt is None or nxt["type"] not in (1, 5):
            return []
        cur = nxt
    child = int(cur.get("child", _NOSTREAM))
    return [e["name"] for e in _iter_sib_tree(entries, child) if e.get("name")]


def list_cfb_stream_names(data: bytes) -> list[str]:
    """Return every stream name in the CFB directory (type=2 entries)."""
    pkg = _load_cfb(data)
    return [e["name"] for e in pkg["entries"] if e["type"] == 2 and e.get("name")]


def list_attach_storage_names(data: bytes) -> list[str]:
    """MSG attachment storage names under the root (``__attach_version1.0_#…``)."""
    names = list_cfb_child_names(data)
    prefix = "__attach_version1.0_#"
    out = [n for n in names if n.lower().startswith(prefix)]
    out.sort(key=lambda n: n.lower())
    return out


def iter_stream_names(data: bytes) -> Iterator[str]:
    if len(data) < 512 or not data.startswith(_SIG):
        return
    try:
        names = list_cfb_stream_names(data)
        if names:
            yield from names
            return
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
