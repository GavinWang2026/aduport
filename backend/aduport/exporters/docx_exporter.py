"""Export DocumentModel to DOCX using python-docx."""

from __future__ import annotations

import logging
import re
from io import BytesIO

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK, WD_PARAGRAPH_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt, RGBColor

from aduport.config import settings
from aduport.core.document_model import (
    Blockquote,
    Bookmark,
    Chart,
    CodeBlock,
    Comment,
    ContentControl,
    DocumentModel,
    FieldCode,
    Footnote,
    HeaderFooter,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    InlineMark,
    InlineMarkType,
    ListItem,
    MathBlock,
    Numbering,
    OleObject,
    PageBreak,
    PageSetup,
    Paragraph,
    PivotTable,
    Revision,
    RunStyle,
    Section,
    SmartArt,
    SpeakerNote,
    Table,
    TextBox,
    ensure_rich_text,
)
from aduport.core.title_source import title_repeats_leading_heading

_log = logging.getLogger("aduport.docx")

_PAGE_SIZES_MM = {
    "a4": (210.0, 297.0),
    "letter": (215.9, 279.4),
    "legal": (215.9, 355.6),
    "a3": (297.0, 420.0),
    "a5": (148.0, 210.0),
}

_THEMES = {
    "default": {
        "heading": RGBColor(0x1A, 0x56, 0xDB),
        "normal_size": 11,
        "font": "Calibri",
        "accent": "1A56DB",
        "theme_name": "AduPort-default",
    },
    "academic": {
        "heading": RGBColor(0x2C, 0x3E, 0x50),
        "normal_size": 12,
        "font": "Times New Roman",
        "accent": "2C3E50",
        "theme_name": "AduPort-academic",
    },
    "minimal": {
        "heading": RGBColor(0x22, 0x22, 0x22),
        "normal_size": 11,
        "font": "Arial",
        "accent": "222222",
        "theme_name": "AduPort-minimal",
    },
}


