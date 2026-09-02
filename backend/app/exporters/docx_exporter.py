"""Export DocumentModel to DOCX using python-docx."""

from __future__ import annotations

from io import BytesIO

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor

from app.config import settings
from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    MathBlock,
    PivotTable,
    Paragraph,
    Revision,
    Table,
    TableCell,
    TextBox,
    ensure_rich_text,
)

_THEMES = {
    "default": {"heading": RGBColor(0x1A, 0x56, 0xDB), "normal_size": 11, "font": "Calibri"},
    "academic": {"heading": RGBColor(0x2C, 0x3E, 0x50), "normal_size": 12, "font": "Times New Roman"},
    "minimal": {"heading": RGBColor(0x22, 0x22, 0x22), "normal_size": 11, "font": "Arial"},
}


def export_docx(model: DocumentModel, *, theme: str | None = None) -> bytes:
    from app.core.exceptions import ExportError

    max_nodes = int(getattr(settings, "docx_max_nodes", 0) or 0)
    if max_nodes > 0 and len(model.nodes) > max_nodes:
        raise ExportError(
            f"文档节点数 {len(model.nodes)} 超过 DOCX 导出上限 {max_nodes}。"
            "请拆分文档，或提高 MDBRIDGE_DOCX_MAX_NODES（企业批量场景建议分片）。",
            code="docx_too_large",
        )
    doc = Document()
    theme_key = (theme or settings.docx_theme or "default").lower().strip()
    theme_cfg = _THEMES.get(theme_key, _THEMES["default"])
    _apply_theme(doc, theme_cfg)
    _setup_header_footer(doc, model.title or "md-bridge")
    _apply_section_props(doc, model)

    comments: list[Comment] = []
    footnotes: list[Footnote] = []
    if model.title:
        doc.add_heading(model.title, level=0)

    body = doc.element.body
    from app.exporters.docx_oxml_fast import FastBody

    fast = FastBody(body)
    try:
        for node in model.nodes:
            if isinstance(node, Heading):
                fast.heading(node.plain_text(), node.level)
            elif isinstance(node, Paragraph):
                rich = ensure_rich_text(node.text)
                if rich.marks:
                    fast.flush()
                    _add_rich_paragraph(doc, rich)
                else:
                    fast.plain(rich.text)
            elif isinstance(node, Blockquote):
                fast.flush()
                for para in node.paragraphs:
                    p = doc.add_paragraph()
                    p.paragraph_format.left_indent = Inches(0.25)
                    _apply_rich_to_paragraph(p, ensure_rich_text(para))
                    if p.runs:
                        p.runs[0].italic = True
            elif isinstance(node, HorizontalRule):
                fast.plain("─" * 20)
            elif isinstance(node, ListItem):
                rich = ensure_rich_text(node.text)
                if rich.marks:
                    fast.flush()
                    style = "List Number" if node.ordered else "List Bullet"
                    p = doc.add_paragraph(style=style)
                    if node.checked is not None:
                        p.add_run("☑ " if node.checked else "☐ ")
                    _apply_rich_to_paragraph(p, rich)
                    level = max(0, min(int(node.level), 8))
                    if level > 0:
                        p.paragraph_format.left_indent = Inches(0.25 * level)
                else:
                    if node.ordered:
                        fast.plain(("  " * node.level) + f"1. {rich.text}")
                    else:
                        fast.bullet(rich.text, level=node.level, checked=node.checked)
                for child in node.children:
                    cr = ensure_rich_text(child.text)
                    if cr.marks:
                        fast.flush()
                        cp = doc.add_paragraph()
                        cp.paragraph_format.left_indent = Inches(0.25 * (node.level + 1))
                        _apply_rich_to_paragraph(cp, cr)
                    else:
                        fast.plain(("  " * (node.level + 1)) + cr.text)
            elif isinstance(node, Table):
                fast.flush()
                _add_table(doc, node)
            elif isinstance(node, Image):
                fast.flush()
                _add_image(doc, node)
            elif isinstance(node, MathBlock):
                fast.flush()
                p = doc.add_paragraph()
                run = p.add_run(node.content)
                run.font.name = "Courier New"
                run.italic = True
            elif isinstance(node, Footnote):
                footnotes.append(node)
            elif isinstance(node, Comment):
                comments.append(node)
            elif isinstance(node, Revision):
                fast.flush()
                _add_revision_paragraph(doc, node)
            elif isinstance(node, ContentControl):
                fast.flush()
                _add_content_control_sdt(doc, node)
            elif isinstance(node, TextBox):
                fast.flush()
                _add_textbox_paragraph(doc, node)
            elif isinstance(node, PivotTable):
                fast.flush()
                _add_pivot_table(doc, node)
            elif isinstance(node, HtmlBlock):
                import re

                text = re.sub(r"<[^>]+>", "", node.content).strip()
                if text:
                    fast.plain(text)
            elif isinstance(node, CodeBlock):
                fast.flush()
                p = doc.add_paragraph()
                run = p.add_run(node.content)
                run.font.name = "Courier New"
                run.font.size = Pt(10)
    finally:
        fast.finish()

    if footnotes:
        doc.add_heading("Footnotes", level=2)
        for fn in footnotes:
            p = doc.add_paragraph()
            ref = p.add_run(f"[{fn.id}] ")
            ref.font.superscript = True
            p.add_run(fn.text)

    buf = BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    if comments and settings.docx_export_comments:
        from app.exporters.docx_comments import inject_word_comments

        data = inject_word_comments(data, comments)
    return data


