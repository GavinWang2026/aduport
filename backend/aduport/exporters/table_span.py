"""Shared table span helpers: native span renderers + roundtrip metadata."""

from __future__ import annotations

import json
import re

from aduport.core.document_model import Table, TableCell

_SPAN_COMMENT_PREFIX = "<!-- aduport-table:"


def table_has_span_cells(table: Table) -> bool:
    if not table.cells:
        return False
    for row in table.cells:
        for cell in row:
            if cell.covered or cell.row_span > 1 or cell.col_span > 1:
                return True
    return False


def table_has_cell_images(table: Table) -> bool:
    if not table.cells:
        return False
    return any(cell.images for row in table.cells for cell in row)


def table_flat_matrix(table: Table) -> list[list[str]]:
    if table.cells and (table_has_span_cells(table) or table_has_cell_images(table)):
        return [
            [
                ""
                if cell.covered
                else _cell_text_with_images(cell)
                for cell in row
            ]
            for row in table.cells
        ]
    if table.rows:
        return [
            [_escape_cell(str(c) if c is not None else "") for c in row]
            for row in table.rows
        ]
    if table.cells:
        return [[_cell_text_with_images(c) for c in row] for row in table.cells]
    return []


def _cell_text_with_images(cell: TableCell) -> str:
    parts = [_escape_cell(cell.text)] if cell.text else []
    for img in cell.images or []:
        alt = (img.alt_text or "image").split("|", 1)[0].replace("]", "")
        if img.data:
            import base64

            b64 = base64.b64encode(img.data).decode("ascii")
            mime = img.mime_type or "image/png"
            parts.append(f"![{alt}](data:{mime};base64,{b64})")
        elif img.url:
            parts.append(f"![{alt}]({img.url})")
        else:
            parts.append(f"[Image: {alt}]")
    return " ".join(parts)


def table_span_metadata_comment(table: Table) -> str | None:
    """Compact JSON in HTML comment for MD/TXT/LaTeX/RTF span roundtrip."""
    if not table.cells or not table_has_span_cells(table):
        return None
    payload = {
        "header_rows": int(table.header_rows or 0),
        "cells": [[_cell_payload(c) for c in row] for row in table.cells],
    }
    if table.name:
        payload["name"] = table.name
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"{_SPAN_COMMENT_PREFIX}{blob} -->"


def parse_table_span_comment(text: str) -> dict | None:
    text = (text or "").strip()
    if not text.startswith(_SPAN_COMMENT_PREFIX):
        return None
    body = text[len(_SPAN_COMMENT_PREFIX) :].strip()
    if body.endswith("-->"):
        body = body[:-3].strip()
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def apply_table_span_metadata(table: Table, meta: dict) -> None:
    raw_cells = meta.get("cells")
    if not isinstance(raw_cells, list):
        return
    cells: list[list[TableCell]] = []
    for row in raw_cells:
        if not isinstance(row, list):
            continue
        cells.append([_cell_from_payload(item) for item in row])
    if cells:
        table.cells = cells
        table.rows = table_flat_matrix(table)
    if "header_rows" in meta:
        table.header_rows = int(meta["header_rows"])
    if meta.get("name"):
        table.name = str(meta["name"])


def _cell_payload(cell: TableCell) -> dict:
    out: dict = {"text": cell.text or ""}
    if cell.covered:
        out["covered"] = True
    if cell.origin_row:
        out["origin_row"] = cell.origin_row
    if cell.origin_col:
        out["origin_col"] = cell.origin_col
    if cell.row_span > 1:
        out["row_span"] = cell.row_span
    if cell.col_span > 1:
        out["col_span"] = cell.col_span
    return out


def _cell_from_payload(item: dict | str) -> TableCell:
    if isinstance(item, str):
        return TableCell(text=item)
    return TableCell(
        text=str(item.get("text") or ""),
        covered=bool(item.get("covered")),
        origin_row=int(item.get("origin_row") or 0),
        origin_col=int(item.get("origin_col") or 0),
        row_span=int(item.get("row_span") or 1),
        col_span=int(item.get("col_span") or 1),
    )