def export_docx(
    model: DocumentModel,
    *,
    theme: str | None = None,
    auto_toc: bool | None = None,
) -> bytes:
    from aduport.core.exceptions import ExportError

    max_nodes = int(getattr(settings, "docx_max_nodes", 0) or 0)
    if max_nodes > 0 and len(model.nodes) > max_nodes:
        raise ExportError(
            f"文档节点数 {len(model.nodes)} 超过 DOCX 导出上限 {max_nodes}。"
            "请拆分文档，或提高 ADUPORT_DOCX_MAX_NODES（企业批量场景建议分片）。",
            code="docx_too_large",
        )
    doc = Document()
    theme_key = (theme or settings.docx_theme or "default").lower().strip()
    theme_cfg = _THEMES.get(theme_key, _THEMES["default"])
    _apply_theme(doc, theme_cfg)

    page_setups = [n for n in model.nodes if isinstance(n, PageSetup)]
    sections = [n for n in model.nodes if isinstance(n, Section)]
    headers_footers = [n for n in model.nodes if isinstance(n, HeaderFooter)]
    run_styles = [n for n in model.nodes if isinstance(n, RunStyle)]
    numberings = [n for n in model.nodes if isinstance(n, Numbering)]

    # First section chrome (subsequent Section nodes open mid-body sectPr breaks).
    _apply_page_setup(doc, page_setups[0] if page_setups else None, model)
    _apply_section_ast_props(doc, sections[0] if sections else None, model)
    _setup_header_footer(
        doc,
        model.title or "AduPort",
        headers_footers=headers_footers,
    )
    _patch_theme_part(doc, theme_key, theme_cfg)
    for rs in run_styles[:50]:
        _add_run_style(doc, rs)
    for num in numberings[:50]:
        _add_numbering_definition(doc, num)

    comments: list[Comment] = []
    footnotes: list[Footnote] = []
    smartarts_out: list[SmartArt] = []
    oles_out: list[OleObject] = []
    charts_out: list[Chart] = []
    _ensure_paragraph_style(doc, "Quote")
    _ensure_paragraph_style(
        doc,
        "AduPort Code",
        font_name="Courier New",
        size_pt=10,
        shading="F4F4F5",
        left_indent_pt=12,
        space_before_pt=6,
        space_after_pt=6,
    )
    if model.title and not title_repeats_leading_heading(model):
        doc.add_heading(model.title, level=0)

    has_headings = any(isinstance(n, Heading) for n in model.nodes)
    has_explicit_toc = any(
        isinstance(n, FieldCode) and (n.field_type or "").upper() == "TOC" for n in model.nodes
    )
    from aduport.core.export_flags import export_flag

    toc_flag = auto_toc if auto_toc is not None else export_flag("docx_auto_toc")
    toc_on = bool(settings.docx_auto_toc if toc_flag is None else toc_flag)
    if toc_on and has_headings and not has_explicit_toc:
        _add_toc_field(doc)

    body = doc.element.body
    from aduport.exporters.docx_oxml_fast import FastBody

    fast = FastBody(body, doc=doc)
    bookmark_id = 0
    section_i = 0
    try:
        for node in model.nodes:
            if isinstance(node, PageBreak):
                fast.flush()
                break_p = doc.add_paragraph()
                break_p.add_run().add_break(WD_BREAK.PAGE)
                continue
            if isinstance(
                node, (PageSetup, HeaderFooter, RunStyle, Numbering)
            ):
                # Applied in pre-pass / paired with Section breaks.
                continue
            if isinstance(node, Section):
                if section_i > 0:
                    fast.flush()
                    break_key = str((node.props or {}).get("break") or "new_page").lower()
                    start = {
                        "continuous": WD_SECTION.CONTINUOUS,
                        "new_column": WD_SECTION.NEW_COLUMN,
                        "even_page": WD_SECTION.EVEN_PAGE,
                        "odd_page": WD_SECTION.ODD_PAGE,
                    }.get(break_key, WD_SECTION.NEW_PAGE)
                    new_sec = doc.add_section(start)
                    page = page_setups[section_i] if section_i < len(page_setups) else None
                    _configure_section(new_sec, page, node)
                if node.title:
                    fast.flush()
                    doc.add_heading(node.title, level=1)
                section_i += 1
                continue
            if isinstance(node, Heading):
                fast.heading(node.plain_text(), node.level)
            elif isinstance(node, FieldCode):
                fast.flush()
                if (node.field_type or "").upper() == "TOC" or "TOC" in (
                    node.instruction or ""
                ).upper():
                    _add_toc_field(doc, instruction=node.instruction or None)
                else:
                    _add_generic_field(
                        doc,
                        node.instruction or node.field_type or "PAGE",
                        result=node.result_text,
                    )
            elif isinstance(node, Bookmark):
                fast.flush()
                bookmark_id += 1
                _add_bookmark(doc, node, bookmark_id)
            elif isinstance(node, Chart):
                fast.flush()
                charts_out.append(node)
                from aduport.exporters.docx_chart import chart_placeholder

                doc.add_paragraph(chart_placeholder(len(charts_out)))
            elif isinstance(node, SpeakerNote):
                fast.flush()
                p = doc.add_paragraph(
                    f"[SpeakerNote s{node.slide_index}] {node.text}"
                )
                if p.runs:
                    p.runs[0].italic = True
            elif isinstance(node, SmartArt):
                fast.flush()
                smartarts_out.append(node)
                _add_smartart(doc, node)
            elif isinstance(node, OleObject):
                fast.flush()
                oles_out.append(node)
                _add_ole_object(doc, node)
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
                    p = doc.add_paragraph(style="Quote")
                    p.paragraph_format.left_indent = Inches(0.25)
                    # v6/T2-#10 / D-12: do NOT force italic — that corrupted
                    # blockquote → italic round-trips. Indent alone marks quote.
                    _apply_rich_to_paragraph(p, ensure_rich_text(para))
            elif isinstance(node, HorizontalRule):
                # v6/T2-#10: real bottom-border para (not a visible "---" glyph).
                # Keep a vanished "---" run so AST×exporter sentinels stay greppable.
                fast.flush()
                p = doc.add_paragraph()
                pPr = p._p.get_or_add_pPr()
                from docx.oxml import OxmlElement
                from docx.oxml.ns import qn

                pBdr = OxmlElement("w:pBdr")
                bottom = OxmlElement("w:bottom")
                bottom.set(qn("w:val"), "single")
                bottom.set(qn("w:sz"), "6")
                bottom.set(qn("w:space"), "1")
                bottom.set(qn("w:color"), "auto")
                pBdr.append(bottom)
                pPr.append(pBdr)
                marker = p.add_run("---")
                marker.font.hidden = True
            elif isinstance(node, ListItem):
                rich = ensure_rich_text(node.text)
                if rich.marks:
                    fast.flush()
                    style = "List Number" if node.ordered else "List Bullet"
                    p = doc.add_paragraph(style=style)
                    if node.checked is not None:
                        p.add_run("[x] " if node.checked else "[ ] ")
                    _apply_rich_to_paragraph(p, rich)
                    level = max(0, min(int(node.level), 8))
                    if level > 0:
                        p.paragraph_format.left_indent = Inches(0.25 * level)
                else:
                    fast.bullet(
                        rich.text,
                        level=node.level,
                        checked=node.checked,
                        ordered=bool(node.ordered),
                    )
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
                try:
                    from aduport.exporters.omml import append_math_to_paragraph

                    append_math_to_paragraph(
                        p, node.content or "", display=bool(node.display)
                    )
                except Exception:
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
                p = doc.add_paragraph(style="AduPort Code")
                if (node.lang or "").strip():
                    marker = p.add_run(f"aduport-lang:{node.lang.strip()}\n")
                    marker.font.hidden = True
                    marker.font.name = "Courier New"
                run = p.add_run(node.content or "")
                run.font.name = "Courier New"
                run.font.size = Pt(10)
    finally:
        fast.finish()

    buf = BytesIO()
    doc.save(buf)
    data = buf.getvalue()
    data = _rewrite_theme_in_docx(data, theme_key, theme_cfg)
    if smartarts_out:
        from aduport.exporters.docx_diagram import inject_smartart_package

        data = inject_smartart_package(data, smartarts_out)
    if oles_out:
        from aduport.exporters.docx_diagram import inject_ole_embeddings

        data = inject_ole_embeddings(data, oles_out)
    if comments and settings.docx_export_comments:
        from aduport.exporters.docx_comments import inject_word_comments

        data = inject_word_comments(data, comments)
    if footnotes:
        from aduport.exporters.docx_footnotes import inject_word_footnotes

        data = inject_word_footnotes(data, footnotes)
    if charts_out:
        from aduport.exporters.docx_chart import inject_word_charts

        data = inject_word_charts(data, charts_out)
    from aduport.core.reproducible_zip import stabilize_zip_timestamps

    return stabilize_zip_timestamps(data)


