"""Lightweight PDF text extraction via pypdfium2 (no Hugging Face models).

PDFium (libpdfium.dylib) is **not thread-safe**. Concurrent opens/extracts on
macOS routinely abort the process with EXC_BREAKPOINT / SIGTRAP. All native
access is serialized on `_PDFIUM_LOCK`, and page text is always extracted
sequentially from a single PdfDocument.
"""

from __future__ import annotations

from aduport.importers.import_guard import swallow_import_error
import logging
import re
import threading
from io import BytesIO

from aduport.config import settings
from aduport.core.document_model import (
    DocumentModel,
    HorizontalRule,
    Image,
    InlineMark,
    InlineMarkType,
    Paragraph,
    RichText,
)
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError
from aduport.importers.pdf_font_fix import normalize_pdf_text

_log = logging.getLogger(__name__)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PDFIUM_LOCK = threading.RLock()


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if _CJK_RE.match(c)) / max(len(text), 1)


_MD_LIST_ITEM = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_IMAGE_PLACEHOLDER = re.compile(r"^\[Image:.*\](?: \(\d+ bytes\))?$")


def _strip_image_markers(page_texts: list[str]) -> tuple[list[str], list[Image]]:
    """Pull ``ADUPORT-IMG`` blocks pdf_simple writes, and drop them from the text."""
    images: list[Image] = []
    cleaned: list[str] = []
    for text in page_texts:
        lines = (text or "").splitlines()
        kept: list[str] = []
        index = 0
        while index < len(lines):
            if lines[index].strip() != "ADUPORT-IMG":
                kept.append(lines[index])
                index += 1
                continue
            if index + 2 >= len(lines):
                index += 1
                continue
            mime = lines[index + 1].strip() or "image/png"
            alt = lines[index + 2].strip() or "image"
            index += 3
            chunks: list[str] = []
            while index < len(lines) and lines[index].strip() != "ADUPORT-IMG-END":
                chunks.append(lines[index].strip())
                index += 1
            if index < len(lines) and lines[index].strip() == "ADUPORT-IMG-END":
                index += 1
            try:
                import base64

                data = base64.b64decode("".join(chunks))
            except Exception:
                data = b""
            if data:
                images.append(Image(data=data, alt_text=alt, mime_type=mime))
        cleaned.append("\n".join(kept))
    return cleaned, images


def _is_image_placeholder(node: object) -> bool:
    if type(node).__name__ != "Paragraph":
        return False
    plain = getattr(node, "plain_text", None)
    text = plain() if callable(plain) else ""
    return bool(_IMAGE_PLACEHOLDER.match(str(text or "").strip()))


def _images_from_xobjects(source_bytes: bytes) -> list[Image]:
    """Images WeasyPrint embedded. pdf_simple has no raster, so this stays empty."""
    try:
        from pypdf import PdfReader
    except Exception:
        return []
    images: list[Image] = []
    seen: set[bytes] = set()
    try:
        reader = PdfReader(BytesIO(source_bytes))
    except Exception:
        return []
    for page in reader.pages:
        try:
            found = list(page.images)
        except Exception:
            continue
        for image in found:
            data = getattr(image, "data", b"") or b""
            if not data or data in seen:
                continue
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif data[:2] == b"\xff\xd8":
                mime = "image/jpeg"
            else:
                continue
            seen.add(data)
            name = str(getattr(image, "name", "") or "image")
            images.append(Image(data=data, alt_text=name, mime_type=mime))
    return images


def _join_wrapped_lines(lines: list[str]) -> str:
    """Join soft-wrapped lines; avoid inserting spaces between CJK characters.

    A list item and the following non-item stay on separate lines. PDF text
    extraction often omits the blank line, and a space-join turns
    ``- bullet`` + ``Closing paragraph.`` into one line.
    """
    if not lines:
        return ""
    sample = "".join(lines)
    if _cjk_ratio(sample) >= 0.25:
        out: list[str] = []
        for i, ln in enumerate(lines):
            if i == 0:
                out.append(ln)
                continue
            prev = out[-1].split("\n")[-1]
            if re.match(r"^[\d一二三四五六七八九十]+[、.．、]\s*", ln) or ln.startswith(
                ("•", "·", "-", "（", "(")
            ):
                out.append("\n" + ln)
            elif _MD_LIST_ITEM.match(prev) and not _MD_LIST_ITEM.match(ln):
                out.append("\n\n" + ln)
            elif prev.endswith(("。", "！", "？", "；", ".", "!", "?", ":")):
                out.append("\n" + ln)
            else:
                out.append(ln)
        return "".join(out)
    parts: list[str] = [lines[0]]
    for ln in lines[1:]:
        prev = parts[-1].split("\n")[-1]
        if _MD_LIST_ITEM.match(prev) and not _MD_LIST_ITEM.match(ln):
            parts.append("\n\n" + ln)
        else:
            parts.append(" " + ln)
    return "".join(parts)


