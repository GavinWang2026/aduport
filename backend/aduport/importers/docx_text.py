"""Lightweight DOCX extract without docling — python-docx + OOXML enrich.

Mammoth-style style-map (v8 §B): when anydoc is off, map common Word styles to
Heading / ListItem / Blockquote and keep run-level bold/italic/strike/code/link.
"""

from __future__ import annotations

import logging
import re
import zipfile
from io import BytesIO

from aduport.core.document_model import (
    Blockquote,
    CodeBlock,
    DocumentModel,
    Heading,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    PageBreak,
    MathBlock,
    Paragraph,
    RichText,
    Table,
    TableCell,
)
from aduport.core.title_source import resolve_title
from aduport.importers.import_guard import swallow_import_error

_log = logging.getLogger("aduport.docx_text")

# Mammoth-inspired style name → AST kind (case-insensitive).
_STYLE_EXACT: dict[str, tuple[str, int | None]] = {
    "title": ("heading", 1),
    "subtitle": ("heading", 2),
    "quote": ("blockquote", None),
    "blockquote": ("blockquote", None),
    "intense quote": ("blockquote", None),
    "aduport code": ("code", None),
    "html preformatted": ("code", None),
    "list paragraph": ("list", None),
    "listparagraph": ("list", None),
    "caption": ("paragraph", None),
}
_HEADING_RE = re.compile(r"^(?:heading|标题)\s*([1-6])\b", re.I)
_HEADING_CN = re.compile(r"^标题\s*([1-6])$")

# python-docx 默认模板把列表编号挂在 styles.xml（List Bullet/List Number 系样式），
# 段落自身 pPr 无 numPr —— 仅靠 _para_has_numbering 会漏检（v9.1 P1-B 修复）。
_LIST_STYLE_RE = re.compile(r"^(list\s+(bullet|number))\b", re.I)


def _style_list_meta(key: str) -> tuple[bool, int] | None:
    """样式名 → (ordered, md_level)。('List Bullet 2') → (False, 1)；('List Number 3') → (True, 2)。"""
    m = _LIST_STYLE_RE.match(key)
    if not m:
        return None
    ordered = m.group(2).lower() == "number"
    tail = key[m.end():].strip()
    level = 0
    digits = re.search(r"(\d+)", tail) if tail else None
    if digits:
        level = max(0, int(digits.group(1)) - 1)
    return ordered, level



_OFFICE_DOCUMENT_RELS = frozenset(
    {
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
        # Strict OOXML (ISO/IEC 29500) package relationship.
        "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument",
    }
)
# Shared sentinel: a fresh object() per call made ``is`` checks fail across helpers.
_MISSING = object()


def _resolve_office_document_part(zf: zipfile.ZipFile) -> str | None:
    """Return the package-relative path of the main document part.

    Prefers the package ``officeDocument`` relationship (transitional or strict).
    Falls back to ``word/document.xml`` when the part exists without a usable rel.
    """
    names = set(zf.namelist())
    try:
        from aduport.core import xml_safe
        from aduport.core.safe_zip import read_zip_member

        raw = read_zip_member(zf, "_rels/.rels")
        root = xml_safe.fromstring(raw)
    except Exception:
        root = None
    if root is not None:
        for rel in root.iter():
            if rel.tag.rsplit("}", 1)[-1] != "Relationship":
                continue
            if (rel.get("Type") or "") not in _OFFICE_DOCUMENT_RELS:
                continue
            target = (rel.get("Target") or "").lstrip("/")
            if target and target in names:
                return target
            if target and target.lstrip("/") in names:
                return target.lstrip("/")
    if "word/document.xml" in names:
        return "word/document.xml"
    return None


def _load_style_outline_levels(zf: zipfile.ZipFile) -> dict[str, int | None]:
    """Map styleId → outlineLvl (0–8) or None when the style sets level 9 (off).

    Inheritance via ``basedOn`` is resolved. Level 9 means “not a heading”.
    """
    from aduport.core import xml_safe
    from aduport.core.safe_zip import read_zip_member

    styles_name = None
    for candidate in ("word/styles.xml", "content/wordstyles.xml"):
        if candidate in zf.namelist():
            styles_name = candidate
            break
    if not styles_name:
        # Styles may hang off the document part's relationships.
        doc_part = _resolve_office_document_part(zf)
        if doc_part:
            rels = _document_part_rels_path(doc_part)
            if rels and rels in zf.namelist():
                try:
                    root = xml_safe.fromstring(read_zip_member(zf, rels))
                    for rel in root.iter():
                        if rel.tag.rsplit("}", 1)[-1] != "Relationship":
                            continue
                        if "styles" not in (rel.get("Type") or ""):
                            continue
                        target = (rel.get("Target") or "").lstrip("/")
                        base = doc_part.rsplit("/", 1)[0] if "/" in doc_part else ""
                        joined = f"{base}/{target}" if base and not target.startswith(base) else target
                        if joined in zf.namelist():
                            styles_name = joined
                            break
                        if target in zf.namelist():
                            styles_name = target
                            break
                except Exception:
                    styles_name = None
    if not styles_name:
        return {}
    try:
        root = xml_safe.fromstring(read_zip_member(zf, styles_name))
    except Exception:
        return {}

    def _attr(el, local: str) -> str | None:
        for key, val in el.attrib.items():
            if key == local or key.endswith("}" + local):
                return val
        return None

    raw: dict[str, tuple[object, str | None]] = {}
    for style in root.iter():
        if style.tag.rsplit("}", 1)[-1] != "style":
            continue
        stype = _attr(style, "type")
        if stype and stype != "paragraph":
            continue
        sid = _attr(style, "styleId")
        if not sid:
            continue
        based_on = None
        outline: object = _MISSING
        for child in style:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "basedOn":
                based_on = _attr(child, "val")
            elif tag == "pPr":
                for ppr in child:
                    if ppr.tag.rsplit("}", 1)[-1] != "outlineLvl":
                        continue
                    val = _attr(ppr, "val")
                    try:
                        level = int(val) if val is not None else None
                    except (TypeError, ValueError):
                        level = None
                    if level is None:
                        outline = _MISSING
                    elif level >= 9:
                        outline = None
                    else:
                        outline = level
        raw[sid] = (outline, based_on)

    resolved: dict[str, int | None] = {}

    def _resolve(sid: str, stack: set[str]) -> object:
        if sid in resolved:
            return resolved[sid]
        if sid in stack or sid not in raw:
            return _MISSING
        outline, based_on = raw[sid]
        if outline is not _MISSING:
            resolved[sid] = outline  # type: ignore[assignment]
            return outline
        if not based_on:
            resolved[sid] = None
            return None
        stack.add(sid)
        parent = _resolve(based_on, stack)
        stack.discard(sid)
        if parent is _MISSING:
            resolved[sid] = None
            return None
        resolved[sid] = parent  # type: ignore[assignment]
        return parent

    for sid in raw:
        _resolve(sid, set())
    return resolved


