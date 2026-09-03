"""BIFF8 Workbook subset parser → sheet grids (LABEL / LABELSST / NUMBER / RK)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field


@dataclass
class BiffSheet:
    name: str
    # sparse map (row, col) -> display text
    cells: dict[tuple[int, int], str] = field(default_factory=dict)

    def as_rows(self, max_rows: int = 500, max_cols: int = 64) -> list[list[str]]:
        if not self.cells:
            return []
        max_r = min(max(r for r, _ in self.cells), max_rows - 1)
        max_c = min(max(c for _, c in self.cells), max_cols - 1)
        rows: list[list[str]] = []
        for r in range(max_r + 1):
            row = [self.cells.get((r, c), "") for c in range(max_c + 1)]
            if any(cell.strip() for cell in row):
                rows.append(row)
        return rows


def _u16(b: bytes, i: int) -> int:
    return struct.unpack_from("<H", b, i)[0]


def _u32(b: bytes, i: int) -> int:
    return struct.unpack_from("<I", b, i)[0]


def _f64(b: bytes, i: int) -> float:
    return struct.unpack_from("<d", b, i)[0]


def _rk_to_float(rk: int) -> float:
    """Decode RK compressed number (MS-XLS)."""
    if rk & 0x02:
        # integer
        val = rk >> 2
        if val & 0x20000000:
            val |= ~0x3FFFFFFF
        num = float(val)
    else:
        # float bits in high 30
        buf = struct.pack("<I", rk & 0xFFFFFFFC) + b"\x00\x00\x00\x00"
        num = struct.unpack("<d", buf)[0]
    if rk & 0x01:
        num /= 100.0
    return num


def _fmt_num(n: float) -> str:
    if n == int(n) and abs(n) < 1e15:
        return str(int(n))
    return f"{n:.10g}"


def _read_biff_string(data: bytes, offset: int) -> tuple[str, int]:
    """Read BIFF8 UnicodeString at offset; return (text, new_offset)."""
    if offset + 3 > len(data):
        return "", offset
    cch = _u16(data, offset)
    flags = data[offset + 2]
    offset += 3
    # Skip rich-text / phonetic runs headers when present
    if flags & 0x08:  # rich
        if offset + 2 > len(data):
            return "", offset
        cRuns = _u16(data, offset)
        offset += 2
    else:
        cRuns = 0
    if flags & 0x04:  # phonetic
        if offset + 4 > len(data):
            return "", offset
        cbExt = _u32(data, offset)
        offset += 4
    else:
        cbExt = 0
    if flags & 0x01:  # unicode
        nbytes = cch * 2
        raw = data[offset : offset + nbytes]
        text = raw.decode("utf-16-le", errors="replace")
        offset += nbytes
    else:
        raw = data[offset : offset + cch]
        text = raw.decode("latin-1", errors="replace")
        offset += cch
    offset += cRuns * 4 + cbExt
    return text, offset


def _iter_records(stream: bytes):
    i = 0
    n = len(stream)
    while i + 4 <= n:
        code = _u16(stream, i)
        length = _u16(stream, i + 2)
        i += 4
        payload = stream[i : i + length]
        i += length
        # CONTINUE stitching for long payloads (SST etc.) handled by caller when needed
        yield code, payload


def _decode_sst(payload: bytes, continues: list[bytes]) -> list[str]:
    blob = payload + b"".join(continues)
    if len(blob) < 8:
        return []
    # cstTotal, cstUnique
    unique = _u32(blob, 4)
    offset = 8
    out: list[str] = []
    for _ in range(unique):
        if offset >= len(blob):
            break
        text, offset = _read_biff_string(blob, offset)
        out.append(text)
    return out


def parse_biff8_workbook(stream: bytes) -> list[BiffSheet]:
    """Parse BIFF8 Workbook/Book stream into sheets with cell text."""
    sheets_meta: list[tuple[str, int]] = []  # (name, lbPlyPos)
    sst: list[str] = []
    records = list(_iter_records(stream))

    # First pass: globals — SST + BOUNDSHEET
    i = 0
    while i < len(records):
        code, payload = records[i]
        if code == 0x00FC:  # SST
            cont: list[bytes] = []
            j = i + 1
            while j < len(records) and records[j][0] == 0x003C:
                cont.append(records[j][1])
                j += 1
            sst = _decode_sst(payload, cont)
            i = j
            continue
        if code == 0x0085 and len(payload) >= 8:  # BOUNDSHEET / BoundSheet8
            pos = _u32(payload, 0)
            # ShortXLUnicodeString: cch u8 + flags u8 + chars
            cch = payload[6]
            flags = payload[7] if len(payload) > 7 else 0
            if flags & 0x01:
                raw = payload[8 : 8 + cch * 2]
                name = raw.decode("utf-16-le", errors="replace")
            else:
                raw = payload[8 : 8 + cch]
                name = raw.decode("latin-1", errors="replace")
            sheets_meta.append((name or f"Sheet{len(sheets_meta)+1}", pos))
        i += 1

    if not sheets_meta:
        sheets_meta = [("Sheet1", 0)]

    # Build sheet bodies by scanning BOF(worksheet)/EOF regions
    sheets: list[BiffSheet] = []
    sheet_idx = -1
    current: BiffSheet | None = None
    in_sheet = False

    for code, payload in records:
        if code == 0x0809 and len(payload) >= 4:  # BOF
            dt = _u16(payload, 2)
            if dt == 0x0010:  # worksheet
                sheet_idx += 1
                name = (
                    sheets_meta[sheet_idx][0]
                    if sheet_idx < len(sheets_meta)
                    else f"Sheet{sheet_idx+1}"
                )
                current = BiffSheet(name=name)
                sheets.append(current)
                in_sheet = True
            else:
                in_sheet = False
            continue
        if code == 0x000A:  # EOF
            in_sheet = False
            current = None
            continue
        if not in_sheet or current is None:
            continue

        if code == 0x00FD and len(payload) >= 10:  # LABELSST
            row, col = _u16(payload, 0), _u16(payload, 2)
            idx = _u32(payload, 6)
            if 0 <= idx < len(sst):
                current.cells[(row, col)] = sst[idx]
        elif code == 0x0204 and len(payload) >= 8:  # LABEL
            row, col = _u16(payload, 0), _u16(payload, 2)
            text, _ = _read_biff_string(payload, 6)
            current.cells[(row, col)] = text
        elif code == 0x0203 and len(payload) >= 14:  # NUMBER
            row, col = _u16(payload, 0), _u16(payload, 2)
            current.cells[(row, col)] = _fmt_num(_f64(payload, 6))
        elif code == 0x027E and len(payload) >= 10:  # RK
            row, col = _u16(payload, 0), _u16(payload, 2)
            rk = _u32(payload, 6)
            current.cells[(row, col)] = _fmt_num(_rk_to_float(rk))
        elif code == 0x00BD and len(payload) >= 6:  # MULRK
            row = _u16(payload, 0)
            col_first = _u16(payload, 2)
            # trailing col_last u16; body of (xf u16 + rk u32) repeats
            body = payload[4:-2] if len(payload) >= 8 else payload[4:]
            col = col_first
            for off in range(0, len(body) - 5, 6):
                rk = _u32(body, off + 2)
                current.cells[(row, col)] = _fmt_num(_rk_to_float(rk))
                col += 1
        elif code == 0x0006 and len(payload) >= 14:  # FORMULA — use cached number if any
            row, col = _u16(payload, 0), _u16(payload, 2)
            # bytes 6-13: FormulaValue
            b6 = payload[6]
            if b6 not in (0x00, 0x01, 0x02, 0x03):
                try:
                    current.cells[(row, col)] = _fmt_num(_f64(payload, 6))
                except Exception:
                    pass

    return sheets
