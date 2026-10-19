"""Best-effort text decoding for CSV/TXT imports (report P2-8)."""

from __future__ import annotations

from aduport.core.exceptions import InvalidDocumentError


def decode_text_bytes(data: bytes, *, filename: str = "") -> str:
    """Decode text bytes: UTF-8 (with BOM) → GB18030 fallback."""
    if not data:
        return ""
    # Strip UTF-8 BOM if present
    payload = data
    if payload.startswith(b"\xef\xbb\xbf"):
        payload = payload[3:]
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return payload.decode("gb18030")
    except UnicodeDecodeError as exc:
        label = filename or "input"
        raise InvalidDocumentError(
            f"文本编码无效（需要 UTF-8 或 GB18030）: {label}"
        ) from exc