def _rewrite_theme_in_docx(data: bytes, theme_key: str, cfg: dict) -> bytes:
    """Post-save ZIP rewrite of theme1.xml (python-docx ignores in-memory theme blob)."""
    try:
        import zipfile as _zf
        from io import BytesIO as _BIO

        name = cfg.get("theme_name") or f"AduPort-{theme_key}"
        accent = (cfg.get("accent") or "1A56DB").upper()
        src = _zf.ZipFile(_BIO(data), "r")
        out_buf = _BIO()
        with _zf.ZipFile(out_buf, "w", compression=_zf.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                raw = src.read(info.filename)
                if info.filename.endswith("theme1.xml") and "theme" in info.filename:
                    text = raw.decode("utf-8", "ignore")
                    text, n = re.subn(
                        r'(<a:theme\b[^>]*\bname=")([^"]*)(")',
                        r"\g<1>" + name + r"\g<3>",
                        text,
                        count=1,
                    )
                    if n == 0:
                        text = text.replace(
                            "<a:theme ", f'<a:theme name="{name}" ', 1
                        )
                    text, _ = re.subn(
                        r'(<a:accent1>\s*<a:srgbClr\s+val=")([0-9A-Fa-f]{6})(")',
                        r"\g<1>" + accent + r"\g<3>",
                        text,
                        count=1,
                    )
                    font = str(cfg.get("font") or "Calibri")
                    # major/minor latin in fontScheme (first two latin typefaces)
                    latin_i = 0

                    def _latin_sub(m: re.Match[str]) -> str:
                        nonlocal latin_i
                        latin_i += 1
                        if latin_i <= 4:
                            return m.group(1) + font + m.group(3)
                        return m.group(0)

                    text = re.sub(
                        r'(<a:latin\s+typeface=")([^"]*)(")',
                        _latin_sub,
                        text,
                    )
                    raw = text.encode("utf-8")
                # Write by name — avoid stale ZipInfo.file_size/CRC truncating new blob
                dst.writestr(info.filename, raw)
        src.close()
        return out_buf.getvalue()
    except Exception:
        return data


def _apply_theme(doc: Document, cfg: dict) -> None:
    try:
        normal = doc.styles["Normal"]
        normal.font.name = cfg["font"]
        normal.font.size = Pt(cfg["normal_size"])
    except Exception as exc:
        _log.warning("docx theme: Normal style apply failed: %s", exc)
    for i in range(1, 7):
        try:
            style = doc.styles[f"Heading {i}"]
            style.font.color.rgb = cfg["heading"]
            style.font.name = cfg["font"]
        except Exception as exc:
            _log.debug("docx theme: Heading %s style apply failed: %s", i, exc)
    # Ensure a named body style exists for theme catalog depth
    try:
        names = {s.name for s in doc.styles}
        if "AduPort Body" not in names:
            body = doc.styles.add_style("AduPort Body", WD_STYLE_TYPE.PARAGRAPH)
            body.font.name = cfg["font"]
            body.font.size = Pt(cfg["normal_size"])
    except Exception as exc:
        _log.warning("docx theme: AduPort Body style create failed: %s", exc)


def _patch_theme_part(doc: Document, theme_key: str, cfg: dict) -> None:
    """No-op placeholder kept for call-site compatibility; real write is post-save."""
    return


def _configure_section(section, page: PageSetup | None, section_node: Section | None) -> None:
    """Apply PageSetup + Section.props onto an existing python-docx Section."""
    if page is not None:
        key = (page.page_size or "A4").strip().lower()
        size = _PAGE_SIZES_MM.get(key)
        if size:
            w_mm, h_mm = size
            if page.orientation == "landscape":
                w_mm, h_mm = h_mm, w_mm
            section.page_width = Mm(w_mm)
            section.page_height = Mm(h_mm)
            section.orientation = (
                WD_ORIENT.LANDSCAPE if page.orientation == "landscape" else WD_ORIENT.PORTRAIT
            )
        if page.margins_mm:
            for side in ("top", "right", "bottom", "left"):
                raw = page.margins_mm.get(side)
                if raw is not None:
                    try:
                        setattr(section, f"{side}_margin", Mm(float(raw)))
                    except (TypeError, ValueError):
                        pass
    if section_node is not None:
        _write_cols(section._sectPr, section_node.props or {})


def _write_cols(sect_pr, props: dict) -> None:
    cols_num = props.get("cols")
    if cols_num is None:
        return
    try:
        n = int(cols_num)
    except (TypeError, ValueError):
        return
    if n < 2:
        return
    for old in sect_pr.xpath("./w:cols"):
        sect_pr.remove(old)
    cols = OxmlElement("w:cols")
    cols.set(qn("w:num"), str(n))
    space = props.get("col_space_twips", 720)
    cols.set(qn("w:space"), str(int(space)))
    sect_pr.append(cols)


def _apply_page_setup(
    doc: Document, page: PageSetup | None, model: DocumentModel
) -> None:
    """Map PageSetup AST (+ legacy metadata.section_props) onto the first section."""
    section = doc.sections[0]
    if page is not None:
        _configure_section(section, page, None)
        return

    props = dict((model.metadata or {}).get("section_props") or {})
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
                    setattr(section, f"{side}_margin", Mm(float(raw)))
        if props.get("orientation") == "landscape":
            section.orientation = WD_ORIENT.LANDSCAPE
    except (TypeError, ValueError):
        pass


def _apply_section_ast_props(
    doc: Document, section_node: Section | None, model: DocumentModel
) -> None:
    """Write w:cols / extra sectPr from Section.props onto first section."""
    props: dict = {}
    if section_node and isinstance(section_node.props, dict):
        props.update(section_node.props)
    meta = (model.metadata or {}).get("section_props") or {}
    if isinstance(meta, dict):
        for k in ("cols", "col_space_twips"):
            if k in meta and k not in props:
                props[k] = meta[k]
    _write_cols(doc.sections[0]._sectPr, props)


def _add_field_run(paragraph, instruction: str, result: str = "") -> None:
    """Append a complex field (begin / instrText / separate / result / end)."""
    run = paragraph.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction.strip()} "
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    run._r.append(fld_begin)
    run._r.append(instr)
    run._r.append(fld_sep)
    if result:
        run2 = paragraph.add_run(result)
        run2.italic = True
    run3 = paragraph.add_run()
    run3._r.append(fld_end)


