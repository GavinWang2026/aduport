"""Post-pass OOXML enrichment for DOCX import (comments / headers / theme).

Runs on the raw ZIP bytes after (or without) docling body extraction so
Word Review comments, section headers, and theme accent survive into AST.
"""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from aduport.core import xml_safe
from aduport.core.document_model import (
    Bookmark,
    Chart,
    Comment,
    ContentControl,
    DocumentModel,
    FieldCode,
    Footnote,
    HeaderFooter,
    MathBlock,
    Numbering,
    OleObject,
    PageSetup,
    Revision,
    RunStyle,
    Section,
    SmartArt,
    TextBox,
)
from aduport.core.exceptions import FileTooLargeError
from aduport.core.safe_zip import read_zip_member, zip_inflate_session
from aduport.importers.import_guard import swallow_import_error

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def _math_key(tex: str) -> str:
    return re.sub(r"\s+", "", tex or "")


def _math_already_inline(model: DocumentModel, tex: str) -> bool:
    needle = (tex or "").strip()
    if not needle:
        return True
    wanted = _math_key(needle)
    for node in model.nodes:
        if isinstance(node, MathBlock) and _math_key(node.content) == wanted:
            return True
        plain = getattr(node, "plain_text", None)
        blob = ""
        if callable(plain):
            try:
                blob = plain() or ""
            except Exception:
                blob = ""
        if needle in blob or (wanted and wanted in _math_key(blob)):
            return True
    return False


def _section_key(section: Section) -> str:
    try:
        props = json.dumps(section.props or {}, sort_keys=True, default=str)
    except TypeError:
        props = str(section.props)
    return f"{section.title}\0{props}"


def _page_key(page: PageSetup) -> tuple:
    margins = tuple(
        sorted((str(k), float(v)) for k, v in (page.margins_mm or {}).items())
    )
    return (page.page_size, page.orientation, margins)


def _chart_key(chart: Chart) -> tuple:
    series: list[tuple] = []
    for ser in chart.series or []:
        if isinstance(ser, dict):
            vals = ser.get("values") if "values" in ser else ser.get("data")
            series.append((str(ser.get("name") or ""), tuple(vals or ())))
        else:
            series.append((str(ser), ()))
    return (chart.title, chart.chart_type, tuple(chart.categories or []), tuple(series))


_BODY_TYPES = frozenset(
    {
        "paragraph",
        "heading",
        "list_item",
        "table",
        "blockquote",
        "code_block",
        "image",
        "math_block",
        "html_block",
    }
)


def _place_body_chrome(model: DocumentModel, node) -> None:
    """Insert chrome in front of the body when this pass did not already place it."""
    pos = len(model.nodes)
    for i, existing in enumerate(model.nodes):
        if getattr(existing, "type", "") in _BODY_TYPES:
            pos = i
            break
    model.nodes.insert(pos, node)