def _apply_theme(doc: Document, cfg: dict) -> None:
    try:
        normal = doc.styles["Normal"]
        normal.font.name = cfg["font"]
        normal.font.size = Pt(cfg["normal_size"])
    except Exception:
        pass
    for i in range(1, 7):
        try:
            style = doc.styles[f"Heading {i}"]
            style.font.color.rgb = cfg["heading"]
            style.font.name = cfg["font"]
        except Exception:
            pass


def _setup_header_footer(doc: Document, title: str) -> None:
    section = doc.sections[0]
    header = section.header
    hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    hp.text = title[:80]
    hp.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    for run in hp.runs:
        run.font.size = Pt(9)
        run.italic = True

    footer = section.footer
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    run = fp.add_run()
    # PAGE field
    fld_char_begin = OxmlElement("w:fldChar")
    fld_char_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    fld_char_end = OxmlElement("w:fldChar")
    fld_char_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_char_begin)
    run._r.append(instr)
    fld_char_separate = OxmlElement("w:fldChar")
    fld_char_separate.set(qn("w:fldCharType"), "separate")
    run._r.append(fld_char_separate)
    run._r.append(fld_char_end)


def _add_rich_paragraph(doc: Document, rich) -> None:
    p = doc.add_paragraph()
    _apply_rich_to_paragraph(p, rich)


def _apply_rich_to_paragraph(p, rich) -> None:
    if not rich.marks:
        p.add_run(rich.text)
        return

    for segment, active in _split_by_marks(rich):
        types = {m.type for m in active}
        if InlineMarkType.LINK in types:
            href = next((m.href for m in active if m.type == InlineMarkType.LINK), None)
            if href:
                _add_hyperlink(p, segment, href)
                continue
        run = p.add_run(segment)
        if InlineMarkType.BOLD in types:
            run.bold = True
        if InlineMarkType.ITALIC in types:
            run.italic = True
        if InlineMarkType.CODE in types:
            run.font.name = "Courier New"
            run.font.size = Pt(10)
        if InlineMarkType.STRIKETHROUGH in types:
            run.font.strike = True


def _add_hyperlink(paragraph, text: str, url: str) -> None:
    """Create a real clickable hyperlink relationship."""
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    new_run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    rPr.append(color)
    u = OxmlElement("w:u")
    u.set(qn("w:val"), "single")
    rPr.append(u)
    new_run.append(rPr)

    text_elem = OxmlElement("w:t")
    text_elem.text = text
    new_run.append(text_elem)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)