def _ensure_paragraph_style(
    doc: Document,
    name: str,
    *,
    font_name: str | None = None,
    size_pt: float | None = None,
    shading: str | None = None,
    left_indent_pt: float | None = None,
    space_before_pt: float | None = None,
    space_after_pt: float | None = None,
):
    """Create a paragraph style when the default template does not ship it."""
    try:
        style = doc.styles[name]
    except KeyError:
        style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    if font_name:
        style.font.name = font_name
    if size_pt:
        style.font.size = Pt(size_pt)
    pPr = style.element.get_or_add_pPr()
    if shading:
        # Replace any existing shade so re-export stays idempotent.
        for old in pPr.findall(qn("w:shd")):
            pPr.remove(old)
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), shading)
        pPr.append(shd)
    if left_indent_pt is not None:
        ind = pPr.find(qn("w:ind"))
        if ind is None:
            ind = OxmlElement("w:ind")
            pPr.append(ind)
        ind.set(qn("w:left"), str(int(left_indent_pt * 20)))
    if space_before_pt is not None or space_after_pt is not None:
        spacing = pPr.find(qn("w:spacing"))
        if spacing is None:
            spacing = OxmlElement("w:spacing")
            pPr.append(spacing)
        if space_before_pt is not None:
            spacing.set(qn("w:before"), str(int(space_before_pt * 20)))
        if space_after_pt is not None:
            spacing.set(qn("w:after"), str(int(space_after_pt * 20)))
    return style