def enrich_docx_model(model: DocumentModel, source_bytes: bytes) -> DocumentModel:
    """Mutate/return model with OOXML-derived comments, revisions, section props, theme."""
    footnotes: list[Footnote] = []
    charts: list = []
    chart_positions: list[int] = []
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            # N-01: one cumulative inflate budget for all OOXML part reads.
            with zip_inflate_session() as budget:
                budget.bind(zf)
                names = set(zf.namelist())
                comments = _parse_comments(zf) if "word/comments.xml" in names else []
                revisions = _parse_track_changes(zf) if "word/document.xml" in names else []
                content_controls = _parse_content_controls(zf) if "word/document.xml" in names else []
                field_codes = _parse_field_codes(zf) if "word/document.xml" in names else []
                text_boxes = _parse_text_boxes(zf) if "word/document.xml" in names else []
                section_props = _parse_section_props(zf) if "word/document.xml" in names else {}
                sections = _parse_sections(zf) if "word/document.xml" in names else []
                bookmarks = _parse_bookmarks(zf) if "word/document.xml" in names else []
                math_blocks = _parse_omml_math(zf) if "word/document.xml" in names else []
                smartarts = _parse_smartart(zf, names)
                ole_objects = _parse_ole_objects(zf) if "word/document.xml" in names else []
                header_text = _parse_headers(zf, names)
                header_footers = _parse_header_footer_nodes(zf, names)
                run_styles = _parse_run_styles(zf) if "word/styles.xml" in names else []
                numberings = _parse_numbering(zf) if "word/numbering.xml" in names else []
                footnotes = (
                    _parse_footnotes(zf) if "word/footnotes.xml" in names else []
                )
                charts: list = []
                chart_positions: list[int] = []
                if any(n.startswith("word/charts/chart") for n in names):
                    from aduport.exporters.docx_chart import parse_docx_charts

                    charts = parse_docx_charts(zf)
                    chart_positions = _chart_body_indexes(zf)
                theme = _parse_theme_name(zf) if "word/theme/theme1.xml" in names else ""
    except zipfile.BadZipFile:
        return model
    except FileTooLargeError:
        raise

    meta = dict(model.metadata or {})
    if theme:
        meta["docx_theme"] = theme
    if section_props:
        meta["section_props"] = section_props
    if sections:
        meta["docx_sections"] = sections[:20]
    if field_codes:
        meta["docx_field_codes"] = field_codes[:50]
    if meta:
        model.metadata = meta

    if header_text:
        meta = dict(model.metadata or {})
        meta["docx_header"] = header_text[:500]
        model.metadata = meta
    seen_hf = {(hf.kind, (hf.text or "").strip()) for hf in model.nodes if isinstance(hf, HeaderFooter)}
    header_insert = 0
    for hf in header_footers:
        key = (hf.kind, (hf.text or "").strip())
        if key in seen_hf:
            continue
        # Headers live outside w:body; keep them in front of the body nodes.
        model.nodes.insert(header_insert, hf)
        header_insert += 1
        seen_hf.add(key)
    if not header_footers and header_text and ("header", header_text[:500].strip()) not in seen_hf:
        # v6/T2-#8 / D-05: also emit a real HeaderFooter AST node (was metadata-only).
        model.nodes.insert(header_insert, HeaderFooter(kind="header", text=header_text[:500]))
    # v9: keep run/numbering catalogs in metadata for JSON fidelity; still append a
    # capped AST sample so depth tests see live RunStyle/Numbering nodes — but the
    # full 80-entry dump no longer floods Markdown exporters (see md_exporter skip).
    if run_styles:
        meta = dict(model.metadata or {})
        meta["docx_run_styles_count"] = len(run_styles)
        model.metadata = meta
    fresh_styles = []
    seen_styles = {(rs.style_id, rs.name) for rs in model.nodes if isinstance(rs, RunStyle)}
    for rs in run_styles[:12]:
        key = (rs.style_id, rs.name)
        if key in seen_styles:
            continue
        fresh_styles.append(rs)
        seen_styles.add(key)
    _insert_before_body(model, fresh_styles)
    if numberings:
        meta = dict(model.metadata or {})
        meta["docx_numberings_count"] = len(numberings)
        model.metadata = meta
    fresh_nums = []
    seen_nums = {(n.num_id, n.abstract_num_id) for n in model.nodes if isinstance(n, Numbering)}
    for num in numberings[:8]:
        key = (num.num_id, num.abstract_num_id)
        if key in seen_nums:
            continue
        fresh_nums.append(num)
        seen_nums.add(key)
    _insert_before_body(model, fresh_nums)

    # v6/T2-#8: promote section / page / field / bookmark metadata into AST nodes
    # so zombie rate drops and exporters can round-trip chrome.
    seen_sections = {_section_key(s) for s in model.nodes if isinstance(s, Section)}
    seen_pages = {_page_key(p) for p in model.nodes if isinstance(p, PageSetup)}
    for props in sections[:20]:
        if not isinstance(props, dict):
            continue
        for node in nodes_from_section_props(props):
            if isinstance(node, Section):
                skey = _section_key(node)
                if skey in seen_sections:
                    continue
                _place_body_chrome(model, node)
                seen_sections.add(skey)
            elif isinstance(node, PageSetup):
                pkey = _page_key(node)
                if pkey in seen_pages:
                    continue
                _place_body_chrome(model, node)
                seen_pages.add(pkey)
    seen_fields = {
        (fc.field_type, fc.instruction, fc.result_text)
        for fc in model.nodes
        if isinstance(fc, FieldCode)
    }
    for fc in field_codes[:50]:
        instr = (fc.get("instr") or "").strip()
        result = (fc.get("text") or "").strip()
        ftype = instr.split()[0] if instr else "FIELD"
        key = (ftype, instr, result)
        if key in seen_fields:
            continue
        _place_body_chrome(
            model, FieldCode(field_type=ftype, instruction=instr, result_text=result)
        )
        seen_fields.add(key)
    seen_bookmarks = {(bm.name, bm.anchor) for bm in model.nodes if isinstance(bm, Bookmark)}
    for bm in bookmarks[:50]:
        key = (bm, bm)
        if key in seen_bookmarks:
            continue
        _place_body_chrome(model, Bookmark(name=bm, anchor=bm))
        seen_bookmarks.add(key)

    seen_notes = {
        (fn.id, (fn.text or "").strip())
        for fn in model.nodes
        if isinstance(fn, Footnote)
    }
    seen_bodies = {
        (fn.text or "").strip() for fn in model.nodes if isinstance(fn, Footnote)
    }
    for fn in footnotes[:80]:
        body = (fn.text or "").strip()
        key = (fn.id, body)
        if key in seen_notes or (body and body in seen_bodies):
            continue
        model.nodes.append(fn)
        seen_notes.add(key)
        if body:
            seen_bodies.add(body)
    model.nodes = _drop_footnote_echoes(model.nodes)
    seen_charts = {_chart_key(ch) for ch in model.nodes if isinstance(ch, Chart)}
    replaced_numbers = _replace_chart_anchors(model, charts, seen_charts)
    pending: list[tuple[Chart, int | None]] = []
    for i, ch in enumerate(charts[:20]):
        if (i + 1) in replaced_numbers:
            continue
        key = _chart_key(ch)
        if key in seen_charts:
            continue
        pos = chart_positions[i] if i < len(chart_positions) else None
        pending.append((ch, pos))
        seen_charts.add(key)
    # Body-child indexes are relative to w:body. Chrome already parked in front
    # of the first paragraph has to be added back or the chart lands too early.
    body_offset = next(
        (
            i
            for i, existing in enumerate(model.nodes)
            if getattr(existing, "type", "") in _BODY_TYPES
        ),
        len(model.nodes),
    )
    for ch, pos in sorted(
        pending, key=lambda item: item[1] if item[1] is not None else 10**9, reverse=True
    ):
        if pos is None:
            model.nodes.append(ch)
        else:
            model.nodes.insert(min(body_offset + pos, len(model.nodes)), ch)

    existing = {
        (c.author, c.text)
        for c in model.nodes
        if isinstance(c, Comment)
    }
    for c in comments:
        if (c.author, c.text) not in existing:
            model.nodes.append(c)

    rev_existing = {
        (r.change, r.author, r.text) for r in model.nodes if isinstance(r, Revision)
    }
    for r in revisions:
        key = (r.change, r.author, r.text)
        if key not in rev_existing:
            model.nodes.append(r)

    cc_existing = {
        (c.tag, c.alias, c.text) for c in model.nodes if isinstance(c, ContentControl)
    }
    for cc in content_controls:
        key = (cc.tag, cc.alias, cc.text)
        if key not in cc_existing:
            model.nodes.append(cc)

    tb_existing = {(t.source, t.text) for t in model.nodes if isinstance(t, TextBox)}
    for tb in text_boxes:
        key = (tb.source, tb.text)
        if key not in tb_existing:
            model.nodes.append(tb)

    math_existing = {
        (m.content, m.display) for m in model.nodes if isinstance(m, MathBlock)
    }
    for mb in math_blocks:
        key = (mb.content, mb.display)
        if key in math_existing or _math_already_inline(model, mb.content):
            continue
        model.nodes.append(mb)
        math_existing.add(key)

    sa_existing = {
        (s.layout, s.title, tuple(s.items))
        for s in model.nodes
        if isinstance(s, SmartArt)
    }
    for sa in smartarts:
        key = (sa.layout, sa.title, tuple(sa.items))
        if key not in sa_existing:
            model.nodes.append(sa)
            sa_existing.add(key)

    ole_existing = {
        (o.prog_id, o.label, o.relationship_id)
        for o in model.nodes
        if isinstance(o, OleObject)
    }
    for ole in ole_objects:
        key = (ole.prog_id, ole.label, ole.relationship_id)
        if key not in ole_existing:
            model.nodes.append(ole)
            ole_existing.add(key)

    return model


