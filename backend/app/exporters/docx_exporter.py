"""Export DocumentModel to DOCX using python-docx."""

from __future__ import annotations

from io import BytesIO

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from app.config import settings
from app.core.document_model import (
    Blockquote,
    CodeBlock,
    Comment,
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
    Paragraph,
    Table,
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

    comments: list[Comment] = []
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
                fast.plain(f"[^{node.id}]: {node.text}")
            elif isinstance(node, Comment):
                comments.append(node)
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

    buf = BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    if comments:
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
    if not table.rows:
        return
    cols = max(len(r) for r in table.rows)
    tbl = doc.add_table(rows=len(table.rows), cols=cols)
    tbl.style = "Table Grid"
    # Even column widths (~6.5" usable)
    try:
        from docx.shared import Twips

        width = Twips(int(9000 / max(cols, 1)))
        for row in tbl.rows:
            for cell in row.cells:
                cell.width = width
    except Exception:
        pass

    for r_idx, row in enumerate(table.rows):
        for c_idx in range(cols):
            cell_text = row[c_idx] if c_idx < len(row) else ""
            cell = tbl.rows[r_idx].cells[c_idx]
            cell.text = str(cell_text)
            if r_idx == 0 and settings.table_first_row_header:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.bold = True
                # light header shading
                try:
                    tc = cell._tc
                    tcPr = tc.get_or_add_tcPr()
                    shd = OxmlElement("w:shd")
                    shd.set(qn("w:fill"), "F0F4F7")
                    shd.set(qn("w:val"), "clear")
                    tcPr.append(shd)
                except Exception:
                    pass

    # Repeat header row on each page
    if settings.table_first_row_header and len(table.rows) > 1:
        try:
            tr = tbl.rows[0]._tr
            trPr = tr.get_or_add_trPr()
            tblHeader = OxmlElement("w:tblHeader")
            trPr.append(tblHeader)
        except Exception:
            pass


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