def _separate_markdown_blocks(text: str) -> str:
    """Blank line before a table or fence so a blockquote cannot swallow it.

    pdfium often omits the empty text line we emit between blocks, and CommonMark
    then treats the next lines as a lazy continuation of the quote.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []

    def _tableish(s: str) -> bool:
        return " | " in s or s.startswith("|")

    for ln in lines:
        s = ln.strip()
        prev = out[-1].strip() if out else ""
        if s and prev:
            if prev.startswith(">") and not s.startswith(">"):
                out.append("")
            elif _tableish(s) and not _tableish(prev):
                out.append("")
            elif s.startswith("```") and not prev.startswith("```"):
                out.append("")
            elif _LISTISH_LINE.match(prev) and not _LISTISH_LINE.match(s) and not s.startswith(
                (">", "|", "```")
            ):
                # CommonMark would treat the next block as a lazy continuation
                # of the list item: "- ECHO 二 FOXTROT 引用行 ...".
                out.append("")
        out.append(ln)
    return "\n".join(out)


_LISTISH_LINE = re.compile(r"^(?:[-*+]\s+|\d{1,3}[.)]\s+|[•·▪◦‣]\s*|\[(?:x| )\]\s+)")


_CUE_LEAD = re.compile(
    r"^(?:#{1,6}\s+|>\s*|[-*+•·▪]\s+|\d+[.)]\s+|\[[ xX]\]\s+|\[\^[^\]]+\]:?\s*)+"
)


def _noise_line(line: str) -> bool:
    """Page numbers and empty bullets that WeasyPrint leaves in the text layer."""
    stripped = line.strip()
    if not stripped:
        return True
    return bool(re.fullmatch(r"[\d.•◦▪‣\-–—]+", stripped))


def _page_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.replace("\r\n", "\n").split("\n")
        if not _noise_line(line)
    ]


def _attached_cue_text(source_bytes: bytes) -> str:
    """Cue markdown embedded as a file, so it does not occupy a visible page."""
    from aduport.exporters.html_render import PDF_CUE_ATTACHMENT, PDF_CUE_MARK

    try:
        from pypdf import PdfReader

        blobs = PdfReader(BytesIO(source_bytes)).attachments.get(PDF_CUE_ATTACHMENT) or []
    except Exception:
        return ""
    if isinstance(blobs, (bytes, str)):
        blobs = [blobs]
    lines: list[str] = []
    for blob in blobs:
        text = blob if isinstance(blob, str) else bytes(blob).decode("utf-8", "replace")
        if PDF_CUE_MARK not in text:
            continue
        for line in text.replace("\r\n", "\n").split("\n"):
            cleaned = line.replace(PDF_CUE_MARK, "").strip()
            if cleaned and not _noise_line(cleaned):
                lines.append(cleaned)
    return "\n".join(lines)


def _partition_cue_pages(page_texts: list[str]) -> tuple[list[str], str]:
    """Split our Markdown cue text off the visual pages, even mid-page."""
    from aduport.exporters.html_render import PDF_CUE_MARK

    visual: list[str] = []
    cue_lines: list[str] = []
    seen = False
    for text in page_texts:
        if not text:
            if not seen:
                visual.append(text or "")
            continue
        if not seen and PDF_CUE_MARK not in text:
            visual.append(text)
            continue
        seen = True
        before, sep, after = text.partition(PDF_CUE_MARK)
        if sep and before.strip():
            visual.append(before)
        rest = after if sep else text
        for line in rest.replace("\r\n", "\n").split("\n"):
            cleaned = line.replace(PDF_CUE_MARK, "").strip()
            if cleaned and not _noise_line(cleaned):
                cue_lines.append(cleaned)
    return visual, "\n".join(cue_lines)


def _cue_nodes_from_text(text: str) -> list:
    restored = _markdown_page_nodes(text)
    if restored:
        return restored
    from aduport.importers.markdown_rehydrate import promote_inline

    return [
        Paragraph(text=promote_inline(para))
        for para in _page_to_paragraphs(text)
        if para.strip()
    ]


def _node_plain(node: object) -> str:
    name = type(node).__name__
    if name == "Footnote":
        return str(getattr(node, "text", "") or "")
    if name == "Blockquote":
        parts: list[str] = []
        for para in getattr(node, "paragraphs", []) or []:
            if isinstance(para, str):
                parts.append(para)
            elif isinstance(getattr(para, "text", None), str):
                parts.append(para.text)
            elif hasattr(para, "plain_text"):
                parts.append(str(para.plain_text() or ""))
            else:
                parts.append(str(para))
        return " ".join(parts)
    if name == "CodeBlock":
        return str(getattr(node, "content", "") or "")
    plain = getattr(node, "plain_text", None)
    if callable(plain):
        return str(plain() or "")
    return ""


def _loose_key(node: object) -> str:
    raw = _node_plain(node).replace("**", "").replace("~~", "").replace("`", "")
    raw = raw.replace("*", "")
    previous = None
    while previous != raw:
        previous = raw
        raw = _CUE_LEAD.sub("", raw.strip())
    return re.sub(r"\s+", " ", raw).strip().casefold()


def _covered_by_cues(key: str, cue_keys: list[str]) -> bool:
    """True when a glued visual line is only the cue phrases joined together."""
    if not key:
        return False
    leftover = key
    hit = False
    for cue_key in sorted((item for item in cue_keys if len(item) >= 4), key=len, reverse=True):
        if cue_key and cue_key in leftover:
            leftover = leftover.replace(cue_key, " ")
            hit = True
    leftover = re.sub(r"[\s.,;:|-]+", " ", leftover).strip()
    return hit and len(leftover) < 8


def _table_row_keys(node: object) -> list[str]:
    """Flattened visual lines WeasyPrint emits for one table row, e.g. ``a b``."""
    if type(node).__name__ != "Table":
        return []
    rows = list(getattr(node, "rows", None) or [])
    if not rows:
        grid = getattr(node, "cells", None) or []
        rows = [[getattr(cell, "text", "") for cell in row] for row in grid]
    keys: list[str] = []
    for row in rows:
        parts = [re.sub(r"\s+", " ", str(cell)).strip() for cell in row]
        parts = [part for part in parts if part and set(part) != {"-"}]
        if len(parts) >= 2:
            keys.append(" ".join(parts).casefold())
    return keys


def _fold_cue_nodes(visual: list, cues: list) -> list:
    """Replace visual paragraphs with the cue node that carries the same words."""
    if not cues:
        return visual
    cue_keys = [_loose_key(node) for node in cues]
    row_keys = {key for node in cues for key in _table_row_keys(node)}
    used = [False] * len(cues)
    has_footnote = any(type(node).__name__ == "Footnote" for node in cues)
    folded: list = []
    for node in visual:
        if has_footnote and _loose_key(node) == "footnotes":
            continue
        key = _loose_key(node)
        hit = None
        if key:
            for index, cue_key in enumerate(cue_keys):
                if used[index] or not cue_key:
                    continue
                if cue_key == key:
                    hit = index
                    break
        if hit is not None:
            folded.append(cues[hit])
            used[hit] = True
            continue
        if key and (key in row_keys or _covered_by_cues(key, cue_keys)):
            continue
        folded.append(node)
    for index, node in enumerate(cues):
        if not used[index]:
            folded.append(node)
    return folded


def _markdown_page_nodes(text: str) -> list | None:
    """pdf_simple writes a Markdown text layer. Restore blocks instead of one paragraph."""
    from aduport.importers.markdown_rehydrate import looks_structural, parse_markdown_nodes

    if not looks_structural(text):
        return None
    nodes = parse_markdown_nodes(_separate_markdown_blocks(text))
    return nodes or None


# Glued markers are normal in third-party text layers: ``1.First``, ``a)Alpha``,
# ``i.Roman``, ``•Bullet``. A space is still accepted. A 4-digit year
# (``2024. Annual``) and a lettered sentence (``Note. This``) are not lists.
_PDF_LIST_LINE = re.compile(
    r"^(?:"
    r"(?P<bullet>[•·▪◦‣])\s*"
    r"|(?P<dash>[-*+])\s+"
    r"|(?P<num>\d{1,3}[.)])\s*"
    r"|(?P<alpha>[A-Za-z]\))\s*"
    r"|(?P<roman>(?i:i{1,3}|iv|vi{0,3}|ix|x{1,3})\.)\s*"
    r")"
    r"(?P<body>\S.*?)\s*$"
)
_INVISIBLE_CHARS = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def _chars_share_line(
    line: list[tuple[float, float, float, float, str]],
    char: tuple[float, float, float, float, str],
) -> bool:
    """Keep descenders on the line. Bottoms alone split a 22pt ``y`` off ``Quarterly``."""
    line_bottom = min(item[1] for item in line)
    line_top = max(item[3] for item in line)
    # pdfium gives spaces a zero-height box on the baseline. Keep them on the line.
    if char[4] == " " or char[3] <= char[1]:
        mid = (char[1] + char[3]) / 2.0
        return line_bottom - 1.0 <= mid <= line_top + 1.0
    overlap = min(line_top, char[3]) - max(line_bottom, char[1])
    if overlap <= 0:
        return False
    height = max(1.0, min(line_top - line_bottom, char[3] - char[1]))
    return overlap >= height * 0.35


def _group_chars_into_lines(
    chars: list[tuple[float, float, float, float, str]],
) -> list[list[tuple[float, float, float, float, str]]]:
    lines: list[list[tuple[float, float, float, float, str]]] = []
    for char in chars:
        placed = False
        for line in lines:
            if _chars_share_line(line, char):
                line.append(char)
                placed = True
                break
        if not placed:
            lines.append([char])
    return lines


def _emphasis_bits(font_name: str, flags: int) -> int:
    """1 = bold, 2 = italic. Weight is often 400 even for a ``-Bold`` face."""
    low = (font_name or "").casefold()
    bold = any(
        token in low
        for token in ("-bold", " bold", ",bold", "semibold", "demibold", "-black", "-heavy")
    ) or low.endswith("bold")
    italic = bool(flags & 64) or "italic" in low or "oblique" in low
    return (1 if bold else 0) | (2 if italic else 0)


def _strike_rules(page) -> list[tuple[float, float, float, float]]:
    """Thin horizontal paths. A line through the middle of glyphs is strikethrough."""
    rules: list[tuple[float, float, float, float]] = []
    try:
        objects = page.get_objects()
    except Exception:
        return rules
    for obj in objects:
        if getattr(obj, "type", None) != 2:
            continue
        try:
            left, bottom, right, top = obj.get_bounds()
        except Exception:
            continue
        if top - bottom > 2.5 or right - left < 6:
            continue
        rules.append((float(left), float(bottom), float(right), float(top)))
    return rules


def _rule_strikes_char(
    box: tuple[float, float, float, float],
    rules: list[tuple[float, float, float, float]],
) -> bool:
    left, bottom, right, top = box
    height = top - bottom
    width = right - left
    if height < 1 or width <= 0:
        return False
    # Underlines sit on the baseline. A strike crosses the middle of the ink.
    mid_lo = bottom + height * 0.35
    mid_hi = bottom + height * 0.72
    for rule_left, rule_bottom, rule_right, rule_top in rules:
        center = (rule_bottom + rule_top) / 2.0
        if not (mid_lo <= center <= mid_hi):
            continue
        overlap = min(rule_right, right) - max(rule_left, left)
        if overlap >= width * 0.5:
            return True
    return False


def _marks_from_styles(text: str, styles: list[int]) -> list[dict[str, object]]:
    named = (("bold", 1), ("italic", 2), ("strike", 4))
    marks: list[dict[str, object]] = []
    for name, bit in named:
        index = 0
        while index < len(styles):
            if not styles[index] & bit:
                index += 1
                continue
            end = index + 1
            while end < len(styles) and styles[end] & bit:
                end += 1
            start = index
            stop = end
            while start < stop and text[start].isspace():
                start += 1
            while stop > start and text[stop - 1].isspace():
                stop -= 1
            if start < stop:
                marks.append({"type": name, "start": start, "end": stop})
            index = end
    return marks


def _shift_style_marks(
    marks: list | None, cut: int, length: int
) -> list[dict[str, object]]:
    shifted: list[dict[str, object]] = []
    for mark in marks or []:
        if not isinstance(mark, dict):
            continue
        start = int(mark.get("start") or 0) - cut
        end = int(mark.get("end") or 0) - cut
        if end <= 0 or start >= length:
            continue
        shifted.append(
            {"type": mark.get("type"), "start": max(0, start), "end": min(length, end)}
        )
    return shifted


def _rich_with_font_marks(text: str, marks: list | None):
    from aduport.core.document_model import InlineMark, InlineMarkType
    from aduport.importers.markdown_rehydrate import promote_inline

    rich = promote_inline(text)
    if not marks:
        return rich
    type_of = {
        "bold": InlineMarkType.BOLD,
        "italic": InlineMarkType.ITALIC,
        "strike": InlineMarkType.STRIKETHROUGH,
    }
    extra: list[InlineMark] = []
    limit = len(rich.text)
    seen = {(mark.type, mark.start, mark.end) for mark in rich.marks}
    for mark in marks:
        if not isinstance(mark, dict):
            continue
        kind = type_of.get(str(mark.get("type") or ""))
        if kind is None:
            continue
        start = max(0, int(mark.get("start") or 0))
        end = min(limit, int(mark.get("end") or 0))
        if start >= end or (kind, start, end) in seen:
            continue
        extra.append(InlineMark(type=kind, start=start, end=end))
        seen.add((kind, start, end))
    if not extra:
        return rich
    return rich.model_copy(update={"marks": list(rich.marks) + extra})


def _layout_block_from_line(
    line: list[tuple[float, float, float, float, str]],
    *,
    page_idx: int,
    page_h: float,
) -> dict[str, object]:
    """One visual line, plus column splits when a gap is wider than a word space."""
    ordered = sorted(line, key=lambda item: item[0])
    left = min(item[0] for item in ordered)
    bottom = min(item[1] for item in ordered)
    right = max(item[2] for item in ordered)
    top = max(item[3] for item in ordered)
    widths = sorted(item[2] - item[0] for item in ordered if item[2] > item[0])
    median_width = widths[len(widths) // 2] if widths else 6.0
    gap_at = max(36.0, median_width * 8.0)
    columns: list[str] = []
    cells: list[dict[str, object]] = []
    buf: list[str] = []
    start_left: float | None = None
    prev_right: float | None = None
    for item in ordered:
        if prev_right is not None and item[0] - prev_right > gap_at and buf:
            piece = "".join(buf).strip()
            if piece and start_left is not None:
                columns.append(piece)
                cells.append({"text": piece, "left": round(start_left, 2)})
            buf = []
            start_left = None
        if start_left is None:
            start_left = item[0]
        buf.append(item[4])
        prev_right = item[2]
    if buf and start_left is not None:
        piece = "".join(buf).strip()
        if piece:
            columns.append(piece)
            cells.append({"text": piece, "left": round(start_left, 2)})
    from statistics import median

    raw_chars: list[str] = []
    raw_styles: list[int] = []
    for item in ordered:
        piece = item[4]
        style = int(item[6]) if len(item) > 6 else 0
        for ch in piece:
            if _INVISIBLE_CHARS.match(ch):
                continue
            raw_chars.append(ch)
            raw_styles.append(style)
    raw = "".join(raw_chars)
    lead = len(raw) - len(raw.lstrip())
    kept = raw.rstrip()
    text = kept[lead:]
    styles = raw_styles[lead : lead + len(text)]
    sizes = [
        float(item[5])
        for item in ordered
        if len(item) > 5 and item[4].strip() and float(item[5]) > 0
    ]
    font_size = median(sizes) if sizes else 0.0
    return {
        "page": page_idx + 1,
        "left": round(left, 2),
        "bottom": round(bottom, 2),
        "right": round(right, 2),
        "top": round(top, 2),
        "y_from_top": round(page_h - top, 2),
        "height": round(max(0.0, top - bottom), 2),
        "font_size": round(font_size, 2),
        "text": text,
        "marks": _marks_from_styles(text, styles),
        "columns": columns if len(columns) >= 2 else [],
        "cells": cells,
    }


def _positioned_cells(block: dict[str, object]) -> list[dict[str, object]]:
    raw = block.get("cells")
    if not isinstance(raw, list):
        return []
    cells: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict) or item.get("left") is None:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        cells.append({"text": text, "left": float(item["left"])})
    return cells


def _mark_aligned_column_runs(
    usable: list[dict[str, object]],
    table_at: list[bool],
) -> None:
    """Join consecutive rows whose right edge shares a column, even if a cell is empty.

    Equal-width runs are already marked. A PDF table often leaves the first
    cell of a later row blank, so the row has fewer pieces than the header.
    """
    index = 0
    while index < len(usable):
        if table_at[index]:
            index += 1
            continue
        cells = _positioned_cells(usable[index])
        if len(cells) < 2 or len(str(usable[index].get("text") or "")) > 80:
            index += 1
            continue
        anchors = [float(cell["left"]) for cell in cells]
        page = int(usable[index].get("page") or 0)
        end = index + 1
        while end < len(usable) and not table_at[end]:
            if int(usable[end].get("page") or 0) != page:
                break
            if len(str(usable[end].get("text") or "")) > 80:
                break
            row_cells = _positioned_cells(usable[end])
            if len(row_cells) < 2:
                break
            right = max(float(cell["left"]) for cell in row_cells)
            if not any(abs(right - anchor) <= 16.0 for anchor in anchors):
                break
            for cell in row_cells:
                left = float(cell["left"])
                if not any(abs(left - anchor) <= 16.0 for anchor in anchors):
                    anchors.append(left)
            end += 1
        if end - index >= 2:
            for slot in range(index, end):
                table_at[slot] = True
        index = end if end > index else index + 1


def _rows_from_column_anchors(blocks: list[dict[str, object]]) -> list[list[str]]:
    anchors: list[float] = []
    positioned = [_positioned_cells(block) for block in blocks]
    for cells in positioned:
        for cell in cells:
            left = float(cell["left"])
            if not any(abs(left - anchor) <= 16.0 for anchor in anchors):
                anchors.append(left)
    anchors.sort()
    merged: list[float] = []
    for left in anchors:
        if merged and left - merged[-1] <= 16.0:
            continue
        merged.append(left)
    if len(merged) < 2:
        return []
    rows: list[list[str]] = []
    for cells in positioned:
        row = [""] * len(merged)
        for cell in cells:
            left = float(cell["left"])
            slot = min(range(len(merged)), key=lambda i: abs(merged[i] - left))
            if abs(merged[slot] - left) > 16.0:
                continue
            text = str(cell["text"])
            row[slot] = f"{row[slot]} {text}".strip() if row[slot] else text
        rows.append(row)
    return rows


def nodes_from_layout_blocks(blocks: list[dict[str, object]]) -> list:
    """Promote third-party text-layer lines using font height, indent, and column gaps.

    A same-size digital page stays paragraphs and list items. A taller short line
    becomes a heading, a run of aligned columns becomes a table, and an indented
    or quotation-marked line becomes a blockquote. ``structure_fidelity`` stays
    ``below_text``; this does not recover nested lists or full layout.
    """
    from statistics import median

    from aduport.core.document_model import Blockquote, Heading, HorizontalRule

    usable = [
        block
        for block in blocks
        if _visible_layout_text(str(block.get("text") or ""))
    ]
    if not usable:
        return []
    heights: list[float] = []
    prose_heights: list[float] = []
    lefts: list[float] = []
    for block in usable:
        height = _block_type_size(block)
        text = _visible_layout_text(str(block.get("text") or ""))
        if height > 0:
            heights.append(height)
            if len(text) >= 40:
                prose_heights.append(height)
        if "left" in block:
            lefts.append(float(block["left"]))
    # Body size comes from prose lines. Short table and list crumbs are smaller
    # and would pull the median down, so a real heading never clears 1.35×.
    # Three prose lines are enough to trust that median; otherwise keep every line.
    if len(prose_heights) >= 3:
        body_height = median(prose_heights)
    else:
        body_height = median(heights) if heights else 12.0
    body_left = median(lefts) if lefts else 0.0

    def columns_of(block: dict[str, object]) -> list[str]:
        raw = block.get("columns")
        if not isinstance(raw, list):
            return []
        return [str(cell).strip() for cell in raw if str(cell).strip()]

    table_at = [False] * len(usable)
    index = 0
    while index < len(usable):
        width = len(columns_of(usable[index]))
        if width < 2:
            index += 1
            continue
        end = index + 1
        while end < len(usable) and len(columns_of(usable[end])) == width:
            if int(usable[end].get("page") or 0) != int(usable[index].get("page") or 0):
                break
            end += 1
        if end - index >= 2:
            for slot in range(index, end):
                table_at[slot] = True
        index = end if end > index else index + 1

    _mark_aligned_column_runs(usable, table_at)

    nodes: list = []
    index = 0
    previous_page = int(usable[0].get("page") or 1)
    while index < len(usable):
        page = int(usable[index].get("page") or previous_page)
        if page != previous_page and nodes:
            nodes.append(HorizontalRule())
            previous_page = page
        if table_at[index]:
            chunk: list[dict[str, object]] = []
            while (
                index < len(usable)
                and table_at[index]
                and int(usable[index].get("page") or page) == page
            ):
                chunk.append(usable[index])
                index += 1
            widths = {len(columns_of(block)) for block in chunk}
            if len(widths) == 1 and widths != {0}:
                rows = [columns_of(block) for block in chunk]
            else:
                rows = _rows_from_column_anchors(chunk)
            if rows:
                nodes.append(_layout_table(rows))
            continue
        block = usable[index]
        text = _visible_layout_text(str(block.get("text") or ""))
        if not text or re.fullmatch(r"\d{1,4}", text):
            index += 1
            continue
        height = _block_type_size(block)
        left = float(block.get("left") or body_left)
        quote_mark = text[:1] in "\"“«「『"
        if (
            height >= body_height * 1.15
            and len(text) <= 40
            and not _PDF_LIST_LINE.match(text)
            and not quote_mark
        ):
            level = 1 if height >= body_height * 1.7 else 2
            nodes.append(Heading(level=level, text=_heading_rich(text, _block_marks(block))))
        elif (
            not _PDF_LIST_LINE.match(text)
            and len(text) <= 240
            and (quote_mark or left >= body_left + 28)
        ):
            body = text.strip("\"“”«»「」『』").strip() or text
            cut = text.find(body)
            nodes.append(
                Blockquote(
                    paragraphs=[
                        _rich_with_font_marks(
                            body, _shift_style_marks(_block_marks(block), max(0, cut), len(body))
                        )
                    ]
                )
            )
        else:
            nodes.append(_node_from_visual_text(text, _block_marks(block)))
        index += 1
    return nodes


def _visible_layout_text(text: str) -> str:
    """Drop zero-width characters. A lone U+200C used to become a blockquote."""
    return _INVISIBLE_CHARS.sub("", text or "").strip()


def _layout_table(rows: list[list[str]]) -> object:
    from aduport.core.document_model import Table, TableCell

    return Table(
        rows=rows,
        cells=[[TableCell(text=cell) for cell in row] for row in rows],
        header_rows=1 if len(rows) >= 2 else 0,
    )


def _block_type_size(block: dict[str, object]) -> float:
    """Font size when the PDF has one. Ink-box height is only a fallback.

    The ink box is the glyph bounds, so ``gjpqy`` makes a body line taller
    than an all-caps title set in the same size. Synthetic fixtures that only
    set ``height`` still use that number.
    """
    raw = block.get("font_size")
    if isinstance(raw, (int, float)) and float(raw) > 0:
        return float(raw)
    return _block_height(block)


def _block_height(block: dict[str, object]) -> float:
    raw = block.get("height")
    if isinstance(raw, (int, float)) and float(raw) > 0:
        return float(raw)
    try:
        return max(0.0, float(block["top"]) - float(block["bottom"]))
    except (KeyError, TypeError, ValueError):
        return 0.0


def _promote_layout_nodes(
    nodes: list,
    layout_blocks: list[dict[str, object]],
    total_chars: int,
    page_count: int,
) -> list | None:
    """Use layout nodes when they recover structure and cover the text layer."""
    if not layout_blocks or not nodes:
        return None
    if any(
        type(node).__name__ not in {"Paragraph", "ListItem", "HorizontalRule"}
        for node in nodes
    ):
        return None
    structured = nodes_from_layout_blocks(layout_blocks)
    kinds = {type(node).__name__ for node in structured}
    if not (kinds & {"Heading", "Table", "Blockquote"}):
        return None
    covered = sum(len(str(block.get("text") or "")) for block in layout_blocks)
    if total_chars and covered < total_chars * 0.55:
        return None
    pages = {int(block.get("page") or 0) for block in layout_blocks}
    if page_count > 1 and pages and max(pages) < page_count and covered < total_chars * 0.85:
        return None
    return structured


def _heading_rich(text: str, marks: list | None):
    """A heading that is entirely bold does not need a second bold mark."""
    from aduport.core.document_model import InlineMarkType

    rich = _rich_with_font_marks(text, marks)
    if not rich.marks:
        return rich
    limit = len(rich.text)
    kept = [
        mark
        for mark in rich.marks
        if not (
            mark.type == InlineMarkType.BOLD and mark.start == 0 and mark.end == limit
        )
    ]
    if len(kept) == len(rich.marks):
        return rich
    return rich.model_copy(update={"marks": kept})


def _block_marks(block: dict[str, object]) -> list:
    raw = block.get("marks")
    return raw if isinstance(raw, list) else []


def _node_from_visual_text(text: str, marks: list | None = None):
    """Promote a third-party text-layer bullet or decimal line to a list item."""
    from aduport.core.document_model import ListItem

    stripped = _visible_layout_text(text)
    # Marks were indexed on the block text. If this string was already that
    # text, offsets still line up; a shorter caller string just clips them.
    if marks and stripped != text:
        cut = text.find(stripped)
        marks = _shift_style_marks(marks, max(0, cut), len(stripped))
    match = _PDF_LIST_LINE.match(stripped)
    if match and match.group("num") and match.group("body")[:1].isdigit():
        match = None
    if match:
        body = match.group("body")
        return ListItem(
            text=_rich_with_font_marks(
                body, _shift_style_marks(marks, match.start("body"), len(body))
            ),
            ordered=bool(match.group("num") or match.group("alpha") or match.group("roman")),
            level=0,
        )
    return Paragraph(text=_rich_with_font_marks(stripped, marks))


def _page_to_paragraphs(text: str) -> list[str]:
    """Split a page into paragraph strings."""
    raw_lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    paragraphs: list[str] = []
    buf: list[str] = []
    for ln in raw_lines:
        if not ln:
            if buf:
                paragraphs.append(_join_wrapped_lines(buf))
                buf = []
            continue
        if _MD_LIST_ITEM.match(ln) or _PDF_LIST_LINE.match(ln):
            if buf:
                paragraphs.append(_join_wrapped_lines(buf))
                buf = []
            paragraphs.append(ln)
            continue
        if buf and (_MD_LIST_ITEM.match(buf[-1]) or _PDF_LIST_LINE.match(buf[-1])):
            paragraphs.append(_join_wrapped_lines(buf))
            buf = []
        buf.append(ln)
    if buf:
        paragraphs.append(_join_wrapped_lines(buf))
    return [p for p in paragraphs if p.strip()]


def _strip_running_headers_footers(page_texts: list[str]) -> list[str]:
    """Drop short lines that repeat across many pages (typical headers/footers).

    Conservative: only removes lines that appear on ≥50% of non-empty pages,
    are ≤40 chars, and are not the sole content of a page.
    """
    pages = [t for t in page_texts if t and t.strip()]
    if len(pages) < 2:
        return page_texts

    from collections import Counter

    counts: Counter[str] = Counter()
    for text in pages:
        # Consider first 2 and last 2 non-empty lines as header/footer candidates
        lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
        if not lines:
            continue
        candidates = set()
        for ln in lines[:2] + lines[-2:]:
            if 1 <= len(ln) <= 40:
                candidates.add(ln)
        for ln in candidates:
            counts[ln] += 1

    threshold = max(2, (len(pages) + 1) // 2)
    noise = {ln for ln, n in counts.items() if n >= threshold}
    if not noise:
        return page_texts

    cleaned: list[str] = []
    for text in page_texts:
        if not text:
            cleaned.append(text)
            continue
        kept = [
            ln
            for ln in text.replace("\r\n", "\n").split("\n")
            if ln.strip() not in noise
        ]
        # Keep at least something if stripping would empty the page
        if any(ln.strip() for ln in kept):
            cleaned.append("\n".join(kept).strip())
        else:
            cleaned.append(text)
    return cleaned


def _require_pdfium():
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise InvalidDocumentError(
            "PDF 文本回退需要 pypdfium2：pip install 'pypdfium2>=4.30'"
        ) from exc
    return pdfium


def _extract_all_page_texts(source_bytes: bytes) -> list[str]:
    """Return stripped text for every page. Must hold `_PDFIUM_LOCK`."""
    texts, _, _, _ = _analyze_pdf_once(source_bytes, layout=False, multicolumn=False)
    return texts


def _pdf_uri_rects(source_bytes: bytes) -> dict[int, list[tuple[str, tuple[float, float, float, float]]]]:
    """Page index → (uri, rect) for link annotations. No network fetch."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return {}
    try:
        reader = PdfReader(BytesIO(source_bytes))
    except Exception:
        return {}
    out: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = {}
    for index, page in enumerate(reader.pages):
        annots = page.get("/Annots") or []
        for annot in annots:
            try:
                obj = annot.get_object()
            except Exception:
                continue
            action = obj.get("/A")
            if not action:
                continue
            uri = action.get("/URI")
            rect = obj.get("/Rect")
            if not uri or not rect or len(rect) != 4:
                continue
            href = str(uri).strip()
            lowered = href.casefold()
            if not href or lowered.startswith(("javascript:", "vbscript:", "data:")):
                continue
            box = tuple(float(v) for v in rect)
            out.setdefault(index, []).append((href, box))  # type: ignore[arg-type]
    return out


