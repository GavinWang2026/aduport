"""Sanitize user-provided CSS for PDF export (block SSRF vectors)."""

from __future__ import annotations

import re

_MAX_CSS_LEN = 8_000
_MAX_FONT_LEN = 256

# Strip remote resource loading and dangerous at-rules
_FORBIDDEN = re.compile(
    r"""
    @import\b
    | @font-face\b
    | url\s*\(
    | expression\s*\(
    | -moz-binding
    | behavior\s*:
    | javascript\s*:
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SAFE_FONT = re.compile(r"^[A-Za-z0-9\s,\.\-_'\"\u4e00-\u9fff]+$")


def sanitize_font_family(value: str | None, default: str) -> str:
    if not value:
        return default
    cleaned = value.strip()[:_MAX_FONT_LEN]
    if not _SAFE_FONT.match(cleaned):
        return default
    return cleaned


def sanitize_custom_css(value: str | None) -> str:
    if not value:
        return ""
    css = value[:_MAX_CSS_LEN]
    # Drop lines / declarations containing forbidden constructs
    parts: list[str] = []
    for chunk in re.split(r"([{};])", css):
        if _FORBIDDEN.search(chunk):
            continue
        parts.append(chunk)
    cleaned = "".join(parts)
    # Final pass: remove any residual forbidden matches
    cleaned = _FORBIDDEN.sub("/*blocked*/", cleaned)
    return cleaned.strip()
