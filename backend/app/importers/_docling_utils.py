"""Shared utilities for converting Docling documents to DocumentModel."""

from __future__ import annotations

import base64
import re
from io import BytesIO
from typing import Any

from app.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    Image,
    ListItem,
    MathBlock,
    Paragraph,
    RichText,
    Table,
    TableCell,
)


def _extract_text(item: Any) -> str:
    if hasattr(item, "text") and item.text:
        return str(item.text).strip()
    if hasattr(item, "orig") and item.orig:
        return str(item.orig).strip()
    return ""


def _heading_level(item: Any) -> int:
    label = getattr(item, "label", None)
    if label is not None:
        name = str(label).lower()
        for level in range(1, 7):
            if f"heading_{level}" in name or ("title" in name and level == 1):
                return level
        if "section_header" in name:
            return 2
    level = getattr(item, "level", None)
    if isinstance(level, int) and 1 <= level <= 6:
        return level
    return 1


def _is_list_item(item: Any) -> bool:
    label = str(getattr(item, "label", "")).lower()
    return "list" in label or "list_item" in label


def _is_code(item: Any) -> bool:
    label = str(getattr(item, "label", "")).lower()
    return "code" in label


def _table_from_item(item: Any) -> Table | None:
    data = getattr(item, "data", None)
    if data is None and hasattr(item, "table"):
        data = item.table
    if data is None:
        return None

    grid = getattr(data, "grid", None) or getattr(data, "table_cells", None)
    if grid is None:
        return None

    rows: list[list[str]] = []
    cell_grid: list[list[TableCell]] = []
    try:
        if hasattr(data, "export_to_dataframe"):
            df = data.export_to_dataframe()
            rows = [[str(c) for c in row] for row in df.values.tolist()]
            if df.columns is not None:
                header = [str(c) for c in df.columns.tolist()]
                if header:
                    rows.insert(0, header)
            cell_grid = [
                [TableCell(text=c) for c in row] for row in rows
            ]
        elif isinstance(grid, list):
            for r_idx, row in enumerate(grid):
                if isinstance(row, list):
                    texts = [_cell_text(c) for c in row]
                    cells_row = [_cell_to_table_cell(c) for c in row]
                else:
                    texts = [_cell_text(row)]
                    cells_row = [_cell_to_table_cell(row)]
                rows.append(texts)
                cell_grid.append(cells_row)
    except Exception:
        return None

    if not rows:
        return None
    # Keep cell grid when present; never stamp self-origin (HTML header-switch illusion).
    return Table(
        rows=rows,
        cells=cell_grid,
        header_rows=1 if len(rows) > 1 else 0,
    )


def _cell_to_table_cell(cell: Any) -> TableCell:
    text = _cell_text(cell)
    row_span = int(getattr(cell, "row_span", None) or getattr(cell, "rowspan", None) or 1)
    col_span = int(getattr(cell, "col_span", None) or getattr(cell, "colspan", None) or 1)
    covered = bool(getattr(cell, "covered", False) or getattr(cell, "is_covered", False))
    return TableCell(
        text=text,
        row_span=max(1, row_span),
        col_span=max(1, col_span),
        covered=covered,
    )


def _cell_text(cell: Any) -> str:
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell.strip()
    if hasattr(cell, "text"):
        return str(cell.text).strip()
    return str(cell).strip()


def _image_from_item(item: Any) -> Image | None:
    image_obj = getattr(item, "image", None)
    if image_obj is None:
        return None
    try:
        buf = BytesIO()
        if hasattr(image_obj, "save"):
            image_obj.save(buf, format="PNG")
            data = buf.getvalue()
        elif isinstance(image_obj, bytes):
            data = image_obj
        else:
            return None
        alt = _extract_text(item) or "image"
        return Image(data=data, alt_text=alt, mime_type="image/png")
    except Exception:
        return None


def docling_document_to_model(doc: Any, title: str = "") -> DocumentModel:
    """Traverse a DoclingDocument and build our DocumentModel."""
    model = DocumentModel(title=title, nodes=[])

    body = getattr(doc, "body", None)
    items: list[Any] = []
    if body is not None and hasattr(body, "children"):
        items = list(body.children)
    elif hasattr(doc, "texts"):
        items = list(doc.texts)
    elif hasattr(doc, "iterate_items"):
        items = [item for item, _ in doc.iterate_items()]

    for item in items:
        node = _item_to_node(item)
        if node is not None:
            model.nodes.append(node)

    if not model.nodes and hasattr(doc, "export_to_markdown"):
        md = doc.export_to_markdown()
        model = _markdown_fallback(md, title)

    if model.metadata is None:
        model.metadata = {}
    model.metadata.setdefault("importer", "docling")
    model.metadata.setdefault("pdf_pipeline", "docling")
    return model