def _split_by_marks(rich) -> list[tuple[str, list[InlineMark]]]:
    boundaries = {0, len(rich.text)}
    for mark in rich.marks:
        boundaries.add(max(0, min(mark.start, len(rich.text))))
        boundaries.add(max(0, min(mark.end, len(rich.text))))
    points = sorted(boundaries)
    segments = []
    for start, end in zip(points, points[1:]):
        if start == end:
            continue
        active = [m for m in rich.marks if m.start <= start and m.end >= end]
        segments.append((rich.text[start:end], active))
    return segments


def _add_table(doc: Document, table: Table) -> None:
    rows = table.rows or (
        [[(c.text if c is not None else "") for c in row] for row in table.cells]
        if table.cells
        else []
    )
    if not rows:
        return
    cols = max(len(r) for r in rows)
    tbl = doc.add_table(rows=len(rows), cols=cols)
    tbl.style = "Table Grid"
    try:
        from docx.shared import Twips

        width = Twips(int(9000 / max(cols, 1)))
        for row in tbl.rows:
            for cell in row.cells:
                cell.width = width
    except Exception:
        pass

    for r_idx, row in enumerate(rows):
        for c_idx in range(cols):
            cell_text = row[c_idx] if c_idx < len(row) else ""
            cell = tbl.rows[r_idx].cells[c_idx]
            cell.text = str(cell_text)
            header_end = (
                table.header_rows
                if table.header_rows
                else (1 if settings.table_first_row_header else 0)
            )
            if r_idx < header_end:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.bold = True
                try:
                    tc = cell._tc
                    tcPr = tc.get_or_add_tcPr()
                    shd = OxmlElement("w:shd")
                    shd.set(qn("w:fill"), "F0F4F7")
                    shd.set(qn("w:val"), "clear")
                    tcPr.append(shd)
                except Exception:
                    pass

    if table.cells:
        for r_idx, row in enumerate(table.cells):
            for c_idx, meta in enumerate(row):
                if meta.covered:
                    continue
                if r_idx >= len(tbl.rows) or c_idx >= len(tbl.rows[r_idx].cells):
                    continue
                cell = tbl.rows[r_idx].cells[c_idx]
                if meta.col_span > 1:
                    end_c = min(c_idx + meta.col_span - 1, cols - 1)
                    if end_c > c_idx:
                        cell.merge(tbl.rows[r_idx].cells[end_c])
                if meta.row_span > 1:
                    end_r = min(r_idx + meta.row_span - 1, len(tbl.rows) - 1)
                    if end_r > r_idx:
                        cell.merge(tbl.rows[end_r].cells[c_idx])

    header_end = (
        table.header_rows
        if table.header_rows
        else (1 if settings.table_first_row_header else 0)
    )
    if header_end and len(rows) > header_end:
        try:
            tr = tbl.rows[0]._tr
            trPr = tr.get_or_add_trPr()
            tblHeader = OxmlElement("w:tblHeader")
            trPr.append(tblHeader)
        except Exception:
            pass


def _add_pivot_table(doc: Document, pivot: PivotTable) -> None:
    """Shallow pivot summary table (not a native Excel pivot rebuild)."""
    title = doc.add_paragraph()
    run = title.add_run(f"Pivot Table: {pivot.name or 'Pivot'}")
    run.bold = True
    rows_meta: list[tuple[str, str]] = []
    if pivot.location:
        rows_meta.append(("Location", pivot.location))
    if pivot.source_range:
        rows_meta.append(("Source range", pivot.source_range))
    if pivot.row_fields:
        rows_meta.append(("Row fields", ", ".join(pivot.row_fields)))
    if pivot.col_fields:
        rows_meta.append(("Column fields", ", ".join(pivot.col_fields)))
    if pivot.data_fields:
        rows_meta.append(("Value fields", ", ".join(pivot.data_fields)))
    if not rows_meta:
        rows_meta.append(("Fields", "(empty)"))
    tbl = doc.add_table(rows=len(rows_meta), cols=2)
    tbl.style = "Table Grid"
    for r_idx, (label, value) in enumerate(rows_meta):
        tbl.rows[r_idx].cells[0].text = label
        cell = tbl.rows[r_idx].cells[1]
        cell.text = value
        for paragraph in cell.paragraphs:
            for r in paragraph.runs:
                r.font.name = "Courier New"
                r.font.size = Pt(10)