def _escape_cell(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", "<br>")
    text = text.replace("|", "\\|")
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text


def render_table_html(table: Table) -> str:
    """Native colspan/rowspan via html_render (MD/TXT span tables)."""
    from aduport.exporters.html_render import _render_table_html

    return _render_table_html(table)


def render_table_latex(table: Table) -> str:
    """Native \\multicolumn / \\multirow for span tables."""
    if not table.cells:
        return ""
    cols = max(len(row) for row in table.cells)
    spec = "|" + "|".join(["c"] * cols) + "|"
    lines: list[str] = [
        r"% \usepackage{multirow}",
        rf"\begin{{tabular}}{{{spec}}}",
        r"\hline",
    ]
    for row in table.cells:
        parts: list[str] = []
        for cell in row:
            if cell.covered:
                continue
            text = _latex_escape(cell.text)
            if cell.col_span > 1:
                parts.append(rf"\multicolumn{{{cell.col_span}}}{{|c|}}{{{text}}}")
            elif cell.row_span > 1:
                parts.append(rf"\multirow{{{cell.row_span}}}{{*}}{{{text}}}")
            else:
                parts.append(text)
        lines.append(" & ".join(parts) + r" \\")
        lines.append(r"\hline")
    lines.append(r"\end{tabular}")
    return "\n".join(lines)


def render_table_rtf(table: Table) -> str:
    """RTF table rows with \\clmgf/\\clmrg horizontal merges."""
    if not table.cells:
        return ""
    unit = 3000
    parts: list[str] = []
    for row in table.cells:
        physical = [c for c in row if not c.covered]
        if not physical:
            continue
        row_def = [r"\trowd\trgaph108\trleft-108"]
        pos = unit
        cells_rtf: list[str] = []
        for cell in physical:
            span = max(1, int(cell.col_span or 1))
            pos += unit * span
            row_def.append(rf"\cellx{pos}")
            flags = r"\clmgf" + r"\clmrg" * (span - 1) if span > 1 else ""
            cells_rtf.append(rf"\pard\intbl{flags} {_rtf_escape(cell.text)}\cell")
            cells_rtf.extend(r"\cell" for _ in range(span - 1))
        parts.append("".join(row_def) + "".join(cells_rtf) + r"\row")
    return "".join(parts)


def _latex_escape(text: str) -> str:
    repl = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    out = text or ""
    for ch, esc in repl.items():
        out = out.replace(ch, esc)
    return out


def _rtf_escape(text: str) -> str:
    out: list[str] = []
    for ch in text or "":
        code = ord(ch)
        if ch in ("\\", "{", "}"):
            out.append("\\" + ch)
        elif code < 128:
            out.append(ch)
        else:
            if code > 32767:
                code -= 65536
            out.append(f"\\u{code}?")
    return "".join(out)


# ---------------------------------------------------------------------------
# Import: LaTeX tabular + RTF \trowd rows → Table with span cells
# ---------------------------------------------------------------------------

_LATEX_MC_RE = re.compile(r"^\\multicolumn\{(\d+)\}\{[^}]*\}\{(.*)\}\s*$", re.S)
_LATEX_MR_RE = re.compile(r"^\\multirow\{(\d+)\}\{[^}]*\}\{(.*)\}\s*$", re.S)
_RTF_ROW_RE = re.compile(r"\\trowd[\s\S]*?\\row")
_LATEX_TABULAR_SPEC_RE = re.compile(r"^\s*\{[^}]*\}")


def parse_latex_tabular_block(inner: str) -> Table | None:
    """Parse \\begin{tabular}…\\end{tabular} body into a span-aware Table."""
    inner = _LATEX_TABULAR_SPEC_RE.sub("", inner or "", count=1)
    grid: list[list[TableCell | None]] = []
    row_idx = -1
    for line in inner.splitlines():
        line = line.strip()
        if not line or line == r"\hline":
            continue
        if line.endswith(r"\\"):
            line = line[:-2].rstrip()
        if not line:
            continue
        row_idx += 1
        for token in _split_latex_row(line):
            col = _next_free_col(grid, row_idx)
            text, col_span, row_span = _parse_latex_cell(token)
            cell = TableCell(
                text=text,
                col_span=col_span,
                row_span=row_span,
                origin_row=row_idx,
                origin_col=col,
            )
            _place_cell(grid, row_idx, col, cell, col_span, row_span)
    if not grid:
        return None
    rows, cells = _grid_to_table(grid)
    return Table(rows=rows, cells=cells, kind="data")


def parse_rtf_table_from_rows(row_chunks: list[str]) -> Table | None:
    """Parse consecutive \\trowd…\\row blocks emitted by render_table_rtf."""
    if not row_chunks:
        return None
    grid: list[list[TableCell | None]] = []
    for row_idx, row_raw in enumerate(row_chunks):
        pending_span = 1
        for text, col_span in _parse_rtf_row_cells(row_raw):
            if not text and col_span == 1 and pending_span > 1:
                pending_span -= 1
                continue
            col = _next_free_col(grid, row_idx)
            cell = TableCell(
                text=text,
                col_span=col_span,
                origin_row=row_idx,
                origin_col=col,
            )
            _place_cell(grid, row_idx, col, cell, col_span, 1)
            pending_span = col_span
    if not grid:
        return None
    rows, cells = _grid_to_table(grid)
    return Table(rows=rows, cells=cells, kind="data")


def split_rtf_segments(raw: str) -> list[tuple[str, str]]:
    """Split RTF into ('text', chunk) and ('table_row', chunk) segments."""
    parts = re.split(r"(\\trowd[\s\S]*?\\row)", raw)
    segments: list[tuple[str, str]] = []
    for part in parts:
        if not part:
            continue
        if part.startswith(r"\trowd"):
            segments.append(("table_row", part))
        else:
            segments.append(("text", part))
    return segments


def _split_latex_row(line: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in line:
        if ch == "{":
            depth += 1
            buf.append(ch)
        elif ch == "}":
            depth -= 1
            buf.append(ch)
        elif ch == "&" and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return parts


def _parse_latex_cell(token: str) -> tuple[str, int, int]:
    token = token.strip()
    m = _LATEX_MC_RE.match(token)
    if m:
        return _latex_unescape(m.group(2)), max(1, int(m.group(1))), 1
    m = _LATEX_MR_RE.match(token)
    if m:
        return _latex_unescape(m.group(2)), 1, max(1, int(m.group(1)))
    return _latex_unescape(token), 1, 1


def _parse_rtf_row_cells(row_raw: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    idx = 0
    marker = r"\pard\intbl"
    while True:
        start = row_raw.find(marker, idx)
        if start < 0:
            break
        pos = start + len(marker)
        col_span = 1
        if row_raw.startswith(r"\clmgf", pos):
            col_span = 1
            pos += len(r"\clmgf")
            while row_raw.startswith(r"\clmrg", pos):
                col_span += 1
                pos += len(r"\clmrg")
        end = row_raw.find(r"\cell", pos)
        if end < 0:
            break
        text = _rtf_unescape(row_raw[pos:end].strip())
        out.append((text, col_span))
        idx = end + len(r"\cell")
    return out


def _latex_unescape(text: str) -> str:
    repl = {
        r"\textbackslash{}": "\\",
        r"\&": "&",
        r"\%": "%",
        r"\$": "$",
        r"\#": "#",
        r"\_": "_",
        r"\{": "{",
        r"\}": "}",
        r"\textasciitilde{}": "~",
        r"\textasciicircum{}": "^",
    }
    out = text or ""
    for src, dst in repl.items():
        out = out.replace(src, dst)
    return out.strip()


def _rtf_unicode_char(raw: str) -> str:
    """RTF ``\\uN`` stores a signed 16-bit value. Negative N is ``N + 65536``."""
    try:
        code = int(raw)
    except ValueError:
        return "\ufffd"
    if code < 0:
        code += 65536
    if code < 0 or code > 0x10FFFF:
        return "\ufffd"
    return chr(code)


def _rtf_unescape(text: str) -> str:
    out = re.sub(
        r"\\'([0-9a-fA-F]{2})",
        lambda m: chr(int(m.group(1), 16)),
        text or "",
    )
    out = re.sub(
        r"\\u(-?\d+)\?",
        lambda m: _rtf_unicode_char(m.group(1)),
        out,
    )
    out = out.replace(r"\{", "{").replace(r"\}", "}").replace(r"\\", "\\")
    out = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", out)
    return out.strip()


def _next_free_col(grid: list[list[TableCell | None]], row: int) -> int:
    while len(grid) <= row:
        grid.append([])
    cols = grid[row]
    c = 0
    while c < len(cols) and cols[c] is not None:
        c += 1
    return c


def _place_cell(
    grid: list[list[TableCell | None]],
    row: int,
    col: int,
    cell: TableCell,
    col_span: int,
    row_span: int,
) -> None:
    for dr in range(row_span):
        for dc in range(col_span):
            rr, cc = row + dr, col + dc
            while len(grid) <= rr:
                grid.append([])
            while len(grid[rr]) <= cc:
                grid[rr].append(None)
            if dr == 0 and dc == 0:
                grid[rr][cc] = cell
            else:
                grid[rr][cc] = TableCell(covered=True, origin_row=row, origin_col=col)


def _grid_to_table(
    grid: list[list[TableCell | None]],
) -> tuple[list[list[str]], list[list[TableCell]]]:
    rows: list[list[str]] = []
    cells: list[list[TableCell]] = []
    for row in grid:
        row_strs: list[str] = []
        row_cells: list[TableCell] = []
        for slot in row:
            cell = slot if slot is not None else TableCell(covered=True)
            row_cells.append(cell)
            row_strs.append("" if cell.covered else cell.text)
        if row_cells:
            rows.append(row_strs)
            cells.append(row_cells)
    return rows, cells