def _item_to_node(item: Any):
    label = str(getattr(item, "label", "")).lower()

    if "table" in label:
        table = _table_from_item(item)
        if table:
            return table

    if "picture" in label or "figure" in label or "image" in label:
        img = _image_from_item(item)
        if img:
            return img

    if "page_break" in label or label in ("pagebreak",):
        return HorizontalRule()

    text = _extract_text(item)
    if not text and "formula" not in label and "equation" not in label:
        return None

    if _is_code(item):
        lang = getattr(item, "code_language", "") or ""
        return CodeBlock(lang=str(lang), content=text)

    if _is_list_item(item):
        ordered = "ordered" in label or "enumeration" in label
        level = getattr(item, "level", 0) or 0
        return ListItem(text=RichText(text=text), ordered=ordered, level=int(level))

    if "heading" in label or "title" in label or "section_header" in label:
        return Heading(level=_heading_level(item), text=RichText(text=text))

    if "footnote" in label or "endnote" in label:
        fid = str(getattr(item, "id", "") or getattr(item, "marker", "") or "1")
        return Footnote(id=fid, text=text)

    if "formula" in label or "equation" in label:
        return MathBlock(content=text or str(getattr(item, "latex", "") or ""))

    if "caption" in label:
        return Paragraph(text=RichText(text=text))

    if "quote" in label or "blockquote" in label:
        return Blockquote(paragraphs=[text] if text else [])

    if "checkbox" in label or "check_box" in label:
        checked = bool(getattr(item, "checked", False) or "checked" in label)
        return ListItem(text=RichText(text=text), ordered=False, checked=checked, level=0)

    if "key_value" in label or "form" in label:
        key = str(getattr(item, "key", "") or "").strip()
        val = str(getattr(item, "value", "") or text).strip()
        joined = f"{key}: {val}" if key else val
        return Paragraph(text=RichText(text=joined))

    if "paragraph" in label or label.endswith("text") or not label:
        return Paragraph(text=RichText(text=text))

    # Unknown labels still become paragraphs so layout text is not dropped
    # (avoids 「弱文本层 → 2 个 Paragraph」total loss when structure is partial).
    return Paragraph(text=RichText(text=text))


def _markdown_fallback(md: str, title: str) -> DocumentModel:
    """Parse basic markdown structure when structured traversal yields nothing."""
    model = DocumentModel(title=title, nodes=[])
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            model.nodes.append(
                Heading(level=level, text=RichText(text=heading_match.group(2)))
            )
            i += 1
            continue

        if line.strip().startswith("```"):
            lang = line.strip()[3:].strip()
            content_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                content_lines.append(lines[i])
                i += 1
            model.nodes.append(CodeBlock(lang=lang, content="\n".join(content_lines)))
            i += 1
            continue

        list_match = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.+)$", line)
        if list_match:
            ordered = list_match.group(2)[-1] == "."
            indent = len(list_match.group(1))
            model.nodes.append(
                ListItem(
                    text=RichText(text=list_match.group(3)),
                    ordered=ordered,
                    level=indent // 2,
                )
            )
            i += 1
            continue

        img_match = re.match(r"!\[([^\]]*)\]\((data:image/[^;]+;base64,([^)]+))\)", line)
        if img_match:
            try:
                data = base64.b64decode(img_match.group(3))
                mime = img_match.group(2).split(";")[0].replace("data:", "")
                model.nodes.append(Image(data=data, alt_text=img_match.group(1), mime_type=mime))
            except Exception:
                model.nodes.append(Paragraph(text=RichText(text=line)))
            i += 1
            continue

        if "|" in line and i + 1 < len(lines) and re.match(r"^[\s|:-]+$", lines[i + 1]):
            table_lines = [line]
            i += 2
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            rows = []
            for tl in table_lines:
                cells = [c.strip() for c in tl.strip("|").split("|")]
                rows.append(cells)
            if rows:
                cells = [
                    [
                        TableCell(text=c, origin_row=r, origin_col=j)
                        for j, c in enumerate(row)
                    ]
                    for r, row in enumerate(rows)
                ]
                model.nodes.append(Table(rows=rows, cells=cells))
            continue

        model.nodes.append(Paragraph(text=RichText(text=line)))
        i += 1

    return model