def _add_toc_field(doc: Document, instruction: str | None = None) -> None:
    """Insert an automatic Table of Contents field (updates on open in Word)."""
    p = doc.add_paragraph()
    instr = instruction or r'TOC \o "1-3" \h \z \u'
    _add_field_run(p, instr, result="[TOC]")
    # Trailing paragraph so body content does not glue to the field
    doc.add_paragraph()


def _add_generic_field(doc: Document, instruction: str, result: str = "") -> None:
    p = doc.add_paragraph()
    _add_field_run(p, instruction, result=result)


def _setup_header_footer(
    doc: Document,
    title: str,
    *,
    headers_footers: list[HeaderFooter] | None = None,
) -> None:
    section = doc.sections[0]
    headers_footers = headers_footers or []
    hdr = next((h for h in reversed(headers_footers) if h.kind == "header"), None)
    ftr = next((h for h in reversed(headers_footers) if h.kind == "footer"), None)

    header = section.header
    hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    hp.text = (hdr.text if hdr and hdr.text else title)[:200]
    hp.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    for run in hp.runs:
        run.font.size = Pt(9)
        run.italic = True

    footer = section.footer
    fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    fp.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
    # Clear default empty run text if we will rebuild
    if fp.runs:
        for r in list(fp.runs):
            r._r.getparent().remove(r._r)

    if ftr and ftr.text:
        run = fp.add_run(ftr.text[:200])
        run.font.size = Pt(9)
        # Keep a PAGE field after custom footer text for pagination.
        fp.add_run(" ")
        _add_field_run(fp, "PAGE", result="")
    else:
        _add_field_run(fp, "PAGE", result="")