def _parse_text_boxes(zf: zipfile.ZipFile) -> list[TextBox]:
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    out: list[TextBox] = []
    for el in root.iter():
        if _local(el.tag) != "txbxContent":
            continue
        text = _text_of(el)
        if text:
            out.append(TextBox(text=text, source="docx:txbx"))
    return out[:80]


def _parse_content_controls(zf: zipfile.ZipFile) -> list[ContentControl]:
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    out: list[ContentControl] = []
    for el in root.iter():
        if _local(el.tag) != "sdt":
            continue
        tag = ""
        alias = ""
        control_type = ""
        for child in el:
            ln = _local(child.tag)
            if ln == "sdtPr":
                for pr in child:
                    pln = _local(pr.tag)
                    if pln == "tag":
                        tag = pr.get(f"{_W}val") or pr.get("val") or ""
                    elif pln == "alias":
                        alias = pr.get(f"{_W}val") or pr.get("val") or ""
                    elif pln in ("date", "dropDownList", "comboBox", "text", "picture"):
                        control_type = pln
            elif ln == "sdtContent":
                text = _text_of(child)
                if text:
                    out.append(
                        ContentControl(
                            tag=tag,
                            alias=alias,
                            control_type=control_type or "richText",
                            text=text,
                        )
                    )
    return out[:100]