def _add_revision_paragraph(doc: Document, rev: Revision) -> None:
    p = doc.add_paragraph()
    tag = "ins" if rev.change == "insert" else "del"
    ch = OxmlElement(f"w:{tag}")
    if rev.author:
        ch.set(qn("w:author"), rev.author)
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = rev.text
    r.append(t)
    ch.append(r)
    p._p.append(ch)


def _apply_section_props(doc: Document, model: DocumentModel) -> None:
    props = (model.metadata or {}).get("section_props") or {}
    if not props:
        return
    section = doc.sections[0]
    try:
        w = props.get("page_width_mm")
        h = props.get("page_height_mm")
        if w and h:
            section.page_width = Mm(float(w))
            section.page_height = Mm(float(h))
        margins = props.get("margins_mm") or {}
        if isinstance(margins, dict):
            for side in ("top", "right", "bottom", "left"):
                raw = margins.get(side)
                if raw is not None:
                    setattr(section, f"margin_{side}", Mm(float(raw)))
    except (TypeError, ValueError):
        pass


def _add_content_control_sdt(doc: Document, cc: ContentControl) -> None:
    """Export as w:sdt block (lightweight round-trip with import enrich)."""
    sdt = OxmlElement("w:sdt")
    sdt_pr = OxmlElement("w:sdtPr")
    if cc.tag:
        tag_el = OxmlElement("w:tag")
        tag_el.set(qn("w:val"), cc.tag)
        sdt_pr.append(tag_el)
    if cc.alias:
        alias_el = OxmlElement("w:alias")
        alias_el.set(qn("w:val"), cc.alias)
        sdt_pr.append(alias_el)
    if cc.control_type and cc.control_type not in ("richText", ""):
        ctype = cc.control_type.lower()
        if ctype in ("dropdown", "dropdownlist"):
            ddl = OxmlElement("w:dropDownList")
            item = OxmlElement("w:listItem")
            item.set(qn("w:displayText"), cc.text or "")
            item.set(qn("w:val"), cc.text or "")
            ddl.append(item)
            sdt_pr.append(ddl)
        elif ctype == "date":
            date_el = OxmlElement("w:date")
            fmt = OxmlElement("w:dateFormat")
            fmt.set(qn("w:val"), "yyyy/M/d")
            date_el.append(fmt)
            sdt_pr.append(date_el)
    sdt.append(sdt_pr)
    sdt_content = OxmlElement("w:sdtContent")
    inner_p = OxmlElement("w:p")
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = cc.text or ""
    r.append(t)
    inner_p.append(r)
    sdt_content.append(inner_p)
    sdt.append(sdt_content)
    doc.element.body.append(sdt)


def _add_content_control_paragraph(doc: Document, cc: ContentControl, theme_cfg: dict) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.2)
    label = cc.alias or cc.tag or "控件"
    run = p.add_run(f"{label}: {cc.text}")
    run.font.color.rgb = theme_cfg.get("heading", RGBColor(0x18, 0x5F, 0xA5))


def _add_textbox_paragraph(doc: Document, box: TextBox) -> None:
    """Export as w:txbxContent under w:r (matches docx_ooxml import enrich)."""
    p = doc.add_paragraph()
    r = p.add_run()
    txbx = OxmlElement("w:txbxContent")
    inner_p = OxmlElement("w:p")
    inner_r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = box.text or ""
    inner_r.append(t)
    inner_p.append(inner_r)
    txbx.append(inner_p)
    r._r.append(txbx)


def _add_image(doc: Document, image: Image) -> None:
    if not image.data:
        doc.add_paragraph(f"[Image: {image.alt_text}]")
        return
    try:
        buf = BytesIO(image.data)
        doc.add_picture(buf, width=Inches(5))
        if image.alt_text:
            cap = doc.add_paragraph(image.alt_text.split("|", 1)[0])
            cap.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    except Exception:
        doc.add_paragraph(f"[Image: {image.alt_text}]")
