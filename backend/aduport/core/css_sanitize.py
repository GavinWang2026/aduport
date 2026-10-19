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


_GENERIC_FAMILIES = frozenset(
    {
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-sans-serif",
        "ui-serif",
        "ui-monospace",
    }
)


def compact_font_family(value: str, *, max_named: int = 1) -> str:
    """Keep the first named face (+ one generic) so WeasyPrint embeds fewer files.

    A long CJK fallback chain (PingFang + Hiragino + Nanum + …) floors a
    short Chinese PDF near 300KB. Callers that want the full stack can pass
    the unsanitized list when compact mode is off.
    """
    parts = [p.strip().strip("'\"") for p in (value or "").split(",") if p.strip()]
    if not parts:
        return value
    named: list[str] = []
    generic = ""
    for part in parts:
        key = part.casefold()
        if key in _GENERIC_FAMILIES:
            if not generic:
                generic = part
            continue
        if len(named) < max(1, int(max_named)):
            named.append(part)
    if not named:
        return value
    if generic:
        named.append(generic)
    return ", ".join(named)


# Faces WeasyPrint typically embeds whole (TTC / CFF full CJK). Short docs
# stay ~150–170KB. Songti / STSong subset to ~10–20KB for the same glyphs.
_HEAVY_CJK_FACES = frozenset(
    {
        "pingfang sc",
        "pingfang tc",
        "pingfang hk",
        "hiragino sans gb",
        "hiragino sans",
        "noto sans cjk sc",
        "noto sans cjk tc",
        "noto sans cjk jp",
        "noto sans cjk kr",
        "source han sans sc",
        "source han sans",
        "heiti sc",
        "stheiti",
        "nanum gothic",
    }
)

# Prefer faces that subset cleanly on common desktops (macOS Songti / STSong).
_SUBSETTABLE_CJK_FACES = (
    "Songti SC",
    "STSong",
    "Songti SC Light",
    "STSongti-SC-Regular",
)


def prefer_subsettable_cjk(font_family: str) -> str:
    """When the lead face embeds whole CJK files, prefer a subsettable face.

    Does not probe the OS font catalog — puts Songti/STSong first so WeasyPrint
    can resolve them when installed, else falls through the rest of the stack.
    """
    parts = [p.strip() for p in (font_family or "").split(",") if p.strip()]
    if not parts:
        return font_family
    first = parts[0].strip("'\"")
    key = first.casefold()
    if key in {face.casefold() for face in _SUBSETTABLE_CJK_FACES}:
        return font_family
    if key not in _HEAVY_CJK_FACES and not any(
        key.startswith(heavy) for heavy in _HEAVY_CJK_FACES
    ):
        return font_family
    # Keep original faces after the preferred subsettable ones as fallbacks.
    preferred = list(_SUBSETTABLE_CJK_FACES[:2])
    rest = [p for p in parts if p.strip("'\"").casefold() not in {f.casefold() for f in preferred}]
    return ", ".join(preferred + rest)


def sanitize_custom_css(value: str | None, *, max_len: int | None = None) -> str:
    if not value:
        return ""
    limit = _MAX_CSS_LEN if max_len is None else max(0, int(max_len))
    css = value[:limit]
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