def field_codes_under(root: ET.Element) -> list[FieldCode]:
    """Field codes in document order under one element (a body child or the whole doc)."""
    out: list[FieldCode] = []
    for el in root.iter():
        if _local(el.tag) != "fldSimple":
            continue
        node = _field_node(el.get(f"{_W}instr") or el.get("instr") or "", _text_of(el))
        if node is not None:
            out.append(node)
    instr_buf = ""
    for el in root.iter():
        ln = _local(el.tag)
        if ln == "instrText" and el.text:
            instr_buf += el.text
        elif ln == "fldChar":
            ftype = el.get(f"{_W}fldCharType") or el.get("fldCharType")
            if ftype == "end" and instr_buf.strip():
                node = _field_node(instr_buf, "")
                if node is not None:
                    out.append(node)
                instr_buf = ""
    return out


def _field_node(instr: str, text: str) -> FieldCode | None:
    instr = (instr or "").strip()
    text = (text or "").strip()
    if not instr and not text:
        return None
    ftype = instr.split()[0] if instr else "FIELD"
    return FieldCode(field_type=ftype, instruction=instr, result_text=text)


def bookmarks_under(root: ET.Element) -> list[Bookmark]:
    out: list[Bookmark] = []
    seen: set[str] = set()
    for el in root.iter(f"{_W}bookmarkStart"):
        name = el.get(f"{_W}name") or el.get("name") or ""
        if not name or name.startswith("_") or name in seen:
            continue
        seen.add(name)
        out.append(Bookmark(name=name, anchor=name))
    return out


def section_nodes_under(root: ET.Element) -> list[Section | PageSetup]:
    """sectPr on this body child, in reading order, as Section + PageSetup pairs."""
    out: list[Section | PageSetup] = []
    for el in root.iter(f"{_W}sectPr"):
        props = _sectpr_to_dict(el)
        if props:
            out.extend(nodes_from_section_props(props))
    return out


def nodes_from_section_props(props: dict[str, object]) -> list[Section | PageSetup]:
    section = Section(title="", props=dict(props))
    orient = str(props.get("orientation") or "portrait")
    if orient not in ("portrait", "landscape"):
        orient = "portrait"
    page_size = "A4"
    w_mm = props.get("page_width_mm")
    h_mm = props.get("page_height_mm")
    if isinstance(w_mm, (int, float)) and isinstance(h_mm, (int, float)):
        page_size = "Letter" if abs(float(w_mm) - 215.9) < 5 else "A4"
    margins = props.get("margins_mm") if isinstance(props.get("margins_mm"), dict) else {}
    page = PageSetup(
        page_size=page_size,
        orientation=orient,  # type: ignore[arg-type]
        margins_mm={str(k): float(v) for k, v in margins.items()},
    )
    return [section, page]


def _insert_before_body(model: DocumentModel, fresh: list) -> None:
    """Park catalog nodes in front of the body instead of after the last paragraph."""
    if not fresh:
        return
    pos = len(model.nodes)
    for i, existing in enumerate(model.nodes):
        if getattr(existing, "type", "") in _BODY_TYPES:
            pos = i
            break
    for offset, node in enumerate(fresh):
        model.nodes.insert(pos + offset, node)