def _apply_section_props(doc: Document, model: DocumentModel) -> None:
    """Backward-compatible wrapper (metadata-only callers)."""
    _apply_page_setup(doc, None, model)
    _apply_section_ast_props(doc, None, model)


def _add_bookmark(doc: Document, node: Bookmark, bid: int) -> None:
    name = (node.name or node.anchor or f"bm{bid}").strip() or f"bm{bid}"
    # OOXML bookmark names: no spaces; keep alnum/_
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)[:40]
    p = doc.add_paragraph()
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bid))
    start.set(qn("w:name"), safe)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bid))
    p._p.append(start)
    if node.anchor and node.anchor != node.name:
        run = p.add_run(node.anchor)
        run.font.size = Pt(1)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    elif node.name:
        # Zero-width visible sentinel for greppable round-trip without layout impact
        run = p.add_run(f"[{safe}]")
        run.font.size = Pt(1)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    p._p.append(end)


def _add_run_style(doc: Document, style: RunStyle) -> None:
    style_id = (style.style_id or style.name or "AduPortChar").strip()
    if not style_id:
        return
    # python-docx style names must be unique; skip if present
    try:
        existing = {s.style_id for s in doc.styles if getattr(s, "style_id", None)}
        existing |= {s.name for s in doc.styles}
        if style_id in existing or (style.name and style.name in existing):
            return
        new = doc.styles.add_style(style.name or style_id, WD_STYLE_TYPE.CHARACTER)
        # Force styleId when possible
        try:
            new._element.set(qn("w:styleId"), style_id)
        except Exception:
            pass
        props = style.props or {}
        if props.get("bold"):
            new.font.bold = True
        if props.get("italic"):
            new.font.italic = True
        if props.get("strike") or props.get("strikethrough"):
            new.font.strike = True
        size_pt = props.get("size_pt") or props.get("size")
        if size_pt is not None:
            try:
                new.font.size = Pt(float(size_pt))
            except (TypeError, ValueError):
                pass
        font_name = props.get("font") or props.get("font_name")
        if font_name:
            new.font.name = str(font_name)
    except Exception:
        pass


def _add_numbering_definition(doc: Document, node: Numbering) -> None:
    """Append abstractNum + num entries from Numbering AST into word/numbering.xml."""
    try:
        numbering = doc.part.numbering_part.numbering_definitions._numbering
    except Exception:
        return
    try:
        abs_id = int(node.abstract_num_id) if str(node.abstract_num_id).strip() else None
    except ValueError:
        abs_id = None
    try:
        num_id = int(node.num_id) if str(node.num_id).strip() else None
    except ValueError:
        num_id = None
    if abs_id is None or num_id is None:
        # Allocate high ids to avoid colliding with python-docx 1/2
        used_abs = {
            int(el.get(qn("w:abstractNumId")))
            for el in numbering.xpath("./w:abstractNum")
            if el.get(qn("w:abstractNumId")) is not None
        }
        used_num = {
            int(el.get(qn("w:numId")))
            for el in numbering.xpath("./w:num")
            if el.get(qn("w:numId")) is not None
        }
        abs_id = abs_id if abs_id is not None else (max(used_abs | {0}) + 1)
        num_id = num_id if num_id is not None else (max(used_num | {0}) + 1)

    # Skip duplicate numId
    for el in numbering.xpath("./w:num"):
        if el.get(qn("w:numId")) == str(num_id):
            return

    abs_n = OxmlElement("w:abstractNum")
    abs_n.set(qn("w:abstractNumId"), str(abs_id))
    multi = OxmlElement("w:multiLevelType")
    multi.set(qn("w:val"), "hybridMultilevel")
    abs_n.append(multi)

    levels = node.levels or [{"ilvl": 0, "fmt": "decimal", "text": "%1."}]
    for lvl_spec in levels[:9]:
        ilvl = int(lvl_spec.get("ilvl", 0))
        lvl = OxmlElement("w:lvl")
        lvl.set(qn("w:ilvl"), str(ilvl))
        start = OxmlElement("w:start")
        start.set(qn("w:val"), str(lvl_spec.get("start", 1)))
        lvl.append(start)
        num_fmt = OxmlElement("w:numFmt")
        num_fmt.set(qn("w:val"), str(lvl_spec.get("fmt") or lvl_spec.get("numFmt") or "decimal"))
        lvl.append(num_fmt)
        lvl_text = OxmlElement("w:lvlText")
        lvl_text.set(qn("w:val"), str(lvl_spec.get("text") or lvl_spec.get("lvlText") or f"%{ilvl + 1}."))
        lvl.append(lvl_text)
        abs_n.append(lvl)

    numbering.append(abs_n)
    num = OxmlElement("w:num")
    num.set(qn("w:numId"), str(num_id))
    abs_ref = OxmlElement("w:abstractNumId")
    abs_ref.set(qn("w:val"), str(abs_id))
    num.append(abs_ref)
    numbering.append(num)


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


