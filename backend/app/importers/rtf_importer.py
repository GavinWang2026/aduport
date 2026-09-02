"""Native RTF import fallback (control-word strip). Prefer anydoc when available."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.document_model import DocumentModel, Paragraph, RichText


_CTRL = re.compile(
    r"\\([a-zA-Z]+)(-?\d+)? ?"  # control word
    r"|\\'([0-9a-fA-F]{2})"  # hex char
    r"|\\[{}\\]"  # escaped
    r"|[{}]"  # groups
    r"|\\[^a-zA-Z]"  # other escapes
)


def _strip_rtf(raw: str) -> str:
    # Drop font/color tables roughly by skipping nested groups starting with \*
    text = re.sub(r"\{\\\*[^{}]*\}", " ", raw)
    out: list[str] = []
    i = 0
    while i < len(text):
        m = _CTRL.match(text, i)
        if m:
            if m.group(1) in ("par", "line", "page"):
                out.append("\n")
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


def import_rtf(source_bytes: bytes, filename: str = "input.rtf") -> DocumentModel:
    try:
        raw = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raw = source_bytes.decode("latin-1", errors="ignore")

    from app.exporters.table_span import parse_rtf_table_from_rows, split_rtf_segments

    nodes: list = []
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
        text = _strip_rtf(chunk)
        for para in text.split("\n\n"):
            para = para.strip()
            if para:
                nodes.append(Paragraph(text=RichText(text=para)))

    if table_buffer:
        table = parse_rtf_table_from_rows(table_buffer)
        if table:
            nodes.append(table)

    if not nodes:
        text = _strip_rtf(raw)
        if text:
            nodes = [Paragraph(text=RichText(text=p.strip())) for p in text.split("\n\n") if p.strip()]
        if not nodes and text:
            nodes = [Paragraph(text=RichText(text=text))]

    return DocumentModel(
        title=Path(filename).stem,
        nodes=nodes or [Paragraph(text=RichText(text=""))],
        metadata={"importer": "rtf_native"},
    )