def _chart_anchor_number(node: object) -> int | None:
    plain = getattr(node, "plain_text", None)
    text = plain() if callable(plain) else ""
    match = re.fullmatch(r"ADUPORT-CHART-(\d+)", (text or "").strip())
    return int(match.group(1)) if match else None


def _replace_chart_anchors(model: DocumentModel, charts: list, seen: set) -> set[int]:
    """Swap hidden anchor paragraphs for the chart they name."""
    found: list[tuple[int, int]] = []
    for index, node in enumerate(model.nodes):
        number = _chart_anchor_number(node)
        if number is not None:
            found.append((index, number))
    replaced: set[int] = set()
    for index, number in sorted(found, reverse=True):
        if not 1 <= number <= len(charts):
            continue
        chart = charts[number - 1]
        model.nodes[index] = chart
        seen.add(_chart_key(chart))
        replaced.add(number)
    return replaced


def _body_child_counts_as_node(child: ET.Element) -> bool:
    """Roughly the paragraphs and tables the DOCX text importer keeps."""
    local = _local(child.tag)
    if local == "tbl":
        return True
    if local != "p":
        return False
    blob = "".join(
        (node.text or "") for node in child.iter() if _local(node.tag) == "t"
    ).strip()
    if re.fullmatch(r"ADUPORT-CHART-\d+", blob):
        return True
    if any(_local(node.tag) == "chart" for node in child.iter()) and not blob:
        return False
    return bool(blob)


def _chart_body_indexes(zf: zipfile.ZipFile) -> list[int]:
    """Slot among kept body children, not the raw ``w:body`` child index.

    The text importer drops an empty drawing paragraph. Using that child's
    raw index inserts the chart after the following paragraph.
    """
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    body = None
    for child in root:
        if _local(child.tag) == "body":
            body = child
            break
    if body is None:
        return []
    kept = 0
    indexes: list[int] = []
    for child in list(body):
        has_chart = any(_local(node.tag) == "chart" for node in child.iter())
        if has_chart:
            indexes.append(kept)
        if _body_child_counts_as_node(child):
            kept += 1
    return indexes


def body_child_chrome(el: ET.Element) -> tuple[list, list]:
    """Chrome that belongs on this w:body child, split into before/after its text."""
    return (bookmarks_under(el) + field_codes_under(el), section_nodes_under(el))


def _parse_field_codes(zf: zipfile.ZipFile) -> list[dict[str, str]]:
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    return [
        {"instr": node.instruction, "text": node.result_text}
        for node in field_codes_under(root)[:50]
    ]


def _parse_track_changes(zf: zipfile.ZipFile) -> list[Revision]:
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    out: list[Revision] = []
    for el in root.iter():
        tag = _local(el.tag)
        if tag not in ("ins", "del"):
            continue
        text = _text_of(el)
        if not text:
            continue
        author = el.get(f"{_W}author") or el.get("author") or ""
        change = "insert" if tag == "ins" else "delete"
        out.append(Revision(change=change, author=author, text=text))
    return out[:200]


def _twips_to_mm(twips: int) -> float:
    return round(twips / 56.7, 2)


def _parse_section_props(zf: zipfile.ZipFile) -> dict[str, object]:
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return {}
    sect = None
    for el in root.iter(f"{_W}sectPr"):
        sect = el  # last wins for legacy single-dict metadata
    if sect is None:
        return {}
    return _sectpr_to_dict(sect)


def _sectpr_to_dict(sect: ET.Element) -> dict[str, object]:
    props: dict[str, object] = {}
    pg_sz = sect.find(f"{_W}pgSz")
    if pg_sz is not None:
        w = int(pg_sz.get(f"{_W}w") or pg_sz.get("w") or 0)
        h = int(pg_sz.get(f"{_W}h") or pg_sz.get("h") or 0)
        if w and h:
            props["page_width_mm"] = _twips_to_mm(w)
            props["page_height_mm"] = _twips_to_mm(h)
        o = pg_sz.get(f"{_W}orient") or pg_sz.get("orient")
        if o:
            props["orientation"] = str(o)
    pg_mar = sect.find(f"{_W}pgMar")
    if pg_mar is not None:
        margins = {}
        for side in ("top", "right", "bottom", "left"):
            raw = pg_mar.get(f"{_W}{side}") or pg_mar.get(side)
            if raw and str(raw).isdigit():
                margins[side] = _twips_to_mm(int(raw))
        if margins:
            props["margins_mm"] = margins
    cols = sect.find(f"{_W}cols")
    if cols is not None:
        num = cols.get(f"{_W}num") or cols.get("num")
        if num and str(num).isdigit():
            props["cols"] = int(num)
    return props