_HYPERLINK_RELTYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)


def _external_hyperlink_rid(part, url: str) -> str:
    """Reuse one rId per URL and append it without scanning every relationship.

    python-docx ``get_or_add_ext_rel`` walks the whole relationship table for
    every link, so n unique links cost O(n²) comparisons.
    """
    cache = getattr(part, "_aduport_href_rids", None)
    if cache is None:
        cache = {}
        part._aduport_href_rids = cache
    hit = cache.get(url)
    if hit:
        return hit
    rels = part.rels
    seq = int(getattr(part, "_aduport_rid_seq", len(rels)) or 0)
    while True:
        seq += 1
        r_id = f"rId{seq}"
        if r_id not in rels:
            break
    part._aduport_rid_seq = seq
    rels.add_relationship(_HYPERLINK_RELTYPE, url, r_id, is_external=True)
    cache[url] = r_id
    return r_id


def _add_hyperlink(paragraph, text: str, url: str) -> None:
    """Create a real clickable hyperlink relationship."""
    part = paragraph.part
    r_id = _external_hyperlink_rid(part, url)
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
    """Split a run into segments covered by the same marks, in O(n) for disjoint marks.

    The previous scan re-checked every mark for every segment, so a paragraph
    with n links cost O(n²) before any relationship was written.
    """
    text = rich.text or ""
    n = len(text)
    if not rich.marks:
        return [(text, [])] if text else []
    events: list[tuple[int, int, InlineMark]] = []
    for mark in rich.marks:
        start = max(0, min(mark.start, n))
        end = max(0, min(mark.end, n))
        if end <= start:
            continue
        events.append((start, 1, mark))
        events.append((end, 0, mark))
    events.sort(key=lambda item: (item[0], item[1]))
    segments: list[tuple[str, list[InlineMark]]] = []
    active: list[InlineMark] = []
    prev = 0
    for pos, kind, mark in events:
        if pos > prev:
            segments.append((text[prev:pos], list(active)))
            prev = pos
        if kind == 0:
            active = [item for item in active if item is not mark]
        else:
            active.append(mark)
    if prev < n:
        segments.append((text[prev:], list(active)))
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


def _add_smartart(doc: Document, node: SmartArt) -> None:
    from aduport.exporters.docx_diagram import append_smartart_drawing

    title = node.title or node.layout or "SmartArt"
    cap = doc.add_paragraph(f"[SmartArt:{title}]")
    if cap.runs:
        cap.runs[0].bold = True
    p = doc.add_paragraph()
    try:
        append_smartart_drawing(p, node, shape_id=10 + len(doc.paragraphs))
    except Exception:
        for item in node.items[:50]:
            doc.add_paragraph(item, style="List Bullet")


def _add_ole_object(doc: Document, node: OleObject) -> None:
    """Visible label; binary embedding injected in post-pass."""
    p = doc.add_paragraph(node.plain_text())
    if p.runs:
        p.runs[0].italic = True
