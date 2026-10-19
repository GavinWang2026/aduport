"""Native RTF import fallback (control-word strip). Prefer anydoc when available."""

from __future__ import annotations

import re

from aduport.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Footnote,
    Heading,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    Paragraph,
    RichText,
)
from aduport.core.title_source import resolve_title
from aduport.exporters.table_span import _rtf_unicode_char

_CTRL = re.compile(
    r"\\([a-zA-Z]+)(-?\d+)? ?"  # control word
    r"|\\'([0-9a-fA-F]{2})"  # hex char
    r"|\\[{}\\]"  # escaped
    r"|[{}]"  # groups
    r"|\\[^a-zA-Z]"  # other escapes
)

# Destination groups whose interior text must not leak into the body (v16 F3).
_SKIP_GROUPS = frozenset(
    {
        "fonttbl",
        "colortbl",
        "stylesheet",
        "info",
        "pict",
        "object",
        "datastore",
        "themedata",
        "colorschememapping",
        "latentstyles",
        "rsidtbl",
        "generator",
        "xmlnstbl",
        "filetbl",
        "revtbl",
    }
)


def _skip_group(text: str, start: int) -> int:
    """``start`` points at ``{``. Return the index just after the matching ``}``."""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _drop_destination_groups(raw: str) -> str:
    """Remove ``{\\fonttbl...}``, ``{\\colortbl...}``, and ``{\\*...}`` groups."""
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] == "{":
            j = i + 1
            while j < n and raw[j] in " \r\n\t":
                j += 1
            star = False
            if raw.startswith("\\*", j):
                star = True
                j += 2
                while j < n and raw[j] in " \r\n\t":
                    j += 1
            word = ""
            if j < n and raw[j] == "\\":
                k = j + 1
                while k < n and raw[k].isalpha():
                    k += 1
                word = raw[j + 1 : k].lower()
            if star or word in _SKIP_GROUPS:
                i = _skip_group(raw, i)
                out.append(" ")
                continue
        out.append(raw[i])
        i += 1
    return "".join(out)


def _strip_rtf(raw: str) -> str:
    text = _drop_destination_groups(raw)
    out: list[str] = []
    i = 0
    while i < len(text):
        m = _CTRL.match(text, i)
        if m:
            if m.group(1) in ("par", "line", "page"):
                out.append("\n")
            elif m.group(1) == "u" and m.group(2):
                out.append(_rtf_unicode_char(m.group(2)))
                i = m.end()
                if i < len(text) and text[i] == "?":
                    i += 1
                continue
            elif m.group(3):
                try:
                    out.append(chr(int(m.group(3), 16)))
                except ValueError:
                    pass
            elif m.group(0) in (r"\\", r"\{", r"\}"):
                out.append(m.group(0)[-1])
            i = m.end()
            continue
        out.append(text[i])
        i += 1
    cleaned = "".join(out)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# Markers written by rtf_exporter. Matched before control words are stripped,
# otherwise code fences and quotes collapse into plain paragraphs.
_STRUCT_RE = re.compile(
    r"\{\\b\\fs36\s+(?P<title>.*?)\}\\par"
    r"|\{\\i code:(?P<lang>[^{}]*)\}\\par\{\\pard\\f1\s*(?P<code_body>.*?)\\par\}"
    r"|\{\\pard\\f1\s*(?P<bare_code>.*?)\\par\}"
    r"|\{\\pard\\ls(?P<ls>\d+)\\ilvl(?P<ilvl>\d+)\s*(?:\{\\\*\\mdn (?P<mdn>\d+)\})?(?P<ls_body>.*?)\\par\}"
    r"|\{\\pard\\li(?P<li>\d+)\s*(?P<li_prefix>\\bullet\s+|\[\s*[xX ]\]\s+|\d+\.\s+)(?P<li_body>.*?)\\par\}"
    r"|\{\\pard\\li360\\i\s+(?P<quote>.*?)\\par\}"
    r"|\{\\super\s+\[\^(?P<fn_id>[^\]]+)\]\}\{\\footnote\s+(?P<fn_body>.*?)\}"
    r"|\{\\fs(?P<fs>\d+)\\b\s+(?P<heading>.*?)\}\\par",
    re.S,
)


def _fs_to_level(fs: int) -> int:
    """Inverse of ``size = max(28, 48 - 4 * level)`` in the RTF exporter."""
    if fs >= 44:
        return 1
    if fs >= 40:
        return 2
    if fs >= 36:
        return 3
    if fs >= 32:
        return 4
    return 5


def _flush_quotes(nodes: list, buf: list[str]) -> None:
    if buf:
        nodes.append(Blockquote(paragraphs=list(buf)))
        buf.clear()