def _document_part_rels_path(doc_part: str) -> str:
    if "/" in doc_part:
        folder, name = doc_part.rsplit("/", 1)
        return f"{folder}/_rels/{name}.rels"
    return f"_rels/{doc_part}.rels"


def _w_attr(el, local: str) -> str | None:
    if el is None:
        return None
    for key, val in el.attrib.items():
        if key == local or key.endswith("}" + local):
            return val
    return None


def _direct_outline_level(para_element, qn) -> object:
    """Direct ``w:outlineLvl`` on the paragraph, or ``_MISSING`` when absent."""
    ppr = para_element.find(qn("w:pPr"))
    if ppr is None:
        return _MISSING
    outline = ppr.find(qn("w:outlineLvl"))
    if outline is None:
        return _MISSING
    val = _w_attr(outline, "val")
    try:
        level = int(val) if val is not None else None
    except (TypeError, ValueError):
        return _MISSING
    if level is None:
        return _MISSING
    if level >= 9:
        return None
    return level


def _style_id_from_para(para_element, qn) -> str:
    ppr = para_element.find(qn("w:pPr"))
    if ppr is None:
        return ""
    style = ppr.find(qn("w:pStyle"))
    if style is None:
        return ""
    return _w_attr(style, "val") or ""


def _heading_level_from_outline(
    para_element,
    style_outlines: dict[str, int | None],
    qn,
) -> int | None:
    """Resolve Word outlineLvl → Markdown heading level (1–6).

    Matches anydoc: direct outline overlays the style chain; level 9 turns a
    style heading off; levels 0–8 become heading levels 1–9 (clamped to 6).
    """
    direct = _direct_outline_level(para_element, qn)
    if direct is None:
        return None
    if direct is not _MISSING:
        return max(1, min(6, int(direct) + 1))  # type: ignore[arg-type]
    sid = _style_id_from_para(para_element, qn)
    if not sid or sid not in style_outlines:
        return None
    style_level = style_outlines[sid]
    if style_level is None:
        return None
    return max(1, min(6, int(style_level) + 1))


