"""Rule-based redaction and a visible text watermark.

Text files are rewritten in place. DOCX replaces ``w:t`` run text. Uncompressed
PDF literals that match a rule are masked with the same number of ``X`` bytes
so the cross-reference table stays valid. Compressed PDF streams are left
unchanged and reported.
"""

from __future__ import annotations

import re

_RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "email",
        re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I),
        "[REDACTED:email]",
    ),
    (
        "phone_cn",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "[REDACTED:phone]",
    ),
    (
        "id_cn",
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        "[REDACTED:id]",
    ),
    (
        "card",
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
        "[REDACTED:card]",
    ),
]


def redact_text(text: str, extra: list[tuple[str, str]] | None = None) -> tuple[str, int]:
    """Return ``(redacted, replacement_count)``. ``extra`` is ``(pattern, replacement)``."""
    count = 0
    out = text or ""
    for _name, pattern, repl in _RULES:
        out, n = pattern.subn(repl, out)
        count += n
    for pattern, repl in extra or []:
        if not pattern:
            continue
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"无效脱敏正则: {exc}") from exc
        out, n = compiled.subn(repl or "[REDACTED]", out)
        count += n
    return out, count


def _mask_rules(text: str) -> tuple[str, int]:
    """Replace each match with the same number of X characters."""
    count = 0

    def _mask(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "X" * len(match.group(0))

    out = text or ""
    for _name, pattern, _repl in _RULES:
        out = pattern.sub(_mask, out)
    return out, count


def redact_docx_bytes(data: bytes, extra: list[tuple[str, str]] | None = None) -> tuple[bytes, int]:
    """Replace secrets inside DOCX ``w:t`` runs. Length changes are safe in OOXML."""
    import html
    import zipfile
    from io import BytesIO

    if not data.startswith(b"PK"):
        raise ValueError("不是 DOCX 压缩包")
    pattern = re.compile(r"(<w:t(?:\s[^>]*)?>)(.*?)(</w:t>)", re.S)
    count = 0

    def _repl(match: re.Match[str]) -> str:
        nonlocal count
        raw = html.unescape(match.group(2))
        new, n = redact_text(raw, extra)
        count += n
        return match.group(1) + html.escape(new) + match.group(3)

    src = zipfile.ZipFile(BytesIO(data))
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as dst:
        for info in src.infolist():
            blob = src.read(info.filename)
            if info.filename.startswith("word/") and info.filename.endswith(".xml"):
                text = blob.decode("utf-8", errors="replace")
                text = pattern.sub(_repl, text)
                blob = text.encode("utf-8")
            dst.writestr(info, blob)
    src.close()
    return buf.getvalue(), count


_PDF_LITERAL = re.compile(rb"\(((?:\\.|[^\\)])*)\)")


def redact_pdf_bytes(data: bytes) -> tuple[bytes, int, bool]:
    """Mask secrets in uncompressed PDF literals. Returns ``(pdf, count, compressed_left)``."""
    if not data.startswith(b"%PDF"):
        raise ValueError("不是 PDF")
    compressed_left = b"FlateDecode" in data
    count = 0

    def _repl(match: re.Match[bytes]) -> bytes:
        nonlocal count
        try:
            text = match.group(1).decode("latin-1")
        except Exception:
            return match.group(0)
        text = text.replace(r"\(", "(").replace(r"\)", ")").replace(r"\\", "\\")
        masked, n = _mask_rules(text)
        if not n:
            return match.group(0)
        count += n
        escaped = masked.replace("\\", "\\\\").replace("(", r"\(").replace(")", r"\)")
        return b"(" + escaped.encode("latin-1", errors="replace") + b")"

    return _PDF_LITERAL.sub(_repl, data), count, compressed_left


def watermark_text(text: str, mark: str) -> str:
    """Prepend and append a visible watermark line. Empty mark is a no-op."""
    label = " ".join((mark or "").split())
    if not label:
        return text or ""
    # Keep the mark on its own line so Markdown/plain viewers show it.
    banner = f"> {label}"
    body = (text or "").rstrip()
    return f"{banner}\n\n{body}\n\n{banner}\n"