def _anchor_from_bounded(text: str) -> str:
    return " ".join((text or "").split())


def _attach_pdf_uri_links(nodes: list, links: list[tuple[str, str]]) -> int:
    """Stamp the first matching text span with a link mark. Each pair once."""
    pending = sorted(links, key=lambda item: len(item[0]), reverse=True)
    applied = 0

    def stamp(node: object) -> None:
        nonlocal applied
        if not pending:
            return
        raw = getattr(node, "text", None)
        if isinstance(raw, str):
            rich = RichText(text=raw)
        elif isinstance(raw, RichText):
            rich = raw
        else:
            return
        if not rich.text:
            return
        kept: list[tuple[str, str]] = []
        for anchor, href in pending:
            start = rich.text.find(anchor)
            if start < 0:
                kept.append((anchor, href))
                continue
            end = start + len(anchor)
            already = any(
                mark.type == InlineMarkType.LINK and mark.start == start and mark.end == end
                for mark in rich.marks
            )
            if not already:
                rich.marks.append(
                    InlineMark(
                        type=InlineMarkType.LINK,
                        start=start,
                        end=end,
                        href=href,
                    )
                )
                applied += 1
        pending[:] = kept
        node.text = rich  # type: ignore[attr-defined]

    for node in nodes:
        stamp(node)
        if not pending:
            break
    return applied