def _parse_sections(zf: zipfile.ZipFile) -> list[dict[str, object]]:
    """All body w:sectPr (mid-document + final) as ordered dicts."""
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    out: list[dict[str, object]] = []
    for el in root.iter(f"{_W}sectPr"):
        d = _sectpr_to_dict(el)
        if d:
            out.append(d)
    return out


def _parse_omml_math(zf: zipfile.ZipFile) -> list[MathBlock]:
    try:
        from aduport.exporters.omml import extract_math_blocks_from_document_xml

        pairs = extract_math_blocks_from_document_xml(
            read_zip_member(zf, "word/document.xml")
        )
    except Exception as exc:
        return swallow_import_error(exc, where='docx_ooxml.py:386', default=[])
    return [MathBlock(content=tex, display=disp) for tex, disp in pairs]


_DGM = "{http://schemas.openxmlformats.org/drawingml/2006/diagram}"
_O_VML = "{urn:schemas-microsoft-com:office:office}"


def _parse_smartart(zf: zipfile.ZipFile, names: set[str]) -> list[SmartArt]:
    """Extract SmartArt text points from word/diagrams/data*.xml."""
    out: list[SmartArt] = []
    data_parts = sorted(
        n for n in names if re.match(r"word/diagrams/data\d*\.xml$", n)
    )
    for path in data_parts[:20]:
        try:
            root = xml_safe.fromstring(read_zip_member(zf, path))
        except ET.ParseError:
            continue
        items: list[str] = []
        for pt in root.iter(f"{_DGM}pt"):
            # skip doc/root points without visible text body preference
            texts: list[str] = []
            for t_el in pt.iter(f"{_A}t"):
                if t_el.text and t_el.text.strip():
                    texts.append(t_el.text.strip())
            # also dgm:t wrapping drawingml
            joined = " ".join(texts).strip()
            if joined:
                items.append(joined)
        # de-dupe preserve order
        seen: set[str] = set()
        uniq: list[str] = []
        for it in items:
            if it not in seen:
                seen.add(it)
                uniq.append(it)
        if uniq:
            out.append(
                SmartArt(
                    layout="diagram",
                    title=path.rsplit("/", 1)[-1],
                    items=uniq[:50],
                )
            )
    return out


def _parse_ole_objects(zf: zipfile.ZipFile) -> list[OleObject]:
    """Pull o:OLEObject / w:object markers and embedding binaries from package."""
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    rel_map: dict[str, str] = {}
    try:
        rels_root = xml_safe.fromstring(read_zip_member(zf, "word/_rels/document.xml.rels"))
        for rel in rels_root:
            rid = rel.get("Id") or ""
            target = rel.get("Target") or ""
            if rid and target:
                rel_map[rid] = target
    except Exception as exc:
        swallow_import_error(exc, where='docx_ooxml.py:449')
    out: list[OleObject] = []
    for el in root.iter():
        ln = _local(el.tag)
        # Prefer o:OLEObject. Bare w:object is only the VML wrapper and used to
        # emit a second empty OleObject with no ProgID / payload.
        if ln == "object":
            if any(_local(child.tag) == "OLEObject" for child in el.iter() if child is not el):
                continue
        elif ln != "OLEObject":
            continue
        prog = (
            el.get("ProgID")
            or el.get("progId")
            or el.get(f"{_O_VML}ProgID")
            or ""
        )
        rid = (
            el.get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            or el.get("r:id")
            or el.get("id")
            or ""
        )
        label = prog or rid or "embedded"
        data = b""
        target = rel_map.get(str(rid), "")
        if target:
            path = target if target.startswith("word/") else f"word/{target.lstrip('/')}"
            # embeddings often relative: embeddings/oleObject1.bin
            if not path.startswith("word/"):
                path = "word/" + path
            try:
                data = read_zip_member(zf, path)
            except Exception as exc:
                data = b""
                swallow_import_error(exc, where='docx_ooxml.py:480')
        out.append(
            OleObject(
                prog_id=str(prog)[:120],
                label=str(label)[:200],
                relationship_id=str(rid)[:80],
                data=data[: 2_000_000],
            )
        )
        if len(out) >= 40:
            break
    seen: set[tuple[str, str, str]] = set()
    uniq: list[OleObject] = []
    for o in out:
        key = (o.prog_id, o.label, o.relationship_id)
        if key not in seen:
            seen.add(key)
            uniq.append(o)
    return uniq