_HYPERLINK_RE = re.compile(
    r"\{\\field\{\\\*\\fldinst HYPERLINK \"([^\"]*)\"\}\{\\fldrslt\{\\ul\\cf1 ((?:[^{}]|\{[^{}]*\})*)\}\}\}"
)


def _restore_hyperlinks(raw: str) -> str:
    """Turn our own HYPERLINK fields into Markdown before destination groups are dropped."""

    def repl(match: re.Match[str]) -> str:
        href, shown = match.group(1), match.group(2)
        return f"[{shown}]({href})"

    return _HYPERLINK_RE.sub(repl, raw)


def _rtf_to_rich(raw: str) -> RichText:
    """Keep ``{\\b}`` / ``{\\i}`` / ``{\\f1}`` as marks instead of stripping them."""
    raw = _restore_hyperlinks(raw)
    raw = _drop_destination_groups(raw)
    chars: list[str] = []
    marks: list[InlineMark] = []
    stack: list[tuple[InlineMarkType | None, int]] = []

    def close_group() -> None:
        if not stack:
            return
        kind, start = stack.pop()
        if kind is not None and len(chars) > start:
            marks.append(InlineMark(type=kind, start=start, end=len(chars)))

    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == "{":
            j = i + 1
            while j < n and raw[j] in " \r\n\t":
                j += 1
            kind: InlineMarkType | None = None
            word_m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", raw[j:])
            if word_m:
                word = word_m.group(1).lower()
                num = word_m.group(2)
                if word == "b" and num != "0":
                    kind = InlineMarkType.BOLD
                elif word == "i" and num != "0":
                    kind = InlineMarkType.ITALIC
                elif word == "f" and num == "1":
                    kind = InlineMarkType.CODE
                elif word == "strike":
                    kind = InlineMarkType.STRIKETHROUGH
                j += word_m.end()
            stack.append((kind, len(chars)))
            i = j
            continue
        if ch == "}":
            close_group()
            i += 1
            continue
        if ch == "\\":
            hex_m = re.match(r"\\'([0-9a-fA-F]{2})", raw[i:])
            if hex_m:
                chars.append(chr(int(hex_m.group(1), 16)))
                i += hex_m.end()
                continue
            if raw.startswith("\\\\", i) or raw.startswith("\\{", i) or raw.startswith("\\}", i):
                chars.append(raw[i + 1])
                i += 2
                continue
            word_m = re.match(r"\\([a-zA-Z]+)(-?\d+)? ?", raw[i:])
            if word_m:
                word = word_m.group(1).lower()
                if word == "u" and word_m.group(2):
                    chars.append(_rtf_unicode_char(word_m.group(2)))
                    i += word_m.end()
                    if i < n and raw[i] == "?":
                        i += 1
                    continue
                if word in ("par", "line"):
                    chars.append("\n")
                elif word in ("bullet",):
                    pass
                i += word_m.end()
                continue
            if i + 1 < n:
                chars.append(raw[i + 1])
                i += 2
                continue
        chars.append(ch)
        i += 1
    while stack:
        close_group()
    text = "".join(chars)
    lead = len(text) - len(text.lstrip())
    trimmed = text.strip()
    if lead or trimmed != text:
        marks = [
            InlineMark(type=m.type, start=m.start - lead, end=m.end - lead)
            for m in marks
            if m.end > lead and m.start - lead < len(trimmed)
        ]
        marks = [
            InlineMark(
                type=m.type,
                start=max(0, m.start),
                end=min(len(trimmed), m.end),
            )
            for m in marks
            if m.end > m.start
        ]
    return RichText(text=trimmed, marks=marks)


def _append_plain(nodes: list, quote_buf: list[str], fragment: str) -> None:
    rich_parts = [_rtf_to_rich(part) for part in re.split(r"\\par\b", fragment)]
    if not any(part.text for part in rich_parts):
        return
    _flush_quotes(nodes, quote_buf)
    for rich in rich_parts:
        if rich.text:
            nodes.append(Paragraph(text=rich))


