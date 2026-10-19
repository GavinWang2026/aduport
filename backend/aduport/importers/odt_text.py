"""Lightweight ODT (OpenDocument Text) extract — content.xml → DocumentModel."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from aduport.core import xml_safe
from aduport.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    ListItem,
    MathBlock,
    Paragraph,
    PivotTable,
    Revision,
    Table,
    TableCell,
    TextBox,
)
from aduport.core.exceptions import FileTooLargeError
from aduport.core.safe_zip import ZipInflateBudget
from aduport.core.title_source import resolve_title

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
}


def _promote_inline(text: str):
    """ODT stores bold/italic/code as visible Markdown characters."""
    from aduport.importers.markdown_rehydrate import promote_inline

    return promote_inline(text)


def try_extract_odt_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return None
    names = set(zf.namelist())
    if "content.xml" not in names:
        zf.close()
        return None
    try:
        budget = ZipInflateBudget()
        root = xml_safe.fromstring(budget.read(zf, "content.xml"))
        object_tex = _load_object_tex(zf, budget)
    except (ET.ParseError, FileTooLargeError, KeyError):
        zf.close()
        return None
    zf.close()

    body = root.find(".//office:body/office:text", _NS)
    if body is None:
        body = root.find(".//{urn:oasis:names:tc:opendocument:xmlns:office:1.0}text")
    if body is None:
        return None

    nodes: list = []
    quote_buf: list[str] = []

    def _flush_quote() -> None:
        if quote_buf:
            nodes.append(Blockquote(paragraphs=list(quote_buf)))
            quote_buf.clear()

    for el in list(body):
        tag = el.tag.rsplit("}", 1)[-1]
        style = _attr(el, "text", "style-name")
        if tag == "p" and style == "AduPortQuote":
            text = _elem_text(el, object_tex=object_tex).strip()
            if text:
                quote_buf.append(text)
            continue
        _flush_quote()
        if tag == "p" and style == "AduPortCode":
            lang = ""
            for key, val in el.attrib.items():
                if key.endswith("lang") and "aduport" in key:
                    lang = val or ""
                    break
            nodes.append(
                CodeBlock(lang=lang, content=_elem_text(el, object_tex=object_tex).rstrip("\n"))
            )
            continue
        if tag == "h":
            level = 1
            lvl = el.get(f"{{{_NS['text']}}}outline-level") or el.get("text:outline-level")
            if lvl and str(lvl).isdigit():
                level = max(1, min(6, int(lvl)))
            text = _elem_text(el, object_tex=object_tex).strip()
            if text:
                nodes.append(Heading(level=level, text=_promote_inline(text)))
        elif tag == "p":
            para_nodes = _nodes_from_paragraph(el, object_tex=object_tex)
            if para_nodes:
                nodes.extend(para_nodes)
        elif tag == "table":
            table = _parse_table(el)
            if table:
                nodes.append(table)
        elif tag == "list":
            nodes.extend(_list_items(el, 0))

    _flush_quote()
    if not nodes:
        return None
    return DocumentModel(
        title=resolve_title(title, nodes, filename=f"{title}.odt" if title else ""),
        nodes=nodes,
    )


def _list_items(el: ET.Element, level: int) -> list:
    style = _attr(el, "text", "style-name")
    low = style.casefold()
    ordered = "unordered" not in low and "ordered" in low
    nodes: list = []
    item_tag = f"{{{_NS['text']}}}list-item"
    list_tag = f"{{{_NS['text']}}}list"
    para_tag = f"{{{_NS['text']}}}p"
    for item in el.findall(item_tag):
        texts = [
            _elem_text(child).strip()
            for child in item.findall(para_tag)
            if _elem_text(child).strip()
        ]
        text = "\n".join(texts).strip()
        if text:
            checked = None
            if text.startswith("[x] "):
                checked = True
                text = text[4:]
            elif text.startswith("[ ] "):
                checked = False
                text = text[4:]
            nodes.append(
                ListItem(
                    text=_promote_inline(text),
                    ordered=ordered,
                    checked=checked,
                    level=level,
                )
            )
        for child in item.findall(list_tag):
            nodes.extend(_list_items(child, level + 1))
    return nodes


def _parse_meta_paragraph(text: str):
    """Map ODT exporter bracket lines back to AST meta nodes."""
    if m := re.match(r"^\[Comment\s+(.+?)\]\s*(.*)$", text, re.DOTALL):
        head, body = m.group(1).strip(), m.group(2).strip()
        anchor = ""
        author = head
        if am := re.match(r"^(.+?)\s+\((.+)\)$", head):
            author, anchor = am.group(1).strip(), am.group(2).strip()
        return Comment(author=author or "reviewer", anchor=anchor, text=body)
    if m := re.match(r"^\[\^(?P<id>[^\]]+)\]:?\s*(?P<body>.*)$", text, re.DOTALL):
        body = m.group("body").strip()
        if body.startswith(":"):
            body = body[1:].strip()
        return Footnote(id=m.group("id"), text=body)
    if m := re.match(r"^\[(?P<tag>ins|del)\s+(?P<author>[^\]]*)\]\s*(?P<body>.*)$", text, re.DOTALL):
        change = "insert" if m.group("tag") == "ins" else "delete"
        return Revision(change=change, author=m.group("author").strip(), text=m.group("body").strip())
    if m := re.match(r"^\[Control:(?P<label>[^\]|]+)(?:\|(?P<ctype>[^\]]+))?\]\s*(?P<body>.*)$", text, re.DOTALL):
        label = m.group("label").strip()
        return ContentControl(
            tag=label.lower().replace(" ", "_"),
            alias=label,
            control_type=(m.group("ctype") or "richText").strip(),
            text=m.group("body").strip(),
        )
    if m := re.match(r"^\[TextBox(?:\s+\((?P<source>[^)]+)\))?\]\s*(?P<body>.*)$", text, re.DOTALL):
        return TextBox(text=m.group("body").strip(), source=(m.group("source") or "").strip())
    if m := re.match(r"^\[PivotTable\s+(?P<name>[^\]]+)\]\s*(?P<fields>.*)$", text, re.DOTALL):
        fields = [f.strip() for f in m.group("fields").split(",") if f.strip()]
        return PivotTable(name=m.group("name").strip(), row_fields=fields)
    return None


def _attr(el: ET.Element, ns_key: str, local: str) -> str:
    return el.get(f"{{{_NS[ns_key]}}}{local}") or el.get(f"{ns_key}:{local}") or ""


def _int_attr(el: ET.Element, ns_key: str, local: str, default: int = 1) -> int:
    raw = _attr(el, ns_key, local)
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _parse_table(table_el: ET.Element) -> Table | None:
    name = _attr(table_el, "table", "name")
    row_els = table_el.findall(f".//{{{_NS['table']}}}table-row")
    if not row_els:
        return None

    cells_grid: list[list[TableCell]] = []
    rows: list[list[str]] = []

    for row_el in row_els:
        row_cells: list[TableCell] = []
        row_strs: list[str] = []
        for cell_el in row_el:
            tag = cell_el.tag.rsplit("}", 1)[-1]
            if tag == "covered-table-cell":
                row_cells.append(TableCell(covered=True))
                row_strs.append("")
            elif tag == "table-cell":
                text = _elem_text(cell_el).strip()
                col_span = _int_attr(cell_el, "table", "number-columns-spanned")
                row_span = _int_attr(cell_el, "table", "number-rows-spanned")
                row_cells.append(
                    TableCell(text=text, col_span=col_span, row_span=row_span)
                )
                row_strs.append(text)
        if row_cells:
            cells_grid.append(row_cells)
            rows.append(row_strs)

    if not rows:
        return None

    _resolve_covered_origins(cells_grid)
    for r_idx, row in enumerate(cells_grid):
        for c_idx, cell in enumerate(row):
            if not cell.covered:
                cell.origin_row = r_idx
                cell.origin_col = c_idx

    has_span = any(
        cell.covered or cell.col_span > 1 or cell.row_span > 1
        for row in cells_grid
        for cell in row
    )
    if has_span:
        return Table(rows=rows, cells=cells_grid, name=name)
    return Table(rows=rows, name=name)


def _find_span_origin(cells: list[list[TableCell]], r: int, c: int) -> tuple[int, int]:
    for cc in range(c - 1, -1, -1):
        if cc >= len(cells[r]):
            continue
        origin = cells[r][cc]
        if not origin.covered and origin.col_span > (c - cc):
            return (r, cc)
    for rr in range(r - 1, -1, -1):
        if c >= len(cells[rr]):
            continue
        origin = cells[rr][c]
        if not origin.covered and origin.row_span > (r - rr):
            return (rr, c)
    return (r, c)


def _resolve_covered_origins(cells: list[list[TableCell]]) -> None:
    for r_idx, row in enumerate(cells):
        for c_idx, cell in enumerate(row):
            if not cell.covered:
                continue
            origin_r, origin_c = _find_span_origin(cells, r_idx, c_idx)
            cell.origin_row = origin_r
            cell.origin_col = origin_c


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _load_object_tex(zf: zipfile.ZipFile, budget: ZipInflateBudget) -> dict[str, str]:
    """Map ``Object 1`` / ``./Object 1`` → TeX from embedded formula packages."""
    out: dict[str, str] = {}
    for name in zf.namelist():
        if name == "content.xml" or not name.endswith("content.xml"):
            continue
        try:
            root = xml_safe.fromstring(budget.read(zf, name))
        except (ET.ParseError, FileTooLargeError, KeyError, zipfile.BadZipFile):
            continue
        tex = _tex_from_math_tree(root)
        if not tex:
            continue
        key = name.split("/", 1)[0]
        out[key] = tex
        out[f"./{key}"] = tex
    return out


def _tex_from_math_tree(el: ET.Element) -> str:
    """TeX annotation wins; otherwise a small MathML subset (frac/sqrt/sup)."""
    for child in el.iter():
        if _local(child.tag) != "annotation":
            continue
        encoding = (child.get("encoding") or "").casefold()
        if "tex" not in encoding:
            continue
        body = "".join(child.itertext()).strip()
        if body:
            return body
    rendered = _mathml_to_tex(el).strip()
    return rendered


def _mathml_to_tex(el: ET.Element) -> str:
    tag = _local(el.tag)
    kids = [c for c in list(el) if isinstance(c.tag, str)]
    text = (el.text or "").strip()
    if tag == "annotation":
        return ""
    if tag in ("math", "semantics", "mrow", "mstyle"):
        return "".join(_mathml_to_tex(c) for c in kids if _local(c.tag) != "annotation")
    if tag in ("mi", "mn", "mtext"):
        return text
    if tag == "mo":
        return text
    if tag == "mfrac" and len(kids) >= 2:
        return f"\\frac{{{_mathml_to_tex(kids[0])}}}{{{_mathml_to_tex(kids[1])}}}"
    if tag == "msqrt":
        inner = "".join(_mathml_to_tex(c) for c in kids)
        return f"\\sqrt{{{inner}}}"
    if tag == "msup" and len(kids) >= 2:
        return f"{_mathml_to_tex(kids[0])}^{{{_mathml_to_tex(kids[1])}}}"
    if tag == "msub" and len(kids) >= 2:
        return f"{_mathml_to_tex(kids[0])}_{{{_mathml_to_tex(kids[1])}}}"
    return "".join(_mathml_to_tex(c) for c in kids) or text


def _object_href(el: ET.Element) -> str:
    for child in el.iter():
        if _local(child.tag) != "object":
            continue
        href = (
            child.get("{http://www.w3.org/1999/xlink}href")
            or child.get("href")
            or ""
        )
        if href:
            return href.strip()
    return ""


def _inline_math_tex(el: ET.Element, object_tex: dict[str, str] | None) -> tuple[str, bool]:
    """Return (tex, display) for a frame/object/math element, else ("", False)."""
    href = _object_href(el)
    if href and object_tex:
        key = href[2:] if href.startswith("./") else href
        tex = object_tex.get(href) or object_tex.get(key) or ""
        if tex:
            return tex, True
    for child in el.iter():
        if _local(child.tag) != "math":
            continue
        tex = _tex_from_math_tree(child)
        if not tex:
            continue
        display = (child.get("display") or "").casefold() == "block"
        return tex, display
    return "", False


def _nodes_from_paragraph(
    el: ET.Element, *, object_tex: dict[str, str] | None = None
) -> list:
    """Split a paragraph around formula objects into text and MathBlock nodes."""
    pieces: list[tuple] = []
    buf: list[str] = []

    def flush() -> None:
        text = "".join(buf).strip()
        buf.clear()
        if text:
            pieces.append(("text", text))

    if el.text:
        buf.append(el.text)
    for child in el:
        tex, display = _inline_math_tex(child, object_tex)
        if tex:
            flush()
            pieces.append(("math", tex, display))
        else:
            buf.append(_elem_text(child, object_tex=object_tex))
        if child.tail:
            buf.append(child.tail)
    flush()
    if not pieces:
        return []
    if len(pieces) == 1 and pieces[0][0] == "math":
        return [MathBlock(content=pieces[0][1], display=True)]
    nodes: list = []
    for piece in pieces:
        if piece[0] == "text":
            meta = _parse_meta_paragraph(piece[1])
            nodes.append(meta if meta is not None else Paragraph(text=_promote_inline(piece[1])))
        else:
            nodes.append(MathBlock(content=piece[1], display=bool(piece[2])))
    return nodes


_DOLLAR_MATH = re.compile(r"\$([^$\n]+)\$")


def lift_dollar_math(model: DocumentModel) -> DocumentModel:
    """Turn anydoc's inline ``$...$`` into MathBlock nodes when none exist yet."""
    if any(getattr(node, "type", "") == "math_block" for node in model.nodes):
        return model
    out: list = []
    changed = False
    for node in model.nodes:
        text = node.plain_text() if hasattr(node, "plain_text") else ""
        if getattr(node, "type", "") != "paragraph" or "$" not in text:
            out.append(node)
            continue
        matches = list(_DOLLAR_MATH.finditer(text))
        if not matches:
            out.append(node)
            continue
        changed = True
        cursor = 0
        for match in matches:
            before = text[cursor : match.start()].strip()
            if before:
                out.append(Paragraph(text=before))
            out.append(MathBlock(content=match.group(1).strip(), display=False))
            cursor = match.end()
        after = text[cursor:].strip()
        if after:
            out.append(Paragraph(text=after))
    if not changed:
        return model
    model.nodes = out
    meta = model.metadata if isinstance(model.metadata, dict) else {}
    meta["odt_math"] = "lifted"
    model.metadata = meta
    return model