def _drop_footnote_echoes(nodes: list) -> list:
    """Drop a body paragraph that only repeats a footnote definition."""
    bodies = {(fn.text or "").strip() for fn in nodes if isinstance(fn, Footnote)}
    if not bodies:
        return nodes
    kept = []
    for node in nodes:
        if getattr(node, "type", "") == "paragraph":
            raw = getattr(node, "text", "")
            text = raw.text if hasattr(raw, "text") else str(raw or "")
            text = text.strip()
            match = re.match(r"^\[\^[^\]]+\]:\s*(.*)$", text)
            if match and match.group(1).strip() in bodies:
                continue
        kept.append(node)
    return kept


def _parse_footnotes(zf: zipfile.ZipFile) -> list[Footnote]:
    """Parse word/footnotes.xml → Footnote AST (skips separator ids ≤ 0)."""
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/footnotes.xml"))
    except (ET.ParseError, KeyError, OSError, FileTooLargeError):
        return []
    out: list[Footnote] = []
    for el in root.findall(f"{_W}footnote"):
        fid = el.get(f"{_W}id") or el.get("id") or ""
        try:
            if int(fid) < 1:
                continue
        except ValueError:
            if not fid:
                continue
        ftype = el.get(f"{_W}type") or el.get("type") or ""
        if ftype in ("separator", "continuationSeparator"):
            continue
        text = _text_of(el).strip()
        # Drop leading footnote reference glyph noise
        if text.startswith(fid):
            text = text[len(fid) :].lstrip()
        fid_out = str(fid)
        marker = re.match(r"aduport-fnid:(\S+)\s*", text)
        if marker:
            fid_out = marker.group(1)
            text = text[marker.end() :].strip()
        if not text:
            continue
        out.append(Footnote(id=fid_out, text=text[:4000]))
        if len(out) >= 80:
            break
    return out


def _parse_bookmarks(zf: zipfile.ZipFile) -> list[str]:
    """Collect w:bookmarkStart @w:name values (v6/T2-#8)."""
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for el in root.iter(f"{_W}bookmarkStart"):
        name = el.get(f"{_W}name") or el.get("name") or ""
        if name and name not in seen and not name.startswith("_"):
            seen.add(name)
            names.append(name)
    return names[:50]


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text_of(el: ET.Element) -> str:
    parts: list[str] = []
    for t in el.iter(f"{_W}t"):
        if t.text:
            parts.append(t.text)
    return "".join(parts).strip()


def _parse_comments(zf: zipfile.ZipFile) -> list[Comment]:
    raw = read_zip_member(zf, "word/comments.xml")
    try:
        root = xml_safe.fromstring(raw)
    except ET.ParseError:
        return []
    out: list[Comment] = []
    for c_el in root.findall(f"{_W}comment"):
        author = c_el.get(f"{_W}author") or c_el.get("author") or "reviewer"
        text = _text_of(c_el)
        if text:
            out.append(Comment(author=author, text=text))
    return out


def _parse_headers(zf: zipfile.ZipFile, names: set[str]) -> str:
    header_files = sorted(n for n in names if re.match(r"word/header\d*\.xml$", n))
    chunks: list[str] = []
    for path in header_files:
        try:
            root = xml_safe.fromstring(read_zip_member(zf, path))
        except ET.ParseError:
            continue
        text = _text_of(root)
        if text:
            chunks.append(text)
    # Unique preserve order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in chunks:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return " | ".join(uniq)