def try_extract_docx_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    """Best-effort DOCX → AST using python-docx (always in core deps)."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            # Report P1-2: python-docx's Document() inflates members through its
            # own zip reader (bypassing read_zip_member accounting). Pre-scan the
            # whole package so a crafted high-ratio DOCX can never blow up into
            # gigabytes inside this process.
            from aduport.core.safe_zip import assert_zip_package_budget

            assert_zip_package_budget(zf)
            doc_part = _resolve_office_document_part(zf)
            if not doc_part:
                return None
            # python-docx only opens the stock word/document.xml path.
            if doc_part != "word/document.xml":
                return _zip_xml_fallback(data, title=title)
    except zipfile.BadZipFile:
        return None

    try:
        from docx import Document  # type: ignore
    except ImportError:
        return _zip_xml_fallback(data, title=title)

    try:
        doc = Document(BytesIO(data))
    except Exception:
        _log.warning("docx.Document failed; falling back to XML text", exc_info=True)
        return _zip_xml_fallback(data, title=title)

    nodes: list = []
    body = doc.element.body
    from docx.oxml.ns import qn  # type: ignore
    from docx.table import Table as DocxTable  # type: ignore
    from docx.text.paragraph import Paragraph as DocxParagraph  # type: ignore

    # v9 P0: docx 内嵌图片抽取 —— 解析 word/_rels/document.xml.rels 的 image 关系。
    # 仅当段落含 drawing/pict 时才从包内读 media bytes（延迟读取，避免全量解压）。
    zf: zipfile.ZipFile | None = None
    image_rels: dict = {}
    style_outlines: dict[str, int | None] = {}
    try:
        zf = zipfile.ZipFile(BytesIO(data))
        image_rels = _docx_image_rels(zf)
        style_outlines = _load_style_outline_levels(zf)
    except Exception as exc:
        image_rels = {}
        style_outlines = {}
        swallow_import_error(exc, where='docx_text.py:101')
        if zf is not None:
            try:
                zf.close()
            except Exception as exc:
                swallow_import_error(exc, where='docx_text.py:106')
            zf = None

    try:
        from aduport.importers.docx_ooxml import body_child_chrome

        for child in body.iterchildren():
            leading, trailing = body_child_chrome(child)
            nodes.extend(leading)
            try:
                _consume_body_child(
                    child,
                    doc,
                    nodes,
                    zf,
                    image_rels,
                    qn,
                    DocxParagraph,
                    DocxTable,
                    style_outlines=style_outlines,
                )
            finally:
                nodes.extend(trailing)
    finally:
        if zf is not None:
            try:
                zf.close()
            except Exception as exc:
                swallow_import_error(exc, where='docx_text.py:147')

    if not nodes:
        return _zip_xml_fallback(data, title=title)

    model = DocumentModel(
        title=resolve_title(title, nodes, filename=f"{title}.docx" if title else ""),
        nodes=nodes,
        metadata={
            "importer": "docx_native",
            "engine": "python-docx",
            "style_map": "mammoth-lite",
        },
    )
    # OOXML chrome (charts, sections, comments) is attached once by import_docx
    # or the registry wrapper. Doing it here as well doubled every appended node.
    return model


def _consume_body_child(
    child,
    doc,
    nodes,
    zf,
    image_rels,
    qn,
    DocxParagraph,
    DocxTable,
    *,
    style_outlines: dict[str, int | None] | None = None,
) -> None:
    if child.tag == qn("w:p"):
        para = DocxParagraph(child, doc)
        math_here = _math_blocks_from_paragraph(child)
        outside = _text_outside_math(child).strip()
        if math_here and not outside:
            nodes.extend(math_here)
            if image_rels and zf is not None:
                try:
                    nodes.extend(_extract_para_images(zf, image_rels, child))
                except Exception as exc:
                    swallow_import_error(exc, where="docx_text.py:math-image")
            return
        rich = _paragraph_to_rich(para)
        para_images: list = []
        if image_rels and zf is not None:
            try:
                para_images = _extract_para_images(zf, image_rels, child)
            except Exception as exc:
                para_images = []
                swallow_import_error(exc, where="docx_text.py:119")
        if para_images and not rich.text.strip():
            nodes.extend(para_images)
            return
        page_break = _paragraph_has_page_break(child)
        if not rich.text.strip():
            if page_break:
                nodes.append(PageBreak())
            return
        if _is_toc_paragraph(para):
            return
        style = ""
        try:
            style = (para.style.name or "") if para.style else ""
        except Exception as exc:
            style = ""
            swallow_import_error(exc, where="docx_text.py:130")
        node = _node_from_style(style, rich, para)
        direct_outline = _direct_outline_level(child, qn)
        outline_level = _heading_level_from_outline(
            child, style_outlines or {}, qn
        )
        if outline_level is not None:
            node = Heading(level=outline_level, text=rich)
        elif direct_outline is None and isinstance(node, Heading):
            # Explicit outlineLvl=9 turns a style-name heading off.
            node = Paragraph(text=rich)
        if node is not None:
            nodes.append(node)
        if page_break:
            nodes.append(PageBreak())
        if para_images:
            nodes.extend(para_images)
    elif child.tag == qn("w:tbl"):
        table = DocxTable(child, doc)
        ast_table = _table_from_docx(table, zf=zf, image_rels=image_rels)
        if ast_table is not None:
            nodes.append(ast_table)


def _is_toc_paragraph(para) -> bool:
    """TOC field result must not become a body paragraph (v16 F6)."""
    text = (getattr(para, "text", None) or "").strip()
    if text in {"[TOC]", "*[TOC]*", "[toc]"}:
        return True
    try:
        xml = para._element.xml
    except Exception:
        return False
    return "instrText" in xml and "TOC" in xml


def _code_block_from_text(raw: str) -> CodeBlock:
    text = raw or ""
    lang = ""
    if text.startswith("aduport-lang:"):
        head, _, rest = text.partition("\n")
        lang = head.split(":", 1)[1].strip()
        text = rest
    return CodeBlock(lang=lang, content=text.rstrip("\n"))


def _entirely_code(rich: RichText) -> bool:
    text = rich.text or ""
    if not text.strip() or not rich.marks:
        return False
    return any(
        m.type == InlineMarkType.CODE and m.start <= 0 and m.end >= len(text)
        for m in rich.marks
    )


def _node_from_style(style: str, rich: RichText, para) -> object | None:
    """Map Word paragraph style (+ numbering) → AST node (mammoth-lite)."""
    key = (style or "").strip().lower()
    kind, level = _STYLE_EXACT.get(key, (None, None))
    if kind is None:
        m = _HEADING_RE.match(key) or _HEADING_CN.match(key)
        if m:
            kind, level = "heading", int(m.group(1))
        elif key.startswith("heading") or key.startswith("标题"):
            kind, level = "heading", 1
            for ch in key:
                if ch.isdigit():
                    level = max(1, min(6, int(ch)))
                    break

    # Numbering definition → list even without List Paragraph style
    if kind is None:
        _list_meta = _style_list_meta(key)
        if _list_meta is not None or _para_has_numbering(para):
            kind = "list"

    if kind == "heading":
        return Heading(level=max(1, min(6, int(level or 1))), text=rich)
    if kind == "code":
        return _code_block_from_text(rich.text)
    if kind == "blockquote":
        return Blockquote(paragraphs=[rich.text])
    if kind is None and _entirely_code(rich):
        return _code_block_from_text(rich.text)
    if kind == "list":
        ordered = _para_list_ordered(para)
        level = _para_ilvl(para)
        _list_meta = _style_list_meta(key)
        if level == 0 and _list_meta is not None:
            ordered, level = _list_meta
        return ListItem(text=rich, ordered=ordered, level=level)
    return Paragraph(text=rich)


def _nested_w_tbl_markdown(tbl, qn) -> str:
    """Render a nested ``w:tbl`` as a compact markdown-ish grid (structure preserved)."""
    rows_out: list[str] = []
    for tr in tbl.findall(qn("w:tr")):
        cells: list[str] = []
        for tc in tr.findall(qn("w:tc")):
            cells.append(_tc_plain_text_preserving_nested(tc, qn).replace("|", "\\|"))
        if cells:
            rows_out.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows_out)


def _tc_plain_text_preserving_nested(tc, qn) -> str:
    """Cell text that keeps nested tables as markdown instead of flat ``w:t`` scrape."""
    chunks: list[str] = []
    for child in tc:
        if child.tag == qn("w:tcPr"):
            continue
        if child.tag == qn("w:p"):
            para = "".join((t.text or "") for t in child.iter(qn("w:t"))).strip()
            if para:
                chunks.append(para)
        elif child.tag == qn("w:tbl"):
            nested = _nested_w_tbl_markdown(child, qn)
            if nested:
                chunks.append(nested)
    if chunks:
        return "\n".join(chunks).strip()
    # Fallback: no direct p/tbl children (unusual) — flat scrape of this cell only,
    # excluding nested tables' text by skipping descendant tbl trees.
    parts: list[str] = []
    for el in tc.iter():
        if el is not tc and el.tag == qn("w:tbl"):
            # Skip walking into nested tbl via iter — already handled above;
            # if we got here, nested wasn't a direct child.
            break
        if el.tag == qn("w:t") and el.text:
            parts.append(el.text)
    return "".join(parts).strip()


def _table_from_docx(
    table,
    *,
    zf: zipfile.ZipFile | None = None,
    image_rels: dict | None = None,
) -> Table | None:
    """Build Table with gridSpan / vMerge → col_span / row_span / covered.

    Walks ``w:tc`` (not python-docx ``row.cells``, which duplicates merges).
    Footnotes/comments remain via ``enrich_docx_model`` post-pass.
    When ``zf`` + ``image_rels`` provided, extracts drawings inside each ``w:tc``.
    """
    try:
        from docx.oxml.ns import qn
    except ImportError:
        return None

    # Each entry: (grid_before, cells[(text, col_span, vmerge, images)], grid_after)
    raw: list[tuple[int, list[tuple[str, int, str | None, list]], int]] = []
    for tr in table._tbl.findall(qn("w:tr")):
        grid_before = 0
        grid_after = 0
        tr_pr = tr.find(qn("w:trPr"))
        if tr_pr is not None:
            gb = tr_pr.find(qn("w:gridBefore"))
            if gb is not None:
                try:
                    grid_before = max(0, int(gb.get(qn("w:val")) or 0))
                except (TypeError, ValueError):
                    grid_before = 0
            ga = tr_pr.find(qn("w:gridAfter"))
            if ga is not None:
                try:
                    grid_after = max(0, int(ga.get(qn("w:val")) or 0))
                except (TypeError, ValueError):
                    grid_after = 0
        row_cells: list[tuple[str, int, str | None, list]] = []
        pending_hmerge = 0
        for tc in tr.findall(qn("w:tc")):
            text = _tc_plain_text_preserving_nested(tc, qn)
            cell_images: list = []
            if zf is not None and image_rels:
                try:
                    cell_images = _extract_para_images(zf, image_rels, tc)
                except Exception as exc:
                    cell_images = []
                    swallow_import_error(exc, where='docx_text.py:233')
            col_span = 1
            vmerge: str | None = None
            hmerge: str | None = None
            tc_pr = tc.find(qn("w:tcPr"))
            if tc_pr is not None:
                gs = tc_pr.find(qn("w:gridSpan"))
                if gs is not None:
                    try:
                        col_span = max(1, int(gs.get(qn("w:val")) or 1))
                    except (TypeError, ValueError):
                        col_span = 1
                vm = tc_pr.find(qn("w:vMerge"))
                if vm is not None:
                    val = (vm.get(qn("w:val")) or "").lower()
                    vmerge = "restart" if val == "restart" else "continue"
                hm = tc_pr.find(qn("w:hMerge"))
                if hm is not None:
                    val = (hm.get(qn("w:val")) or "").lower()
                    hmerge = "restart" if val == "restart" else "continue"
            if hmerge == "continue":
                # Fold into the open horizontal merge to the left.
                if row_cells:
                    prev_text, prev_span, prev_vm, prev_imgs = row_cells[-1]
                    row_cells[-1] = (prev_text, prev_span + 1, prev_vm, prev_imgs)
                else:
                    pending_hmerge += 1
                continue
            if hmerge == "restart":
                col_span = max(col_span, 1)
            if pending_hmerge:
                # continue cells before a restart are unusual; treat as leading span
                col_span += pending_hmerge
                pending_hmerge = 0
            row_cells.append((text, col_span, vmerge, cell_images))
        if row_cells or grid_before or grid_after:
            raw.append((grid_before, row_cells, grid_after))
    if not raw:
        return None

    # Map logical column → (text, col_span, vmerge, images) per row
    starts: list[dict[int, tuple[str, int, str | None, list]]] = []
    n_cols = 0
    for grid_before, row, grid_after in raw:
        by_col: dict[int, tuple[str, int, str | None, list]] = {}
        col = grid_before
        for text, col_span, vmerge, cell_images in row:
            by_col[col] = (text, col_span, vmerge, cell_images)
            col += col_span
        n_cols = max(n_cols, col + grid_after)
        starts.append(by_col)

    cells: list[list[TableCell]] = []
    for r_idx, by_col in enumerate(starts):
        # gridBefore / gridAfter leave real empty cells, not merge covers.
        # Defaulting them to covered=True used to collapse middle rows to <tr></tr>.
        row_cells: list[TableCell] = [
            TableCell(text="", covered=False, origin_row=r_idx, origin_col=c)
            for c in range(n_cols)
        ]
        for start_col, (text, col_span, vmerge, cell_images) in by_col.items():
            if vmerge == "continue":
                # Find restart above
                origin_r = r_idx
                for rr in range(r_idx - 1, -1, -1):
                    prev = starts[rr].get(start_col)
                    if prev is None:
                        continue
                    if prev[2] == "restart" or prev[2] is None:
                        origin_r = rr
                        break
                    if prev[2] == "continue":
                        continue
                # Continue cells sometimes carry the lower half of the label.
                if text.strip() and 0 <= origin_r < len(cells):
                    origin = cells[origin_r][start_col]
                    if not origin.covered:
                        joined = (
                            f"{origin.text}\n{text.strip()}"
                            if origin.text.strip()
                            else text.strip()
                        )
                        cells[origin_r][start_col] = origin.model_copy(
                            update={"text": joined}
                        )
                row_cells[start_col] = TableCell(
                    text="",
                    covered=True,
                    origin_row=origin_r,
                    origin_col=start_col,
                )
                for k in range(1, col_span):
                    if start_col + k < n_cols:
                        row_cells[start_col + k] = TableCell(
                            text="",
                            covered=True,
                            origin_row=origin_r,
                            origin_col=start_col,
                        )
                continue

            row_span = 1
            if vmerge == "restart":
                for rr in range(r_idx + 1, len(starts)):
                    below = starts[rr].get(start_col)
                    if below is not None and below[2] == "continue":
                        row_span += 1
                    else:
                        break

            row_cells[start_col] = TableCell(
                text=text,
                col_span=col_span,
                row_span=row_span,
                covered=False,
                origin_row=r_idx,
                origin_col=start_col,
                images=list(cell_images or []),
            )
            for k in range(1, col_span):
                if start_col + k < n_cols:
                    row_cells[start_col + k] = TableCell(
                        text="",
                        covered=True,
                        origin_row=r_idx,
                        origin_col=start_col,
                    )
        cells.append(row_cells)

    rows = [[c.text for c in row] for row in cells]
    has_span = any(
        c.covered or c.row_span > 1 or c.col_span > 1 for row in cells for c in row
    )
    has_images = any(c.images for row in cells for c in row)
    return Table(
        rows=rows,
        cells=cells if (has_span or has_images) else [],
        header_rows=1 if rows else 0,
    )


def _para_ilvl(para) -> int:
    try:
        from docx.oxml.ns import qn

        pPr = para._element.find(qn("w:pPr"))
        if pPr is None:
            return 0
        numPr = pPr.find(qn("w:numPr"))
        if numPr is None:
            return 0
        ilvl = numPr.find(qn("w:ilvl"))
        if ilvl is None:
            return 0
        return max(0, min(8, int(ilvl.get(qn("w:val")) or 0)))
    except Exception as exc:
        return swallow_import_error(exc, where="docx_text.py:ilvl", default=0)


def _math_blocks_from_paragraph(element) -> list[MathBlock]:
    try:
        from aduport.exporters.omml import omml_element_to_tex
    except Exception:
        return []
    blocks: list[MathBlock] = []
    for child in list(element):
        local = child.tag.rsplit("}", 1)[-1]
        if local not in ("oMath", "oMathPara"):
            continue
        try:
            tex = omml_element_to_tex(child).strip()
        except Exception:
            tex = ""
        if tex:
            blocks.append(MathBlock(content=tex, display=local == "oMathPara"))
    return blocks


def _text_outside_math(element) -> str:
    wt = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    chunks: list[str] = []

    def walk(node, in_math: bool) -> None:
        local = node.tag.rsplit("}", 1)[-1]
        now = in_math or local in ("oMath", "oMathPara")
        if node.tag == wt and not now and node.text:
            chunks.append(node.text)
        for child in list(node):
            walk(child, now)

    walk(element, False)
    return "".join(chunks)


def _para_has_numbering(para) -> bool:
    try:
        from docx.oxml.ns import qn

        pPr = para._element.find(qn("w:pPr"))
        if pPr is None:
            return False
        return pPr.find(qn("w:numPr")) is not None
    except Exception as exc:
        return swallow_import_error(exc, where='docx_text.py:347', default=False)


def _para_list_ordered(para) -> bool:
    """Style name or numbering.xml numFmt. Decimal numIds are ordered lists."""
    try:
        from_numbering = _num_id_is_ordered(para)
        if from_numbering is not None:
            return from_numbering
        style = ""
        try:
            style = (para.style.name or "").lower() if para.style else ""
        except Exception as exc:
            swallow_import_error(exc, where='docx_text.py:357')
        if "number" in style or "enum" in style or style.startswith("list number"):
            return True
        return False
    except Exception as exc:
        return swallow_import_error(exc, where='docx_text.py:362', default=False)


def _num_id_is_ordered(para) -> bool | None:
    from docx.oxml.ns import qn

    pPr = para._element.find(qn("w:pPr"))
    if pPr is None:
        return None
    numPr = pPr.find(qn("w:numPr"))
    if numPr is None:
        return None
    num_id_el = numPr.find(qn("w:numId"))
    num_id = num_id_el.get(qn("w:val")) if num_id_el is not None else None
    if not num_id or num_id == "0":
        return None
    numbering = para.part.numbering_part.element
    abstract_id = None
    for num in numbering.findall(qn("w:num")):
        if num.get(qn("w:numId")) != num_id:
            continue
        ref = num.find(qn("w:abstractNumId"))
        abstract_id = ref.get(qn("w:val")) if ref is not None else None
        break
    if abstract_id is None:
        return None
    for abstract in numbering.findall(qn("w:abstractNum")):
        if abstract.get(qn("w:abstractNumId")) != abstract_id:
            continue
        for lvl in abstract.findall(qn("w:lvl")):
            if (lvl.get(qn("w:ilvl")) or "0") != "0":
                continue
            fmt = lvl.find(qn("w:numFmt"))
            kind = (fmt.get(qn("w:val")) if fmt is not None else "") or ""
            if not kind or kind in {"bullet", "none"}:
                return False
            return True
    return None


def _splice_omml(para, raw: str, marks: list[InlineMark]) -> tuple[str, list[InlineMark]]:
    """Put OMML back inside the paragraph so formulas are not appended at EOF."""
    inserts = _omml_inserts(para)
    if not inserts:
        return raw, marks
    for offset, snippet in reversed(inserts):
        offset = max(0, min(offset, len(raw)))
        raw = raw[:offset] + snippet + raw[offset:]
        shifted: list[InlineMark] = []
        span = len(snippet)
        for mark in marks:
            start = mark.start + span if mark.start >= offset else mark.start
            end = mark.end + span if mark.end > offset else mark.end
            if end > start:
                shifted.append(
                    InlineMark(type=mark.type, start=start, end=end, href=mark.href)
                )
        marks = shifted
    return raw, marks


def _omml_inserts(para) -> list[tuple[int, str]]:
    element = getattr(para, "_element", None)
    if element is None:
        return []
    try:
        from aduport.exporters.omml import omml_element_to_tex
    except Exception:
        return []
    wt = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"

    def _text_len(node) -> int:
        total = 0
        for child in list(node):
            local = child.tag.rsplit("}", 1)[-1]
            if local in ("oMath", "oMathPara"):
                continue
            if child.tag == wt:
                total += len(child.text or "")
            total += _text_len(child)
        return total

    offset = 0
    found: list[tuple[int, str]] = []
    for child in list(element):
        local = child.tag.rsplit("}", 1)[-1]
        if local in ("oMath", "oMathPara"):
            try:
                tex = omml_element_to_tex(child).strip()
            except Exception:
                tex = ""
            if tex:
                snippet = f"$${tex}$$" if local == "oMathPara" else f"${tex}$"
                found.append((offset, snippet))
            continue
        offset += _text_len(child)
    return found


def _paragraph_has_page_break(element) -> bool:
    from docx.oxml.ns import qn

    for el in element.iter():
        if not str(el.tag).endswith("}br"):
            continue
        if (el.get(qn("w:type")) or "") == "page":
            return True
    return False


def _paragraph_to_rich(para) -> RichText:
    """§3.4: emit run-level bold/italic/strike/code/link without anydoc.

    v9.1 实锤修复 —— 超链接锚文本静默丢失：python-docx 的 ``para.runs`` 只覆盖
    ``w:p`` 直接子级 ``w:r``，而 Word 把超链接文本包在 ``w:hyperlink`` 元素内，
    故旧实现两段式「先收直接 run、再按全文 find 子串贴 LINK」永远 find 不到
    锚文本（文本本身也从未进 parts），纯超链接段整段被吞。
    现改用 ``para.iter_inner_content()`` 按文档序产出 Run/Hyperlink；hyperlink
    内部 run 走同一套 mark 提取，锚文本整体再包一个 LINK mark，坐标天然精确，
    同时兼容超链接内部加粗/斜体等嵌套样式。
    """
    parts: list[str] = []
    marks: list[InlineMark] = []
    offset = 0

    def _run_marks(run, start: int, end: int) -> list[InlineMark]:
        out: list[InlineMark] = []
        try:
            if run.bold:
                out.append(InlineMark(type=InlineMarkType.BOLD, start=start, end=end))
            if run.italic:
                out.append(InlineMark(type=InlineMarkType.ITALIC, start=start, end=end))
            if run.font and run.font.strike:
                out.append(
                    InlineMark(type=InlineMarkType.STRIKETHROUGH, start=start, end=end)
                )
            name = (run.font.name or "") if run.font else ""
            if name and ("courier" in name.lower() or "mono" in name.lower()):
                out.append(InlineMark(type=InlineMarkType.CODE, start=start, end=end))
            try:
                rstyle = (run.style.name or "").lower() if run.style else ""
            except Exception as exc:
                rstyle = ""
                swallow_import_error(exc, where='docx_text.py:397')
            if rstyle in ("strong", "emphasis", "intense emphasis"):
                if "strong" in rstyle:
                    out.append(InlineMark(type=InlineMarkType.BOLD, start=start, end=end))
                if "emphasis" in rstyle:
                    out.append(InlineMark(type=InlineMarkType.ITALIC, start=start, end=end))
        except Exception as exc:
            swallow_import_error(exc, where='docx_text.py:404')
        return out

    def _emit_run(run) -> None:
        nonlocal offset
        chunk = run.text or ""
        if not chunk:
            return
        start, end = offset, offset + len(chunk)
        parts.append(chunk)
        marks.extend(_run_marks(run, start, end))
        offset = end

    try:
        inner_items = list(para.iter_inner_content())
    except Exception as exc:
        inner_items = []
        swallow_import_error(exc, where='docx_text.py:420')
    if not inner_items:
        # python-docx < 1.1 兜底：退化为只扫直接 run
        try:
            inner_items = list(para.runs)
        except Exception as exc:
            inner_items = []
            swallow_import_error(exc, where='docx_text.py:426')
    if not inner_items:
        text = (para.text or "").strip()
        return RichText(text=text)

    for item in inner_items:
        if type(item).__name__ == "Hyperlink":
            start = offset
            href = ""
            try:
                href = item.address or ""
            except Exception as exc:
                href = ""
                swallow_import_error(exc, where='docx_text.py:438')
            sub_runs: list = []
            try:
                sub_runs = list(item.runs)
            except Exception as exc:
                sub_runs = []
                swallow_import_error(exc, where='docx_text.py:443')
            if sub_runs:
                for r in sub_runs:
                    _emit_run(r)
            else:
                # 无 w:r 包裹的裸文本（罕见），按纯文本收录
                t = ""
                try:
                    t = item.text or ""
                except Exception as exc:
                    t = ""
                    swallow_import_error(exc, where='docx_text.py:453')
                if t:
                    parts.append(t)
                    offset += len(t)
            end = offset
            if end > start:
                marks.append(
                    InlineMark(
                        type=InlineMarkType.LINK,
                        start=start,
                        end=end,
                        href=href or None,
                    )
                )
        else:
            _emit_run(item)

    raw, marks = _splice_omml(para, "".join(parts), marks)
    text = raw.strip()
    if not text:
        return RichText(text="")
    lead = len("".join(parts)) - len("".join(parts).lstrip())
    if lead:
        adjusted = []
        for m in marks:
            s = max(0, m.start - lead)
            e = max(0, m.end - lead)
            if e > s and e <= len(text):
                adjusted.append(InlineMark(type=m.type, start=s, end=e, href=m.href))
        marks = adjusted
    marks.sort(key=lambda m: (m.start, m.end))
    # 去重：run.bold 与 strong 样式可能对同 span 重复打标，渲染器会双开双闭
    seen: set[tuple] = set()
    deduped: list[InlineMark] = []
    for m in marks:
        key = (str(m.type), m.start, m.end, m.href)
        if key not in seen:
            seen.add(key)
            deduped.append(m)
    return RichText(text=text, marks=deduped)


def _heading_from_style_id(sid: str, rich: RichText) -> Heading | None:
    """Map a bare styleId like ``Heading1`` to a Heading node."""
    key = (sid or "").strip().lower()
    if not key:
        return None
    m = _HEADING_RE.match(key) or re.match(r"^(?:heading|标题)\s*([1-6])$", key, re.I)
    if not m:
        m = re.match(r"^heading([1-6])$", key, re.I)
    if m:
        return Heading(level=max(1, min(6, int(m.group(1)))), text=rich)
    if key.startswith("heading") or key.startswith("标题"):
        level = 1
        for ch in key:
            if ch.isdigit():
                level = max(1, min(6, int(ch)))
                break
        return Heading(level=level, text=rich)
    if key in _STYLE_EXACT and _STYLE_EXACT[key][0] == "heading":
        return Heading(level=int(_STYLE_EXACT[key][1] or 1), text=rich)
    return None


def _zip_xml_fallback(data: bytes, *, title: str = "") -> DocumentModel | None:
    """XML scrape when python-docx cannot open the package (incl. alt-path OPC)."""
    from xml.etree import ElementTree as ET

    from aduport.core import xml_safe
    from aduport.core.safe_zip import read_zip_member, zip_inflate_session
    from docx.oxml.ns import qn  # type: ignore

    doc_part = ""
    style_outlines: dict[str, int | None] = {}
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            with zip_inflate_session() as budget:
                budget.bind(zf)
                resolved = _resolve_office_document_part(zf)
                if not resolved:
                    return None
                doc_part = resolved
                raw = read_zip_member(zf, doc_part)
                style_outlines = _load_style_outline_levels(zf)
    except Exception as exc:
        return swallow_import_error(exc, where='docx_text.py:507', default=None)
    try:
        root = xml_safe.fromstring(raw)
    except ET.ParseError:
        return None

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    body = None
    for child in list(root):
        if _local(child.tag) == "body":
            body = child
            break
    if body is None:
        body = root

    # Strict OOXML (purl.oclc.org) and transitional share local names; qn() from
    # python-docx is transitional-only, so look up pPr / outlineLvl by local name.
    def _find_local(el, name: str):
        for child in list(el):
            if _local(child.tag) == name:
                return child
        return None

    def _outline_level_ns_agnostic(para_el) -> int | None:
        ppr = _find_local(para_el, "pPr")
        direct = _MISSING
        if ppr is not None:
            outline = _find_local(ppr, "outlineLvl")
            if outline is not None:
                val = _w_attr(outline, "val")
                try:
                    level = int(val) if val is not None else None
                except (TypeError, ValueError):
                    level = None
                if level is None:
                    direct = _MISSING
                elif level >= 9:
                    return None
                else:
                    return max(1, min(6, level + 1))
        if direct is None:
            return None
        style_el = _find_local(ppr, "pStyle") if ppr is not None else None
        sid = _w_attr(style_el, "val") if style_el is not None else ""
        if not sid or sid not in style_outlines:
            return None
        style_level = style_outlines[sid]
        if style_level is None:
            return None
        return max(1, min(6, int(style_level) + 1))

    def _style_id_ns_agnostic(para_el) -> str:
        ppr = _find_local(para_el, "pPr")
        if ppr is None:
            return ""
        style_el = _find_local(ppr, "pStyle")
        return _w_attr(style_el, "val") or ""

    nodes: list = []
    for child in list(body):
        if _local(child.tag) != "p":
            continue
        texts = [
            (t.text or "")
            for t in child.iter()
            if _local(t.tag) == "t" and t.text
        ]
        plain = "".join(texts).strip()
        if not plain:
            continue
        rich = RichText(text=plain)
        outline_level = _outline_level_ns_agnostic(child)
        if outline_level is not None:
            nodes.append(Heading(level=outline_level, text=rich))
            continue
        # Explicit outlineLvl=9 → body paragraph even if style says Heading.
        ppr = _find_local(child, "pPr")
        if ppr is not None and _find_local(ppr, "outlineLvl") is not None:
            val = _w_attr(_find_local(ppr, "outlineLvl"), "val")
            try:
                if val is not None and int(val) >= 9:
                    nodes.append(Paragraph(text=rich))
                    continue
            except (TypeError, ValueError):
                pass
        sid = _style_id_ns_agnostic(child)
        headed = _heading_from_style_id(sid, rich)
        nodes.append(headed if headed is not None else Paragraph(text=rich))

    if not nodes:
        return None
    return DocumentModel(
        title=title or "docx",
        nodes=nodes,
        metadata={
            "importer": "docx_zip_fallback",
            "engine": "xml",
            "document_part": doc_part,
        },
    )
# ---------------------------------------------------------------------------
# v9 P0: docx 内嵌图片抽取 —— 主循环只负责调用；关系解析/媒体读取在此。
# ---------------------------------------------------------------------------
def _img_mime_for(ext: str) -> str:
    """扩展名 → MIME。缺省按 png 处理（Word 内嵌图绝大多数为 png/jpeg）。"""
    ext = (ext or "").lower().lstrip(".")
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "bmp": "image/bmp",
        "svg": "image/svg+xml",
        "webp": "image/webp",
        "tif": "image/tiff",
        "tiff": "image/tiff",
        "emf": "image/x-emf",
        "wmf": "image/x-wmf",
        "ico": "image/x-icon",
    }.get(ext, "image/png")


def _docx_image_rels(zf: zipfile.ZipFile) -> dict[str, dict]:
    """word/_rels/document.xml.rels → {rId: {"target": 包内路径, "mime": ...}}。

    只保留 image 关系；Target 相对 word/ 目录的路径规范化为包内绝对路径
    （media/image1.png → word/media/image1.png）。rels / media 均走
    ``read_zip_member``（与 docx_ooxml 同款安全读取）。
    """
    rels_path = "word/_rels/document.xml.rels"
    try:
        names = set(zf.namelist())
        if rels_path not in names:
            return {}
        from aduport.core import xml_safe
        from aduport.core.safe_zip import read_zip_member

        root = xml_safe.fromstring(read_zip_member(zf, rels_path))
    except Exception as exc:
        return swallow_import_error(exc, where='docx_text.py:570', default={})
    ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    out: dict[str, dict] = {}
    for rel in root.findall(f"{ns}Relationship"):
        rid = rel.get("Id")
        target = rel.get("Target") or ""
        rtype = rel.get("Type") or ""
        if not rid or "image" not in rtype.lower():
            continue
        if not target.startswith(("/", "http://", "https://", "file://")):
            target = "word/" + target.lstrip("/")
        out[rid] = {"target": target, "mime": _img_mime_for(target.rsplit(".", 1)[-1])}
    return out


def _extract_para_images(
    zf: zipfile.ZipFile, rels: dict[str, dict], p_el
) -> list[Image]:
    """从单个 w:p 抽取内嵌图片（现代 a:blip + 旧式 v:imagedata）。

    - 先用 findall 预检段落是否含 w:drawing/w:pict，无图普通文本段零开销短路；
    - r:embed / r:link / r:id 三种关系引用都认，rId 去重（同一图多次引用只读一次）；
    - alt 取所在 w:drawing 的 wp:docPr @descr/@name；
    - 媒体字节经 ``read_zip_member`` 按需读取（复用 docx_ooxml 安全 zip 工具）。
    """
    from docx.oxml.ns import qn  # type: ignore

    from aduport.core.safe_zip import read_zip_member

    out: list[Image] = []
    seen: set[str] = set()
    try:
        has_drawing = bool(p_el.findall(".//" + qn("w:drawing")))
        has_pict = bool(p_el.findall(".//" + qn("w:pict")))
    except Exception as exc:
        return swallow_import_error(exc, where='docx_text.py:605', default=[])
    if not has_drawing and not has_pict:
        return []

    for blip in p_el.iter():
        tag = blip.tag.rsplit("}", 1)[-1]
        if tag not in ("blip", "imagedata"):
            continue
        rid = None
        for key in ("r:embed", "r:link", "r:id"):
            rid = blip.get(qn(key))
            if rid:
                break
        if not rid or rid in seen or rid not in rels:
            continue
        info = rels[rid]
        try:
            raw = read_zip_member(zf, info["target"])
        except Exception as exc:
            swallow_import_error(exc, where='docx_text.py:624')
            continue
        seen.add(rid)
        alt = ""
        try:
            anc = blip
            while anc is not None:
                if anc.tag == qn("w:drawing"):
                    for dp in anc.iter():
                        if dp.tag == qn("wp:docPr"):
                            alt = dp.get("descr") or dp.get("name") or ""
                            break
                    break
                anc = anc.getparent()
        except Exception as exc:
            alt = ""
            swallow_import_error(exc, where='docx_text.py:638')
        out.append(
            Image(
                data=raw,
                alt_text=alt or f"Picture {len(out) + 1}",
                mime_type=info.get("mime") or "image/png",
                origin_part="word/document.xml",
            )
        )
    return out