def _nodes_from_rtf_text(chunk: str) -> tuple[list, str]:
    """Lift exporter markers out of one non-table RTF chunk."""
    nodes: list = []
    quote_buf: list[str] = []
    explicit = ""
    pos = 0
    saw_body = False
    for match in _STRUCT_RE.finditer(chunk):
        if match.start() > pos:
            _append_plain(nodes, quote_buf, chunk[pos:match.start()])
            if nodes or quote_buf:
                saw_body = True
        title = match.group("title")
        lang = match.group("lang")
        code_body = match.group("code_body")
        bare_code = match.group("bare_code")
        li_prefix = match.group("li_prefix")
        ls_body = match.group("ls_body")
        li_body = match.group("li_body")
        li_indent = match.group("li")
        quote = match.group("quote")
        fn_id = match.group("fn_id")
        fn_body = match.group("fn_body")
        fs = match.group("fs")
        heading = match.group("heading")
        if title is not None:
            text = _strip_rtf(title)
            if text and not explicit and not saw_body and not nodes and not quote_buf:
                explicit = text
            elif text:
                _flush_quotes(nodes, quote_buf)
                nodes.append(Paragraph(text=RichText(text=text)))
                saw_body = True
        elif code_body is not None or bare_code is not None:
            _flush_quotes(nodes, quote_buf)
            body = _strip_rtf(code_body if code_body is not None else bare_code)
            nodes.append(CodeBlock(lang=_strip_rtf(lang or ""), content=body))
            saw_body = True
        elif ls_body is not None:
            _flush_quotes(nodes, quote_buf)
            mdn = match.group("mdn")
            nodes.append(
                ListItem(
                    text=_rtf_to_rich(ls_body),
                    ordered=True,
                    level=int(match.group("ilvl") or 0),
                    number=int(mdn) if mdn else None,
                )
            )
            saw_body = True
        elif li_body is not None:
            _flush_quotes(nodes, quote_buf)
            prefix = li_prefix or ""
            ordered = bool(re.match(r"\d+\.", prefix))
            checked: bool | None = None
            if "[x]" in prefix.lower():
                checked = True
            elif "[ ]" in prefix:
                checked = False
            level = max(0, int(li_indent or "360") // 360 - 1)
            nodes.append(
                ListItem(
                    text=_rtf_to_rich(li_body),
                    ordered=ordered,
                    checked=checked,
                    level=level,
                )
            )
            saw_body = True
        elif quote is not None:
            text = _strip_rtf(quote)
            if text:
                quote_buf.append(text)
                saw_body = True
        elif fn_id is not None:
            _flush_quotes(nodes, quote_buf)
            nodes.append(Footnote(id=_strip_rtf(fn_id) or "1", text=_strip_rtf(fn_body or "")))
            saw_body = True
        elif heading is not None:
            _flush_quotes(nodes, quote_buf)
            text = _strip_rtf(heading)
            if text:
                nodes.append(Heading(level=_fs_to_level(int(fs)), text=text))
                saw_body = True
        pos = match.end()
    if pos < len(chunk):
        _append_plain(nodes, quote_buf, chunk[pos:])
    _flush_quotes(nodes, quote_buf)
    return nodes, explicit


_RTF_PICT = re.compile(
    r"\{\\\*\\mdalt ([^}]*)\}\{\\pict\\(pngblip|jpegblip)\s*([0-9a-fA-F\s]*)\}",
    re.IGNORECASE,
)


def _take_rtf_pictures(raw: str) -> tuple[str, list[Image]]:
    images: list[Image] = []

    def replace(match: re.Match[str]) -> str:
        hexdata = re.sub(r"\s+", "", match.group(3))
        if len(hexdata) < 2 or len(hexdata) % 2:
            return ""
        try:
            data = bytes.fromhex(hexdata)
        except ValueError:
            return ""
        if not data:
            return ""
        mime = "image/jpeg" if match.group(2).lower() == "jpegblip" else "image/png"
        images.append(Image(data=data, alt_text=match.group(1) or "image", mime_type=mime))
        return ""

    return _RTF_PICT.sub(replace, raw), images


def import_rtf(source_bytes: bytes, filename: str = "input.rtf") -> DocumentModel:
    try:
        raw = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw = source_bytes.decode("latin-1", errors="ignore")

    from aduport.exporters.table_span import parse_rtf_table_from_rows, split_rtf_segments

    raw, pictures = _take_rtf_pictures(raw)
    nodes: list = []
    explicit = ""
    table_buffer: list[str] = []
    for kind, chunk in split_rtf_segments(raw):
        if kind == "table_row":
            table_buffer.append(chunk)
            continue
        if table_buffer:
            table = parse_rtf_table_from_rows(table_buffer)
            if table:
                nodes.append(table)
            table_buffer = []
        part_nodes, part_title = _nodes_from_rtf_text(chunk)
        if part_title and not explicit:
            explicit = part_title
        nodes.extend(part_nodes)

    if table_buffer:
        table = parse_rtf_table_from_rows(table_buffer)
        if table:
            nodes.append(table)

    if not nodes:
        part_nodes, part_title = _nodes_from_rtf_text(raw)
        if part_title and not explicit:
            explicit = part_title
        nodes = part_nodes

    nodes.extend(pictures)
    return DocumentModel(
        title=resolve_title(explicit, nodes, filename=filename),
        nodes=nodes or [Paragraph(text=RichText(text=""))],
        metadata={"importer": "rtf_native"},
    )