def _analyze_pdf_once(
    source_bytes: bytes,
    *,
    layout: bool = True,
    multicolumn: bool = True,
    max_layout_pages: int = 3,
    max_blocks: int = 200,
) -> tuple[list[str], int, list[dict[str, object]], list[tuple[str, str]]]:
    """Single PdfDocument open for text + multicolumn count + layout blocks (P1 #18)."""
    pdfium = _require_pdfium()
    doc = pdfium.PdfDocument(BytesIO(source_bytes))
    texts: list[str] = []
    multi_count = 0
    blocks: list[dict[str, object]] = []
    uri_links: list[tuple[str, str]] = []
    uri_rects = _pdf_uri_rects(source_bytes)
    try:
        n_pages = len(doc)
        want_multi = bool(multicolumn and settings.pdf_text_multicolumn)
        for i in range(n_pages):
            page = doc[i]
            try:
                textpage = page.get_textpage()
                try:
                    raw = _page_text_with_columns(page, textpage) or ""
                    texts.append((raw or "").strip())
                    for href, (x0, y0, x1, y1) in uri_rects.get(i, []):
                        try:
                            snippet = textpage.get_text_bounded(
                                left=x0, bottom=y0, right=x1, top=y1
                            )
                        except Exception:
                            snippet = ""
                        anchor = _anchor_from_bounded(snippet or "")
                        if len(anchor) >= 2:
                            uri_links.append((anchor, href))
                    if want_multi:
                        try:
                            full = (textpage.get_text_bounded() or "").strip()
                            width = float(page.get_width())
                            height = float(page.get_height())
                            mid = width / 2.0
                            gap = width * 0.02
                            left = (
                                textpage.get_text_bounded(
                                    left=0, bottom=0, right=mid - gap, top=height
                                )
                                or ""
                            ).strip()
                            right = (
                                textpage.get_text_bounded(
                                    left=mid + gap, bottom=0, right=width, top=height
                                )
                                or ""
                            ).strip()
                            if len(left) >= 80 and len(right) >= 80:
                                merged = left + "\n\n" + right
                                if merged != full and len(merged) > len(full) * 0.85:
                                    multi_count += 1
                        except Exception as exc:
                            swallow_import_error(exc, where='pdf_text_fallback.py:184')
                    if layout and i < max_layout_pages and len(blocks) < max_blocks:
                        try:
                            n = textpage.count_chars()
                            if n > 0:
                                import ctypes

                                font_buf = ctypes.create_string_buffer(128)
                                font_flags = ctypes.c_int(0)
                                strikes = _strike_rules(page)
                                chars: list[tuple] = []
                                for ci in range(n):
                                    box = textpage.get_charbox(ci)
                                    ch = textpage.get_text_range(ci, 1) or ""
                                    if not ch.strip() and ch != " ":
                                        continue
                                    left, bottom, right, top = box
                                    font_size = 0.0
                                    try:
                                        font_size = float(
                                            pdfium.raw.FPDFText_GetFontSize(textpage.raw, ci)
                                        )
                                    except Exception:
                                        font_size = 0.0
                                    style = 0
                                    try:
                                        font_flags.value = 0
                                        read = pdfium.raw.FPDFText_GetFontInfo(
                                            textpage.raw, ci, font_buf, 128, ctypes.byref(font_flags)
                                        )
                                        name = font_buf.value.decode("latin1", "replace") if read else ""
                                        style = _emphasis_bits(name, int(font_flags.value))
                                    except Exception:
                                        style = 0
                                    if _rule_strikes_char((left, bottom, right, top), strikes):
                                        style |= 4
                                    chars.append((left, bottom, right, top, ch, font_size, style))
                                if chars:
                                    page_h = float(page.get_height())
                                    lines = _group_chars_into_lines(chars)
                                    for line in sorted(lines, key=lambda ln: -ln[0][1]):
                                        blocks.append(
                                            _layout_block_from_line(
                                                line, page_idx=i, page_h=page_h
                                            )
                                        )
                                        if len(blocks) >= max_blocks:
                                            break
                        except Exception as exc:
                            swallow_import_error(exc, where='pdf_text_fallback.py:233')
                finally:
                    textpage.close()
            finally:
                page.close()
        return texts, multi_count, blocks, uri_links
    finally:
        doc.close()


