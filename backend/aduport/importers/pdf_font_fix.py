"""Fix PEP-style PDF math fonts that map Latin letters to CJK codepoints.

Many Chinese textbooks embed italic Latin (A,B,x,…) with a custom ToUnicode
map pointing at rare CJK ideographs. Extracted text then shows 犃/犅/狓 instead
of A/B/x. This module rewrites those codepoints back to ASCII/Latin.
"""

from __future__ import annotations

import re
import unicodedata

# Empirically derived from 义务教育教科书·数学 (人教版) text layers.
# Uppercase / lowercase italic math labels → ASCII.
_PEP_MATH_FONT_MAP: dict[str, str] = {
    # Uppercase
    "犃": "A",
    "犅": "B",
    "犆": "C",
    "犇": "D",
    "犈": "E",
    "犉": "F",
    "犌": "G",
    "犐": "I",
    "犓": "K",
    "犕": "M",
    "犖": "N",
    "犗": "O",
    "犘": "P",
    "犙": "Q",
    "犚": "R",
    "犛": "S",
    "犝": "U",
    "犞": "V",
    # Lowercase (incl. style variants)
    "犪": "a",
    "狆": "a",
    "犫": "b",
    "犮": "c",
    "犱": "d",
    "狇": "e",
    "犺": "h",
    "犾": "l",
    "犿": "m",
    "狀": "n",
    "狉": "r",
    "狊": "s",
    "狋": "t",
    "狏": "v",
    "狑": "v",
    "狓": "x",
    "狔": "y",
}

# Private-use hyphens / separators seen in figure numbers (图２０．１６)
_PUA_REPLACEMENTS: dict[str, str] = {
    "\ue011": "-",
    "\ue5d0": "-",
}

_GARBAGE_LINE = re.compile(r'^[!"#$%&\'()\*\+,./:;<=>?@\[\\\]^_`{|}~—\-]+$')


def _translate_math_font(text: str) -> str:
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        if ch in _PEP_MATH_FONT_MAP:
            out.append(_PEP_MATH_FONT_MAP[ch])
        elif ch in _PUA_REPLACEMENTS:
            out.append(_PUA_REPLACEMENTS[ch])
        else:
            out.append(ch)
    return "".join(out)


def _looks_like_pep_math_font(text: str) -> bool:
    """Heuristic: textbook math fonts emit many remappable CJK codepoints."""
    if not text:
        return False
    hits = sum(1 for ch in text if ch in _PEP_MATH_FONT_MAP)
    if hits >= 3:
        return True
    # Dense short labels like 「犃犅犆」
    return hits >= 2 and hits / max(1, len(text.strip())) >= 0.15


def normalize_pdf_text(text: str) -> str:
    """
    Normalize extracted PDF text for Chinese textbooks:
    - math-font CJK → Latin (settings flag or dense-PEP heuristic)
    - fullwidth alphanumerics → ASCII (NFKC)
    - PUA separators → '-'
    - drop decorative garbage-only lines / control chars
    """
    if not text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip C0 controls except tab/newline
    text = "".join(ch for ch in text if ch in "\t\n" or ord(ch) >= 32)
    # PEP textbook remap is destructive on normal CJK (犇→D). Default off unless
    # explicitly enabled or the page looks like math-font garbage.
    from aduport.config import settings

    if getattr(settings, "pdf_pep_font_fix", False) or _looks_like_pep_math_font(text):
        text = _translate_math_font(text)
    else:
        text = "".join(_PUA_REPLACEMENTS.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKC", text)
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if _markdown_structure_line(stripped):
            lines.append(stripped)
            continue
        if stripped and _GARBAGE_LINE.match(stripped):
            continue
        stripped = re.sub(r'^[!"#$%&\'()]{2,}\s*', "", stripped)
        lines.append(stripped if stripped != line.strip() else line.rstrip())
    return "\n".join(lines)


def _markdown_structure_line(stripped: str) -> bool:
    """Keep fences, rules, and ATX headings that the garbage filter would eat."""
    if not stripped:
        return False
    if stripped.startswith("```") or stripped in ("---", "***", "___"):
        return True
    if re.match(r"^#{1,6}\s+\S", stripped):
        return True
    if re.match(r"^>\s?", stripped):
        return True
    if re.match(r"^[-*+]\s+\S", stripped) or re.match(r"^\d+\.\s+\S", stripped):
        return True
    if stripped.startswith("|") or " | " in stripped:
        return True
    if stripped.startswith("[^"):
        return True
    return False