def _parse_header_footer_nodes(zf: zipfile.ZipFile, names: set[str]) -> list[HeaderFooter]:
    """Emit HeaderFooter AST nodes from word/header*.xml and word/footer*.xml."""
    out: list[HeaderFooter] = []
    specs = (
        ("header", r"word/header\d*\.xml$"),
        ("footer", r"word/footer\d*\.xml$"),
    )
    seen: set[tuple[str, str]] = set()
    for kind, pat in specs:
        for path in sorted(n for n in names if re.match(pat, n)):
            try:
                root = xml_safe.fromstring(read_zip_member(zf, path))
            except (ET.ParseError, KeyError, OSError):
                continue
            text = _text_of(root).strip()
            if not text:
                continue
            key = (kind, text[:500])
            if key in seen:
                continue
            seen.add(key)
            out.append(HeaderFooter(kind=kind, text=text[:500]))  # type: ignore[arg-type]
    return out


def _parse_run_styles(zf: zipfile.ZipFile) -> list[RunStyle]:
    """v6/T2-#8: styles.xml → RunStyle catalog (was zombie)."""
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/styles.xml"))
    except (ET.ParseError, KeyError, OSError):
        return []
    out: list[RunStyle] = []
    for st in root.iter(f"{_W}style"):
        sid = (st.get(f"{_W}styleId") or "").strip()
        if not sid:
            continue
        name_el = st.find(f"{_W}name")
        name = (name_el.get(f"{_W}val") if name_el is not None else "") or sid
        stype = (st.get(f"{_W}type") or "").strip()
        rpr = st.find(f"{_W}rPr")
        props: dict[str, object] = {"type": stype}
        if rpr is not None:
            if rpr.find(f"{_W}b") is not None:
                props["bold"] = True
            if rpr.find(f"{_W}i") is not None:
                props["italic"] = True
            if rpr.find(f"{_W}strike") is not None:
                props["strikethrough"] = True
            sz = rpr.find(f"{_W}sz")
            if sz is not None and sz.get(f"{_W}val"):
                try:
                    props["size_pt"] = int(sz.get(f"{_W}val") or "0") / 2
                except ValueError:
                    pass
            fonts = rpr.find(f"{_W}rFonts")
            if fonts is not None:
                font = fonts.get(f"{_W}ascii") or fonts.get(f"{_W}hAnsi") or ""
                if font:
                    props["font"] = font
        out.append(RunStyle(style_id=sid, name=str(name), props=props))
        if len(out) >= 80:
            break
    return out


def _parse_numbering(zf: zipfile.ZipFile) -> list[Numbering]:
    """v6/T2-#8: numbering.xml → Numbering AST (was zombie)."""
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/numbering.xml"))
    except (ET.ParseError, KeyError, OSError):
        return []
    abstracts: dict[str, list[dict[str, object]]] = {}
    for an in root.iter(f"{_W}abstractNum"):
        aid = (an.get(f"{_W}abstractNumId") or "").strip()
        levels: list[dict[str, object]] = []
        for lvl in an.findall(f"{_W}lvl"):
            ilvl = lvl.get(f"{_W}ilvl") or "0"
            fmt_el = lvl.find(f"{_W}numFmt")
            start_el = lvl.find(f"{_W}start")
            levels.append(
                {
                    "ilvl": ilvl,
                    "numFmt": (fmt_el.get(f"{_W}val") if fmt_el is not None else "") or "",
                    "start": (start_el.get(f"{_W}val") if start_el is not None else "1"),
                }
            )
        abstracts[aid] = levels
    out: list[Numbering] = []
    for num in root.iter(f"{_W}num"):
        nid = (num.get(f"{_W}numId") or "").strip()
        abs_el = num.find(f"{_W}abstractNumId")
        aid = (abs_el.get(f"{_W}val") if abs_el is not None else "") or ""
        if not nid:
            continue
        out.append(
            Numbering(num_id=nid, abstract_num_id=aid, levels=abstracts.get(aid, []))
        )
        if len(out) >= 50:
            break
    if not out:
        for aid, levels in list(abstracts.items())[:20]:
            out.append(Numbering(num_id="", abstract_num_id=aid, levels=levels))
    return out


def _parse_theme_name(zf: zipfile.ZipFile) -> str:
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/theme/theme1.xml"))
    except ET.ParseError:
        return ""
    # Prefer theme name attribute if present
    for el in root.iter():
        if _local(el.tag) == "theme":
            name = el.get("name") or ""
            if name:
                return name
    # Fall back to accent1 scheme color hint
    for el in root.iter():
        if _local(el.tag) == "srgbClr":
            val = el.get("val")
            if val:
                return f"accent:{val}"
    return ""