def _page_text_with_columns(page, textpage) -> str:
    """Full-page text; optionally merge left/right halves for two-column layouts."""
    full = (textpage.get_text_bounded() or "").strip()
    if not settings.pdf_text_multicolumn:
        return full
    try:
        width = float(page.get_width())
        height = float(page.get_height())
    except Exception as exc:
        return swallow_import_error(exc, where='pdf_text_fallback.py:252', default=full)
    if width < 200 or height < 200:
        return full
    mid = width / 2.0
    gap = width * 0.02
    left = (textpage.get_text_bounded(left=0, bottom=0, right=mid - gap, top=height) or "").strip()
    right = (textpage.get_text_bounded(left=mid + gap, bottom=0, right=width, top=height) or "").strip()
    if len(left) < 80 or len(right) < 80:
        return full
    # Prefer column merge when halves are substantial and not near-duplicates of full
    if abs(len(left) + len(right) - len(full)) > max(40, len(full) * 0.15):
        # Halves don't reconstruct full well → keep reading order from full
        if left.count("\n") + right.count("\n") < full.count("\n") * 0.4:
            return full
    return left + "\n\n" + right


def extract_pdf_text_model(source_bytes: bytes, filename: str = "input.pdf") -> DocumentModel:
    """
    Extract embedded text layer page-by-page (always sequential under a lock).

    Suitable for digital (non-scanned) PDFs when docling models are unavailable.
    ``ADUPORT_PDF_TEXT_PARALLEL`` is ignored — parallel PDFium use crashes on macOS.
    """
    if settings.pdf_text_parallel:
        _log.warning(
            "ADUPORT_PDF_TEXT_PARALLEL is set but ignored — PDFium is not thread-safe"
        )

    try:
        with _PDFIUM_LOCK:
            page_texts, multicolumn_pages, layout_blocks, uri_links = _analyze_pdf_once(
                source_bytes
            )
    except InvalidDocumentError:
        raise
    except Exception as exc:
        raise CorruptedFileError(f"无法打开 PDF: {filename} — {exc}") from exc

    page_count = len(page_texts)
    if page_count < 1:
        raise InvalidDocumentError(f"PDF 无页面: {filename}")

    page_texts = [
        normalize_pdf_text(text) if text else text for text in page_texts
    ]
    page_texts, marker_images = _strip_image_markers(page_texts)
    page_texts = _strip_running_headers_footers(page_texts)
    visual_pages, cue_text = _partition_cue_pages(page_texts)
    if not cue_text.strip():
        cue_text = _attached_cue_text(source_bytes)

    nodes: list = []
    total_chars = 0
    line_mode = bool(cue_text.strip())
    for text in visual_pages:
        if not text:
            continue
        text = normalize_pdf_text(text)
        if not text.strip():
            continue
        total_chars += len(text)
        if line_mode:
            for line in _page_lines(text):
                nodes.append(_node_from_visual_text(line))
            continue
        # Page separators — do not forge H2 "第 N 页" into the outline (P1 #8)
        if len(visual_pages) > 1 and nodes:
            nodes.append(HorizontalRule())
        restored = _markdown_page_nodes(text)
        if restored:
            nodes.extend(restored)
            continue
        for para in _page_to_paragraphs(text):
            nodes.append(_node_from_visual_text(para))
    layout_promoted = False
    if not line_mode:
        promoted = _promote_layout_nodes(
            nodes, layout_blocks, total_chars, page_count
        )
        if promoted is not None:
            nodes = promoted
            layout_promoted = True
    cues_folded = False
    if cue_text.strip():
        cue_nodes = _cue_nodes_from_text(cue_text)
        if cue_nodes:
            nodes = _fold_cue_nodes(nodes, cue_nodes)
            cues_folded = True
            total_chars += len(cue_text.strip())

    if total_chars < 1:
        raise InvalidDocumentError(
            f"PDF 无可提取文本层（可能为扫描件）: {filename}。"
            "请启用 OCR 并预下载 docling 模型（见 scripts/download_docling_models.sh），"
            "或设置 HF_ENDPOINT 镜像后重试。"
        )

    from aduport.core.title_source import resolve_title

    title = resolve_title("", nodes, filename=filename)
    layout_meta = {
        "page_count": page_count,
        "multicolumn_pages": multicolumn_pages,
        "reconstruction": "charbox_v1+text_layer+header_strip",
        "full_layout": bool(layout_blocks),
        "pixel_mode": "charbox_v1",
        "blocks": layout_blocks,
        "page_breaks": page_count - 1 if page_count > 1 else 0,
    }
    model = DocumentModel(
        title=title,
        nodes=nodes,
        metadata={
            "pdf_layout": layout_meta,
            "importer": "pdf_text",
            "pdf_text_chars": total_chars,
            "structure_fidelity": "below_text",
            "pdf_cues": "folded" if cues_folded else "absent",
            "pdf_text_structure": "layout" if layout_promoted else "flat",
        },
    )
    if layout_blocks:
        from aduport.core.node_span import attach_spans_from_layout_blocks

        attach_spans_from_layout_blocks(
            model, layout_blocks, source="pdf_layout:charbox_v1"
        )
    images = marker_images or _images_from_xobjects(source_bytes)
    if images:
        model.nodes = [node for node in model.nodes if not _is_image_placeholder(node)]
        model.nodes.extend(images)
    linked = _attach_pdf_uri_links(model.nodes, uri_links)
    if linked:
        model.metadata["pdf_uri_links"] = linked
    return model


