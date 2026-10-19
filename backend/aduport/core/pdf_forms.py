"""Read and fill uncompressed PDF AcroForm text fields.

Field dictionaries stored in compressed object streams are reported and left
unchanged. This does not create appearance streams or certificate signatures.
"""

from __future__ import annotations

import re


class PdfFormError(ValueError):
    """The file is not a PDF, or the requested field cannot be updated."""


_OBJ_RE = re.compile(rb"(\d+)\s+\d+\s+obj\b(.*?)\bendobj\b", re.S)
_KEY_RE_CACHE: dict[bytes, re.Pattern[bytes]] = {}


def _key_re(name: bytes) -> re.Pattern[bytes]:
    pattern = _KEY_RE_CACHE.get(name)
    if pattern is None:
        pattern = re.compile(rb"/" + re.escape(name) + rb"(?![A-Za-z])\s*")
        _KEY_RE_CACHE[name] = pattern
    return pattern


def _require_pdf(data: bytes) -> None:
    if not data or not data.startswith(b"%PDF"):
        raise PdfFormError("不是有效的 PDF")


def _skip_ws(data: bytes, index: int) -> int:
    while index < len(data) and data[index] in b" \t\r\n":
        index += 1
    return index


def _read_literal(data: bytes, index: int) -> tuple[str, int]:
    """``index`` points at ``(``."""
    index += 1
    out = bytearray()
    depth = 1
    while index < len(data) and depth:
        char = data[index]
        if char == ord("\\") and index + 1 < len(data):
            nxt = data[index + 1]
            mapped = {
                ord("n"): b"\n",
                ord("r"): b"\r",
                ord("t"): b"\t",
                ord("b"): b"\b",
                ord("f"): b"\f",
                ord("("): b"(",
                ord(")"): b")",
                ord("\\"): b"\\",
            }
            if nxt in mapped:
                out.extend(mapped[nxt])
                index += 2
                continue
            if nxt in b"01234567":
                octal = bytes([nxt])
                index += 2
                for _ in range(2):
                    if index < len(data) and data[index] in b"01234567":
                        octal += bytes([data[index]])
                        index += 1
                    else:
                        break
                out.append(int(octal, 8) & 0xFF)
                continue
            out.append(nxt)
            index += 2
            continue
        if char == ord("("):
            depth += 1
            out.append(char)
            index += 1
            continue
        if char == ord(")"):
            depth -= 1
            if depth == 0:
                return out.decode("utf-8", "replace"), index + 1
            out.append(char)
            index += 1
            continue
        out.append(char)
        index += 1
    return out.decode("utf-8", "replace"), index


def _read_hex(data: bytes, index: int) -> tuple[str, int]:
    """``index`` points at ``<``."""
    end = data.find(b">", index + 1)
    if end < 0:
        return "", len(data)
    raw_hex = re.sub(rb"\s+", b"", data[index + 1 : end])
    if len(raw_hex) % 2:
        raw_hex += b"0"
    try:
        raw = bytes.fromhex(raw_hex.decode("ascii"))
    except ValueError:
        return "", end + 1
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace"), end + 1
    return raw.decode("latin-1", "replace"), end + 1


def _value_span(body: bytes, key: bytes) -> tuple[str | None, int, int]:
    match = _key_re(key).search(body)
    if not match:
        return None, -1, -1
    index = _skip_ws(body, match.end())
    if index >= len(body):
        return None, -1, -1
    if body[index] == ord("("):
        text, end = _read_literal(body, index)
        return text, match.start(), end
    if body[index] == ord("<") and not body.startswith(b"<<", index):
        text, end = _read_hex(body, index)
        return text, match.start(), end
    if body[index] == ord("/"):
        end = index + 1
        while end < len(body) and body[end:end + 1].isalnum():
            end += 1
        return body[index + 1 : end].decode("ascii", "replace"), match.start(), end
    return None, -1, -1


def _field_type(body: bytes) -> str:
    compact = re.sub(rb"\s+", b"", body)
    if b"/FT/Btn" in compact:
        return "button"
    if b"/FT/Ch" in compact:
        return "choice"
    if b"/FT/Sig" in compact:
        return "signature"
    if b"/FT/Tx" in compact:
        return "text"
    return "unknown"


def _encode_pdf_string(text: str) -> bytes:
    if all(32 <= ord(ch) < 127 and ch not in "()\\" for ch in text):
        return b"(" + text.encode("ascii") + b")"
    if all(ord(ch) < 128 for ch in text):
        escaped = (
            text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        )
        return b"(" + escaped.encode("ascii") + b")"
    encoded = text.encode("utf-16-be")
    return b"<FEFF" + encoded.hex().upper().encode("ascii") + b">"


def extract_form_fields(data: bytes) -> dict:
    """Return uncompressed widget fields and whether an AcroForm was unreadable."""
    _require_pdf(data)
    fields: list[dict[str, str]] = []
    for match in _OBJ_RE.finditer(data):
        body = match.group(2)
        dict_body = body.split(b"stream", 1)[0]
        name, _, _ = _value_span(dict_body, b"T")
        if not name:
            continue
        value, _, _ = _value_span(dict_body, b"V")
        fields.append(
            {
                "name": name,
                "value": value or "",
                "type": _field_type(dict_body),
            }
        )
    compressed = b"/AcroForm" in data and not fields
    return {"fields": fields, "compressed_form": compressed}


def fill_form_fields(data: bytes, values: dict[str, str]) -> bytes:
    """Replace ``/V`` on uncompressed fields. Unknown names are rejected."""
    _require_pdf(data)
    if not values:
        raise PdfFormError("没有要填写的字段")
    report = extract_form_fields(data)
    if report["compressed_form"]:
        raise PdfFormError("表单在压缩对象流里，当前不能填写")
    known = {item["name"] for item in report["fields"]}
    missing = [name for name in values if name not in known]
    if missing:
        raise PdfFormError("没有这些字段: " + ", ".join(missing))

    spans: list[tuple[int, int, bytes]] = []
    for match in _OBJ_RE.finditer(data):
        body = match.group(2)
        dict_body = body.split(b"stream", 1)[0]
        body_at = match.start(2)
        name, _, name_end = _value_span(dict_body, b"T")
        if name not in values:
            continue
        encoded = b"/V " + _encode_pdf_string(str(values[name]))
        _, value_start, value_end = _value_span(dict_body, b"V")
        if value_start >= 0:
            spans.append((body_at + value_start, body_at + value_end, encoded))
        else:
            spans.append((body_at + name_end, body_at + name_end, b" " + encoded))
    out = bytearray(data)
    for start, end, repl in sorted(spans, key=lambda item: item[0], reverse=True):
        out[start:end] = repl
    return bytes(out)
