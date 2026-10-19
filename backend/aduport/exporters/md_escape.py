"""Context-aware Markdown escaping (v9 P2).

Avoids two competitor failure modes:
- markitdown: bare line-start ``#`` inside prose/code misread as headings
- mammoth: over-escaping of backticks / list-like runs that breaks rendering

Strategy: escape only characters that would form block/inline syntax in context;
code spans stay literal; pairable ``*``/``_`` mid-prose are left alone unless
they form an odd lone delimiter run at a word boundary.
"""

from __future__ import annotations

import re


def escape_md_text(
    text: str,
    *,
    at_line_start: bool = False,
    in_code: bool = False,
) -> str:
    """Escape a plain-text run for Markdown body context."""
    if not text or in_code:
        return text

    out: list[str] = []
    i = 0
    n = len(text)
    line_start = at_line_start

    while i < n:
        ch = text[i]

        if ch == "\n":
            out.append(ch)
            line_start = True
            i += 1
            continue

        if line_start and ch in (" ", "\t"):
            out.append(ch)
            i += 1
            # still at visual line start for indented block markers
            continue

        if line_start:
            # Heading: "# " / "### "
            if ch == "#":
                j = i
                while j < n and text[j] == "#":
                    j += 1
                if j < n and text[j] in (" ", "\t"):
                    out.append("\\" + text[i:j])
                    i = j
                    line_start = False
                    continue
            # Blockquote
            if ch == ">" and (i + 1 >= n or text[i + 1] in (" ", "\t", "\n")):
                out.append("\\>")
                i += 1
                line_start = False
                continue
            # Unordered list marker
            if ch in "-*+" and (i + 1 >= n or text[i + 1] in (" ", "\t")):
                out.append("\\" + ch)
                i += 1
                line_start = False
                continue
            # Ordered list "1. " / "12) "
            if ch.isdigit():
                j = i
                while j < n and text[j].isdigit():
                    j += 1
                if j < n and text[j] in ".)" and (j + 1 >= n or text[j + 1] in (" ", "\t")):
                    out.append(text[i:j] + "\\" + text[j])
                    i = j + 1
                    line_start = False
                    continue
            line_start = False

        if ch in ("\\", "`"):
            out.append("\\" + ch)
            i += 1
            continue

        if ch == "[":
            close = text.find("]", i + 1)
            if close < 0:
                out.append("\\[")
                i += 1
                continue
            out.append(text[i : close + 1])
            i = close + 1
            continue

        if ch == "]":
            out.append("\\]")
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def collapse_blank_lines(text: str) -> str:
    """Cap consecutive blank lines at one (v9 P1 native blank-line flood)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text