def _count_multicolumn_pages(source_bytes: bytes) -> int:
    """Pages where left/right column heuristic would merge (layout hint)."""
    if not settings.pdf_text_multicolumn:
        return 0
    pdfium = _require_pdfium()
    doc = pdfium.PdfDocument(BytesIO(source_bytes))
    count = 0
    try:
        for i in range(len(doc)):
            page = doc[i]
            try:
                textpage = page.get_textpage()
                try:
                    full = (textpage.get_text_bounded() or "").strip()
                    width = float(page.get_width())
                    height = float(page.get_height())
                    mid = width / 2.0
                    gap = width * 0.02
                    left = (
                        textpage.get_text_bounded(
                            left=0, bottom=0, right=mid - gap, top=height
                        )
                        or ""
                    ).strip()
                    right = (
                        textpage.get_text_bounded(
                            left=mid + gap, bottom=0, right=width, top=height
                        )
                        or ""
                    ).strip()
                    if len(left) >= 80 and len(right) >= 80:
                        merged = left + "\n\n" + right
                        if merged != full and len(merged) > len(full) * 0.85:
                            count += 1
                finally:
                    textpage.close()
            finally:
                page.close()
    finally:
        doc.close()
    return count


def _extract_layout_blocks(source_bytes: bytes, *, max_pages: int = 3, max_blocks: int = 200) -> list[dict[str, object]]:
    """Group PDFium char boxes into positioned line blocks (pixel-level layout hint)."""
    pdfium = _require_pdfium()
    doc = pdfium.PdfDocument(BytesIO(source_bytes))
    blocks: list[dict[str, object]] = []
    try:
        for page_idx in range(min(len(doc), max_pages)):
            page = doc[page_idx]
            try:
                textpage = page.get_textpage()
                try:
                    n = textpage.count_chars()
                    if n <= 0:
                        continue
                    chars: list[tuple[float, float, float, float, str]] = []
                    for i in range(n):
                        box = textpage.get_charbox(i)
                        ch = textpage.get_text_range(i, 1) or ""
                        if not ch.strip() and ch != " ":
                            continue
                        left, bottom, right, top = box
                        chars.append((left, bottom, right, top, ch))
                    if not chars:
                        continue
                    page_h = float(page.get_height())
                    lines = _group_chars_into_lines(chars)
                    for line in sorted(lines, key=lambda ln: -ln[0][1]):
                        left = min(x[0] for x in line)
                        bottom = min(x[1] for x in line)
                        right = max(x[2] for x in line)
                        top = max(x[3] for x in line)
                        text = "".join(x[4] for x in sorted(line, key=lambda x: x[0]))
                        blocks.append(
                            {
                                "page": page_idx + 1,
                                "left": round(left, 2),
                                "bottom": round(bottom, 2),
                                "right": round(right, 2),
                                "top": round(top, 2),
                                "y_from_top": round(page_h - top, 2),
                                "text": text.strip(),
                            }
                        )
                        if len(blocks) >= max_blocks:
                            break
                finally:
                    textpage.close()
            finally:
                page.close()
            if len(blocks) >= max_blocks:
                break
    finally:
        doc.close()
    return blocks


def pdf_text_char_count(source_bytes: bytes) -> int:
    try:
        _require_pdfium()
    except InvalidDocumentError:
        _log.warning(
            "pypdfium2 not installed — PDF text-layer count returns 0; "
            "install AduPort core deps (pypdfium2>=4.30)"
        )
        return 0
    try:
        with _PDFIUM_LOCK:
            texts = _extract_all_page_texts(source_bytes)
        return sum(len(t) for t in texts)
    except Exception as exc:
        return swallow_import_error(exc, where='pdf_text_fallback.py:463', default=0)
