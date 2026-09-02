"""Import a subset of LaTeX article sources into DocumentModel.

Supports common article macros produced by ``export_latex`` and similar
minimal documents: ``\\section`` / ``\\subsection``, paragraphs, itemize/
enumerate, verbatim, display math. Not a full TeX engine.
"""

from __future__ import annotations

import re

from app.core.document_model import (
    CodeBlock,
    DocumentModel,
    Heading,
    ListItem,
    MathBlock,
    Paragraph,
    Table,
)
from app.core.exceptions import InvalidDocumentError


_SECTION_RE = re.compile(
    r"\\(section|subsection|subsubsection|paragraph)\*?\{([^{}]*)\}"
)
_BEGIN_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}")
_END_RE = re.compile(r"\\end\{([a-zA-Z*]+)\}")
_ITEM_RE = re.compile(r"\\item\b\s*")
_TITLE_RE = re.compile(r"\\title\{([^{}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")


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


def import_latex(source_bytes: bytes, filename: str = "input.tex") -> DocumentModel:
    try:
        text = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = source_bytes.decode("latin-1", errors="replace")
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

    nodes: list = []
    i = 0
    while i < len(body):
        # Environments
        bm = _BEGIN_RE.match(body, i)
        if bm:
            env = bm.group(1).rstrip("*")
            end = body.find(rf"\end{{{bm.group(1)}}}", bm.end())
            if end < 0:
                i = bm.end()
                continue
            inner = body[bm.end() : end]
            if env in ("verbatim", "lstlisting", "minted"):
                nodes.append(CodeBlock(content=inner.strip("\n"), language=""))
            elif env in ("itemize", "enumerate"):
                ordered = env == "enumerate"
                for chunk in _ITEM_RE.split(inner):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    nodes.append(
                        ListItem(text=_unescape(chunk), ordered=ordered, level=0)
                    )
            elif env in ("equation", "align", "displaymath"):
                nodes.append(MathBlock(content=inner.strip(), display=True))
            elif env == "tabular":
                from app.exporters.table_span import parse_latex_tabular_block

                table = parse_latex_tabular_block(inner)
                if table:
                    nodes.append(table)
            i = end + len(rf"\end{{{bm.group(1)}}}")
            continue

        sm = _SECTION_RE.match(body, i)
        if sm:
            level = {"section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}[
                sm.group(1)
            ]
            nodes.append(Heading(level=level, text=_unescape(sm.group(2))))
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
        # Split blank lines
        for para in re.split(r"\n\s*\n", chunk):
            plain = _unescape(para.replace("\n", " "))
            if plain:
                nodes.append(Paragraph(text=plain))

    if not nodes and not title:
        raise InvalidDocumentError(f"未能从 LaTeX 解析出正文: {filename}")
    return DocumentModel(title=title or "", nodes=nodes)
