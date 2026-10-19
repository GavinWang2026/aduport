"""Import a subset of LaTeX article sources into DocumentModel.

Supports common article macros produced by ``export_latex`` and similar
minimal documents: ``\\section`` / ``\\subsection``, paragraphs, itemize/
enumerate, verbatim, display math. Not a full TeX engine.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from aduport.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Heading,
    HorizontalRule,
    Image,
    ListItem,
    MathBlock,
    Paragraph,
)
from aduport.core.exceptions import InvalidDocumentError

_SECTION_RE = re.compile(
    r"\\(section|subsection|subsubsection|paragraph)\*?\{([^{}]*)\}"
)
_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
_END_RE = re.compile(r"\\end\{([a-zA-Z*]+)\}")
_ITEM_RE = re.compile(r"\\item\b\s*")
_TITLE_RE = re.compile(r"\\title\{([^{}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
_GRAPHICS_RE = re.compile(
    r"\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}[ \t]*\n?[ \t]*%[ \t]*aduport-b64:[ \t]*([A-Za-z0-9+/=]+)"
)
_GRAPHICS_BARE_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^{}]+)\}")


def _matching_end(text: str, cursor: int, env: str) -> int:
    token_begin = rf"\begin{{{env}}}"
    token_end = rf"\end{{{env}}}"
    depth = 1
    index = cursor
    while index < len(text):
        next_begin = text.find(token_begin, index)
        next_end = text.find(token_end, index)
        if next_end < 0:
            return -1
        if 0 <= next_begin < next_end:
            depth += 1
            index = next_begin + len(token_begin)
            continue
        depth -= 1
        if depth == 0:
            return next_end
        index = next_end + len(token_end)
    return -1


def _cut_list_env(text: str) -> tuple[str, str, str, str] | None:
    """Split out the first balanced itemize/enumerate. Returns head, env, inner, tail."""
    match = re.search(r"\\begin\{(itemize|enumerate)\}", text)
    if not match:
        return None
    env = match.group(1)
    begin = match.start()
    token_begin = rf"\begin{{{env}}}"
    token_end = rf"\end{{{env}}}"
    depth = 0
    cursor = begin
    while cursor < len(text):
        next_begin = text.find(token_begin, cursor)
        next_end = text.find(token_end, cursor)
        if next_end < 0:
            return None
        if 0 <= next_begin < next_end:
            depth += 1
            cursor = next_begin + len(token_begin)
            continue
        depth -= 1
        cursor = next_end + len(token_end)
        if depth == 0:
            inner = text[begin + len(token_begin) : next_end]
            return text[:begin], env, inner, text[cursor:]
    return None


def _list_items(inner: str, ordered: bool, level: int) -> list:
    nested: list[tuple[str, str]] = []
    rebuilt: list[str] = []
    cursor = inner
    while True:
        cut = _cut_list_env(cursor)
        if cut is None:
            rebuilt.append(cursor)
            break
        head, env, body, tail = cut
        rebuilt.append(head)
        rebuilt.append(f"\nMDBNEST{len(nested)}\n")
        nested.append((env, body))
        cursor = tail
    nodes: list = []
    for chunk in _ITEM_RE.split("".join(rebuilt)):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = re.split(r"MDBNEST(\d+)", chunk)
        head = re.sub(r"(?m)(?<!\\)%[^\n]*", "", parts[0]).strip()
        if head:
            nodes.append(ListItem(text=_rich_inline(head), ordered=ordered, level=level))
        for index in range(1, len(parts), 2):
            env, body = nested[int(parts[index])]
            nodes.extend(_list_items(body, env == "enumerate", level + 1))
            trail = (
                re.sub(r"(?m)(?<!\\)%[^\n]*", "", parts[index + 1]).strip()
                if index + 1 < len(parts)
                else ""
            )
            if trail:
                nodes.append(ListItem(text=_rich_inline(trail), ordered=ordered, level=level))
    return nodes


def _pull_footnotes(body: str) -> tuple[str, list]:
    from aduport.core.document_model import Footnote

    notes: list[Footnote] = []

    def _repl(match: re.Match[str]) -> str:
        fid = str(len(notes) + 1)
        notes.append(Footnote(id=fid, text=_unescape(match.group(1)).strip()))
        return f"[^{fid}]"

    return re.sub(r"\\footnote\{([^{}]*)\}", _repl, body), notes


def _balanced_brace(src: str, i: int) -> tuple[str, int]:
    """``src[i]`` is ``{``. Return the inner text and the index after ``}``."""
    depth = 0
    start = i + 1
    for k in range(i, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[start:k], k + 1
    return src[start:], len(src)


def _rich_inline(raw: str):
    """Restore ``\\textbf`` / ``\\textit`` / ``\\texttt`` / ``\\href`` as marks.

    Unknown commands keep their brace argument. ``_unescape`` used to delete
    ``\\textbf{bold}`` entirely, so the word disappeared on the way back.
    """
    from aduport.core.document_model import InlineMark, InlineMarkType, RichText

    pieces: list[str] = []
    marks: list[InlineMark] = []
    i = 0
    kind_of = {
        "textbf": InlineMarkType.BOLD,
        "textit": InlineMarkType.ITALIC,
        "emph": InlineMarkType.ITALIC,
        "texttt": InlineMarkType.CODE,
        "sout": InlineMarkType.STRIKETHROUGH,
    }

    def _append_rich(inner: str, kind: InlineMarkType | None = None, href: str = "") -> None:
        child = _rich_inline(inner)
        start = len("".join(pieces))
        pieces.append(child.text)
        end = len("".join(pieces))
        shift = start
        for mark in child.marks:
            marks.append(
                InlineMark(type=mark.type, start=mark.start + shift, end=mark.end + shift, href=mark.href)
            )
        if end > start and kind is not None:
            marks.append(InlineMark(type=kind, start=start, end=end, href=href))

    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw) and raw[i + 1].isalpha():
            cmd_m = re.match(r"\\([a-zA-Z]+)\*?", raw[i:])
            assert cmd_m is not None
            cmd = cmd_m.group(1)
            j = i + cmd_m.end()
            if j < len(raw) and raw[j] == "[":
                endb = raw.find("]", j)
                j = endb + 1 if endb >= 0 else len(raw)
            if cmd == "textasciicircum" and raw.startswith("{}", j):
                pieces.append("^")
                i = j + 2
                continue
            if cmd == "href" and j < len(raw) and raw[j] == "{":
                href, j = _balanced_brace(raw, j)
                if j < len(raw) and raw[j] == "{":
                    inner, j = _balanced_brace(raw, j)
                    child = _rich_inline(inner)
                    if href.startswith("#fn-") or re.fullmatch(r"\[\^[^\]]+\]", child.text):
                        pieces.append(child.text)
                        shift = len("".join(pieces)) - len(child.text)
                        for mark in child.marks:
                            marks.append(
                                InlineMark(
                                    type=mark.type,
                                    start=mark.start + shift,
                                    end=mark.end + shift,
                                    href=mark.href,
                                )
                            )
                    else:
                        _append_rich(inner, InlineMarkType.LINK, href=_unescape(href))
                i = j
                continue
            if cmd in kind_of and j < len(raw) and raw[j] == "{":
                inner, j = _balanced_brace(raw, j)
                _append_rich(inner, kind_of[cmd])
                i = j
                continue
            if j < len(raw) and raw[j] == "{":
                inner, j = _balanced_brace(raw, j)
                _append_rich(inner)
                i = j
                continue
            i = j
            continue
        pieces.append(raw[i])
        i += 1

    text = _unescape_literals("".join(pieces))
    lead = len(text) - len(text.lstrip())
    trimmed = text.strip()
    if lead or trimmed != text:
        marks = [
            InlineMark(type=m.type, start=m.start - lead, end=m.end - lead, href=m.href)
            for m in marks
            if m.end > lead
        ]
        marks = [
            InlineMark(
                type=m.type,
                start=max(0, m.start),
                end=min(len(trimmed), m.end),
                href=m.href,
            )
            for m in marks
            if m.end > max(0, m.start)
        ]
    return RichText(text=trimmed, marks=marks)


def _unescape_literals(text: str) -> str:
    """Undo ``_esc`` character replacements without deleting command arguments."""
    repl = {
        r"\%": "%",
        r"\_": "_",
        r"\&": "&",
        r"\#": "#",
        r"\$": "$",
        r"\{": "{",
        r"\}": "}",
        r"\textbackslash{}": "\\",
        r"\textasciitilde{}": "~",
        r"\textasciicircum{}": "^",
        "~": " ",
    }
    out = text
    for src, dst in repl.items():
        out = out.replace(src, dst)
    return out


def _unescape(text: str) -> str:
    repl = {
        r"\%": "%",
        r"\_": "_",
        r"\&": "&",
        r"\#": "#",
        r"\$": "$",
        r"\{": "{",
        r"\}": "}",
        r"~": " ",
        r"\,": " ",
        r"\ ": " ",
    }
    out = text
    for a, b in repl.items():
        out = out.replace(a, b)
    out = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?(?:\{[^{}]*\})?", "", out)
    return re.sub(r"\s+", " ", out).strip()


def _strip_comments(src: str) -> str:
    return _COMMENT_RE.sub("", src)


def _pull_graphics(text: str) -> tuple[str, list[Image]]:
    """Lift ``\\includegraphics`` (and an optional base64 comment) into Image nodes.

    Comments are stripped before the paragraph walk, so the bytes have to be
    read here. A command without bytes still becomes an Image node.
    """
    images: list[Image] = []

    def _one(name: str, b64: str | None) -> str:
        raw = b""
        if b64:
            try:
                raw = base64.b64decode(b64)
            except Exception:
                raw = b""
        stem = Path(name).stem or "image"
        lower = name.lower()
        mime = "image/jpeg" if lower.endswith((".jpg", ".jpeg")) else "image/png"
        images.append(Image(data=raw, alt_text=stem, mime_type=mime))
        return ""

    def repl_full(match: re.Match[str]) -> str:
        return _one(match.group(1).strip(), match.group(2))

    def repl_bare(match: re.Match[str]) -> str:
        return _one(match.group(1).strip(), None)

    text = _GRAPHICS_RE.sub(repl_full, text)
    text = _GRAPHICS_BARE_RE.sub(repl_bare, text)
    return text, images


def import_latex(source_bytes: bytes, filename: str = "input.tex") -> DocumentModel:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = source_bytes.decode("latin-1", errors="replace")
    text, graphics = _pull_graphics(text)
    text = _strip_comments(text)
    if "\\documentclass" not in text and "\\begin{document}" not in text:
        # Still accept body-only snippets with section macros
        if "\\" not in text[:2000] and len(text.strip()) < 8:
            raise InvalidDocumentError(f"不像 LaTeX 源文件: {filename}")

    title = ""
    m = _TITLE_RE.search(text)
    if m:
        title = _unescape(m.group(1))

    body_m = re.search(r"\\begin\{document\}(.*)\\end\{document\}", text, re.S)
    body = body_m.group(1) if body_m else text
    body, footnotes = _pull_footnotes(body)

    nodes: list = []
    i = 0
    while i < len(body):
        # Environments
        bm = _BEGIN_RE.match(body, i)
        if bm:
            env_raw = bm.group(1)
            env = env_raw.rstrip("*")
            cursor = bm.end()
            opt = ""
            minted_lang = ""
            if env in ("lstlisting", "minted") and cursor < len(body) and body[cursor] == "[":
                close = body.find("]", cursor + 1)
                if close > cursor:
                    opt = body[cursor + 1 : close]
                    cursor = close + 1
            if env == "minted" and cursor < len(body) and body[cursor] == "{":
                close = body.find("}", cursor + 1)
                if close > cursor:
                    minted_lang = body[cursor + 1 : close].strip()
                    cursor = close + 1
            if env in ("itemize", "enumerate"):
                end = _matching_end(body, cursor, env_raw)
            else:
                end = body.find(rf"\end{{{env_raw}}}", cursor)
            if end < 0:
                i = bm.end()
                continue
            inner = body[cursor:end]
            if env in ("verbatim", "lstlisting", "minted"):
                lang = minted_lang
                if not lang and opt:
                    found = re.search(r"language\s*=\s*([A-Za-z0-9_+.-]+)", opt)
                    if found:
                        lang = found.group(1)
                nodes.append(CodeBlock(content=inner.strip("\n"), lang=lang))
            elif env in ("quote", "quotation"):
                paragraphs = []
                for line in inner.splitlines():
                    rich = _rich_inline(line)
                    if rich.text:
                        paragraphs.append(rich)
                if paragraphs:
                    nodes.append(Blockquote(paragraphs=paragraphs))
            elif env in ("itemize", "enumerate"):
                nodes.extend(_list_items(inner, env == "enumerate", 0))
            elif env in ("equation", "align", "displaymath"):
                nodes.append(MathBlock(content=inner.strip(), display=True))
            elif env == "tabular":
                from aduport.exporters.table_span import parse_latex_tabular_block

                table = parse_latex_tabular_block(inner)
                if table:
                    nodes.append(table)
            i = end + len(rf"\end{{{env_raw}}}")
            continue

        sm = _SECTION_RE.match(body, i)
        if sm:
            level = {"section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}[
                sm.group(1)
            ]
            nodes.append(Heading(level=level, text=_rich_inline(sm.group(2))))
            i = sm.end()
            continue

        # Display math \[ ... \]
        if body.startswith(r"\[", i):
            end = body.find(r"\]", i + 2)
            if end > i:
                nodes.append(MathBlock(content=body[i + 2 : end].strip(), display=True))
                i = end + 2
                continue

        # Paragraph until next macro/env
        nxt = len(body)
        for pat in (_BEGIN_RE, _SECTION_RE):
            m2 = pat.search(body, i)
            if m2:
                nxt = min(nxt, m2.start())
        br = body.find(r"\[", i)
        if br >= 0:
            nxt = min(nxt, br)
        chunk = body[i:nxt]
        i = nxt
        if r"\rule" in chunk:
            nodes.append(HorizontalRule())
            chunk = re.sub(r"\\rule\s*\{[^{}]*\}\s*\{[^{}]*\}", " ", chunk)
        # Split blank lines
        for para in re.split(r"\n\s*\n", chunk):
            rich = _rich_inline(para.replace("\n", " "))
            if rich.text:
                nodes.append(Paragraph(text=rich))

    fn_ids = {fn.id for fn in footnotes}
    cleaned: list = []
    for node in nodes:
        if isinstance(node, Paragraph):
            raw_text = node.text.text if hasattr(node.text, "text") else str(node.text)
            lone = re.fullmatch(r"\[\^([^\]]+)\]", raw_text.strip())
            if lone and lone.group(1) in fn_ids:
                continue
        cleaned.append(node)
    nodes = cleaned
    nodes.extend(graphics)
    nodes.extend(footnotes)
    if not nodes and not title:
        raise InvalidDocumentError(f"未能从 LaTeX 解析出正文: {filename}")
    return DocumentModel(title=title or "", nodes=nodes)
