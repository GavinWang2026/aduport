"""CSV → DocumentModel (single or multi-table blocks)."""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from aduport.core.document_model import DocumentModel, Table, TableCell
from aduport.core.exceptions import InvalidDocumentError
from aduport.core.text_decode import decode_text_bytes


def _blank_row(row: list[str]) -> bool:
    return not row or (len(row) == 1 and not str(row[0]).strip())


def _content_marker(row: list[str]) -> bool:
    return len(row) == 1 and str(row[0]).strip() == "_CONTENT"


def _content_banner(row: list[str]) -> bool:
    """Human label after ``_CONTENT``; must not rehydrate as a fake H1 (v7 P2)."""
    if len(row) != 1:
        return False
    cell = str(row[0]).strip()
    # Accept legacy md-bridge banners so old CSV exports still skip the fake H1.
    return (
        cell.startswith("# [AduPort]")
        or cell.startswith("[AduPort]")
        or cell.startswith("# [md-bridge]")
        or cell.startswith("[md-bridge]")
    )


def _markdown_column(rows: list[list[str]]) -> str | None:
    """Single-column Markdown, or a sentence column that is not a grid."""
    from aduport.importers.markdown_rehydrate import looks_structural

    if not rows or not all(len(row) <= 1 for row in rows):
        return None
    lines = [str(row[0]) if row else "" for row in rows]
    blob = "\n".join(lines)
    if looks_structural(blob) or _looks_like_prose(lines):
        return blob
    return None


def _looks_like_prose(lines: list[str]) -> bool:
    """A column of sentences, not ``Name / Ada / 42``."""
    body = [line.strip() for line in lines if line.strip()]
    if not body:
        return False
    hits = 0
    for line in body:
        if len(line) >= 16 or any(ch in line for ch in "。！？；，,.!?;:"):
            hits += 1
    return hits >= 1 and hits * 2 >= len(body)


def _table_node(rows: list[list[str]]) -> Table:
    cells = [
        [TableCell(text=str(cell or ""), origin_row=r, origin_col=c) for c, cell in enumerate(row)]
        for r, row in enumerate(rows)
    ]
    return Table(rows=rows, cells=cells, header_rows=1 if len(rows) > 1 else 0)


def _nodes_from_rows(rows: list[list[str]]) -> list:
    from aduport.importers.markdown_rehydrate import parse_markdown_nodes

    marker = next((i for i, row in enumerate(rows) if _content_marker(row)), None)
    head = rows if marker is None else rows[:marker]
    tail = [] if marker is None else rows[marker + 1 :]
    # Drop the exporter banner so round-trip does not invent an H1.
    while tail and (_blank_row(tail[0]) or _content_banner(tail[0])):
        if _content_banner(tail[0]):
            tail = tail[1:]
            break
        tail = tail[1:]
    nodes: list = []
    block: list[list[str]] = []

    def flush() -> None:
        nonlocal block
        kept = [row for row in block if not _blank_row(row)]
        block = []
        if not kept:
            return
        prose = _markdown_column(kept)
        if prose is not None:
            nodes.extend(parse_markdown_nodes(prose))
            return
        nodes.append(_table_node(kept))

    for row in head:
        if _blank_row(row):
            flush()
            continue
        block.append(row)
    flush()
    if tail:
        prose = "\n".join(
            ""
            if _blank_row(row) or _content_banner(row)
            else str(row[0] if len(row) == 1 else ",".join(row))
            for row in tail
        )
        if prose.strip():
            nodes.extend(parse_markdown_nodes(prose))
    return nodes


def import_csv(source_bytes: bytes, filename: str = "input.csv") -> DocumentModel:
    text = decode_text_bytes(source_bytes, filename=filename)
    rows = [list(row) for row in csv.reader(StringIO(text))]
    nodes = _nodes_from_rows(rows)
    if not nodes:
        raise InvalidDocumentError(f"CSV 无数据行: {filename}")
    return DocumentModel(title=Path(filename).stem, nodes=nodes)