def _math_from_paragraph(el: ET.Element) -> MathBlock | None:
    """Prefer the TeX annotation. MathML also stores the same string as mtext."""
    nodes = _nodes_from_paragraph(el)
    for node in nodes:
        if isinstance(node, MathBlock):
            return node
    return None


def _elem_text(el: ET.Element, *, object_tex: dict[str, str] | None = None) -> str:
    parts: list[str] = []
    if el.text:
        parts.append(el.text)
    for child in el:
        ctag = child.tag.rsplit("}", 1)[-1]
        if ctag == "line-break":
            parts.append("\n")
        elif ctag == "s":
            n = child.get(f"{{{_NS['text']}}}c") or child.get("text:c") or "1"
            parts.append(" " * max(1, int(n) if str(n).isdigit() else 1))
        elif ctag == "tab":
            parts.append("\t")
        elif ctag == "annotation":
            continue
        elif ctag in ("frame", "object", "math"):
            tex, _display = _inline_math_tex(child, object_tex)
            if tex:
                parts.append(f"${tex}$")
            elif ctag != "math":
                parts.append(_elem_text(child, object_tex=object_tex))
        elif ctag == "a":
            href = (
                child.get("{http://www.w3.org/1999/xlink}href")
                or child.get("xlink:href")
                or ""
            )
            inner = _elem_text(child, object_tex=object_tex)
            parts.append(f"[{inner}]({href})" if href else inner)
        else:
            parts.append(_elem_text(child, object_tex=object_tex))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _table_rows(table_el: ET.Element) -> list[list[str]]:
    """Legacy flat rows helper (span metadata discarded)."""
    table = _parse_table(table_el)
    return table.rows if table else []
