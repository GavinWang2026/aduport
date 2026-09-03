"""Lightweight DOCX extract without docling — python-docx + OOXML enrich.

Mammoth-style style-map (v8 §B): when anydoc is off, map common Word styles to
Heading / ListItem / Blockquote and keep run-level bold/italic/strike/code/link.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

from app.core.document_model import (
    Blockquote,
    DocumentModel,
    Heading,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    Paragraph,
    RichText,
    Table,
    TableCell,
)

# Mammoth-inspired style name → AST kind (case-insensitive).
_STYLE_EXACT: dict[str, tuple[str, int | None]] = {
    "title": ("heading", 1),
    "subtitle": ("heading", 2),
    "quote": ("blockquote", None),
    "blockquote": ("blockquote", None),
    "intense quote": ("blockquote", None),
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



def try_extract_docx_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    """Best-effort DOCX → AST using python-docx (always in core deps)."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            names = set(zf.namelist())
            if "word/document.xml" not in names:
                return None
    except zipfile.BadZipFile:
        return None

    try:
        from docx import Document  # type: ignore
    except ImportError:
        return _zip_xml_fallback(data, title=title)

    try:
        doc = Document(BytesIO(data))
    except Exception:
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
    try:
        zf = zipfile.ZipFile(BytesIO(data))
        image_rels = _docx_image_rels(zf)
    except Exception:
        image_rels = {}
        if zf is not None:
            try:
                zf.close()
            except Exception:
                pass
            zf = None

    try:
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                para = DocxParagraph(child, doc)
                rich = _paragraph_to_rich(para)
                para_images: list = []
                if image_rels and zf is not None:
                    try:
                        para_images = _extract_para_images(zf, image_rels, child)
                    except Exception:
                        para_images = []
                if para_images and not rich.text.strip():
                    # 纯图片段落：原逻辑 continue 跳过 → 现产出 Image 节点（v9 P0 修复）
                    nodes.extend(para_images)
                    continue
                if not rich.text.strip():
                    continue
                style = ""
                try:
                    style = (para.style.name or "") if para.style else ""
                except Exception:
                    style = ""
                node = _node_from_style(style, rich, para)
                if node is not None:
                    nodes.append(node)
                if para_images:
                    # 图文同段：图片紧随段落文本之后（块级降级，保持文档顺序）
                    nodes.extend(para_images)
            elif child.tag == qn("w:tbl"):
                table = DocxTable(child, doc)
                ast_table = _table_from_docx(table, zf=zf, image_rels=image_rels)
                if ast_table is not None:
                    nodes.append(ast_table)
    finally:
        if zf is not None:
            try:
                zf.close()
            except Exception:
                pass

    if not nodes:
        return _zip_xml_fallback(data, title=title)

    model = DocumentModel(
        title=title or "docx",
        nodes=nodes,
        metadata={
            "importer": "docx_native",
            "engine": "python-docx",
            "style_map": "mammoth-lite",
        },
    )
    try:
        from app.importers.docx_ooxml import enrich_docx_model

        model = enrich_docx_model(model, data)
    except Exception:
        pass
    return model


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
    if kind == "blockquote":
        return Blockquote(paragraphs=[rich.text])
    if kind == "list":
        ordered = _para_list_ordered(para)
        level = 0
        _list_meta = _style_list_meta(key)
        if _list_meta is not None:
            ordered, level = _list_meta
        return ListItem(text=rich, ordered=ordered, level=level)
    return Paragraph(text=rich)


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

    # Each entry: (text, col_span, vmerge, images)
    raw: list[list[tuple[str, int, str | None, list]]] = []
    for tr in table._tbl.findall(qn("w:tr")):
        row_cells: list[tuple[str, int, str | None, list]] = []
        for tc in tr.findall(qn("w:tc")):
            text = "".join((t.text or "") for t in tc.iter(qn("w:t"))).strip()
            cell_images: list = []
            if zf is not None and image_rels:
                try:
                    cell_images = _extract_para_images(zf, image_rels, tc)
                except Exception:
                    cell_images = []
            col_span = 1
            vmerge: str | None = None
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
            row_cells.append((text, col_span, vmerge, cell_images))
        if row_cells:
            raw.append(row_cells)
    if not raw:
        return None

    # Map logical column → (text, col_span, vmerge, images) per row
    starts: list[dict[int, tuple[str, int, str | None, list]]] = []
    n_cols = 0
    for row in raw:
        by_col: dict[int, tuple[str, int, str | None, list]] = {}
        col = 0
        for text, col_span, vmerge, cell_images in row:
            by_col[col] = (text, col_span, vmerge, cell_images)
            col += col_span
        n_cols = max(n_cols, col)
        starts.append(by_col)

    cells: list[list[TableCell]] = []
    for r_idx, by_col in enumerate(starts):
        row_cells: list[TableCell] = [
            TableCell(text="", covered=True) for _ in range(n_cols)
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


def _para_has_numbering(para) -> bool:
    try:
        from docx.oxml.ns import qn

        pPr = para._element.find(qn("w:pPr"))
        if pPr is None:
            return False
        return pPr.find(qn("w:numPr")) is not None
    except Exception:
        return False


def _para_list_ordered(para) -> bool:
    """Best-effort: style name hints ordered; otherwise unordered."""
    try:
        style = ""
        try:
            style = (para.style.name or "").lower() if para.style else ""
        except Exception:
            pass
        if "number" in style or "enum" in style or style.startswith("list number"):
            return True
        return False
    except Exception:
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
            except Exception:
                rstyle = ""
            if rstyle in ("strong", "emphasis", "intense emphasis"):
                if "strong" in rstyle:
                    out.append(InlineMark(type=InlineMarkType.BOLD, start=start, end=end))
                if "emphasis" in rstyle:
                    out.append(InlineMark(type=InlineMarkType.ITALIC, start=start, end=end))
        except Exception:
            pass
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
    except Exception:
        inner_items = []
    if not inner_items:
        # python-docx < 1.1 兜底：退化为只扫直接 run
        try:
            inner_items = list(para.runs)
        except Exception:
            inner_items = []
    if not inner_items:
        text = (para.text or "").strip()
        return RichText(text=text)

    for item in inner_items:
        if type(item).__name__ == "Hyperlink":
            start = offset
            href = ""
            try:
                href = item.address or ""
            except Exception:
                href = ""
            sub_runs: list = []
            try:
                sub_runs = list(item.runs)
            except Exception:
                sub_runs = []
            if sub_runs:
                for r in sub_runs:
                    _emit_run(r)
            else:
                # 无 w:r 包裹的裸文本（罕见），按纯文本收录
                t = ""
                try:
                    t = item.text or ""
                except Exception:
                    t = ""
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

    text = "".join(parts).strip()
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


def _zip_xml_fallback(data: bytes, *, title: str = "") -> DocumentModel | None:
    """Minimal w:t scrape when python-docx cannot open the package."""
    from xml.etree import ElementTree as ET

    from app.core import xml_safe
    from app.core.safe_zip import read_zip_member, zip_inflate_session

    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            with zip_inflate_session() as budget:
                budget.bind(zf)
                raw = read_zip_member(zf, "word/document.xml")
    except Exception:
        return None
    try:
        root = xml_safe.fromstring(raw)
    except ET.ParseError:
        return None
    texts: list[str] = []
    buf: list[str] = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "t" and el.text:
            buf.append(el.text)
        elif tag == "p" and buf:
            texts.append("".join(buf).strip())
            buf = []
    if buf:
        texts.append("".join(buf).strip())
    nodes = [Paragraph(text=RichText(text=t)) for t in texts if t]
    if not nodes:
        return None
    return DocumentModel(
        title=title or "docx",
        nodes=nodes,
        metadata={"importer": "docx_zip_fallback", "engine": "xml"},
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
        from app.core import xml_safe
        from app.core.safe_zip import read_zip_member

        root = xml_safe.fromstring(read_zip_member(zf, rels_path))
    except Exception:
        return {}
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

    from app.core.safe_zip import read_zip_member

    out: list[Image] = []
    seen: set[str] = set()
    try:
        has_drawing = bool(p_el.findall(".//" + qn("w:drawing")))
        has_pict = bool(p_el.findall(".//" + qn("w:pict")))
    except Exception:
        return []
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
        except Exception:
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
        except Exception:
            alt = ""
        out.append(
            Image(
                data=raw,
                alt_text=alt or f"Picture {len(out) + 1}",
                mime_type=info.get("mime") or "image/png",
                origin_part="word/document.xml",
            )
        )
    return out
