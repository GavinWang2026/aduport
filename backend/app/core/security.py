"""Upload security helpers: magic-bytes sniffing and extension whitelist."""

from __future__ import annotations

from app.core.exceptions import InvalidDocumentError, UnsupportedFormatError

# Declared format → allowed filename suffixes
_EXT_WHITELIST: dict[str, frozenset[str]] = {
    "md": frozenset({"md", "markdown", "txt"}),
    "txt": frozenset({"txt", "text"}),
    "html": frozenset({"html", "htm"}),
    "docx": frozenset({"docx"}),
    "pdf": frozenset({"pdf"}),
    "pptx": frozenset({"pptx"}),
    "epub": frozenset({"epub"}),
    "mobi": frozenset({"mobi", "azw", "azw3"}),
}

# Declared format → acceptable magic signatures (any match)
_MAGIC: dict[str, tuple[bytes, ...]] = {
    "pdf": (b"%PDF",),
    "docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),  # ZIP container
    "pptx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "epub": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    "mobi": (),  # PalmDB — validated in importer (BOOKMOBI)
    "md": (),  # text — validated as UTF-8 later
    "txt": (),
    "html": (),
}


def sniff_format(data: bytes) -> str | None:
    """Best-effort format detection from magic bytes."""
    head = data[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        # Could be docx or other zip; caller still checks declared format
        return "docx"
    sample = data[:2048].lstrip().lower()
    if sample.startswith(b"<!doctype html") or sample.startswith(b"<html"):
        return "html"
    return None


def validate_upload(
    data: bytes,
    declared_format: str,
    filename: str | None = "",
) -> None:
    """
    Enforce extension whitelist + magic-byte consistency.

    Raises UnsupportedFormatError / InvalidDocumentError on mismatch.
    """
    fmt = declared_format.lower().lstrip(".")
    name = (filename or "").replace("\\", "/").split("/")[-1]
    if name and "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        allowed = _EXT_WHITELIST.get(fmt)
        if allowed is not None and ext not in allowed:
            # Allow missing/odd extensions for md/txt (CLI may use .bin with --from)
            if fmt not in ("md", "txt", "html"):
                raise UnsupportedFormatError(
                    f"扩展名 '.{ext}' 与声明格式 '{fmt}' 不匹配，允许: {sorted(allowed)}"
                )

    magics = _MAGIC.get(fmt, ())
    if magics:
        if not any(data.startswith(m) or (m == b"%PDF" and b"%PDF" in data[:1024]) for m in magics):
            raise InvalidDocumentError(
                f"文件内容与声明格式 '{fmt}' 不一致（magic bytes 校验失败）: {name or '(unnamed)'}"
            )

    # Soft sniff: if we can detect another binary type, reject mismatch
    sniffed = sniff_format(data)
    if sniffed and fmt in ("pdf", "docx", "pptx", "epub") and sniffed != fmt:
        # ZIP-based formats share PK magic — only reject PDF vs ZIP mismatches
        if sniffed == "pdf" or fmt == "pdf":
            raise InvalidDocumentError(
                f"文件疑似 '{sniffed}'，但声明为 '{fmt}': {name or '(unnamed)'}"
            )
