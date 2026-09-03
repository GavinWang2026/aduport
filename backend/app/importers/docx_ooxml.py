"""Post-pass OOXML enrichment for DOCX import (comments / headers / theme).

Runs on the raw ZIP bytes after (or without) docling body extraction so
Word Review comments, section headers, and theme accent survive into AST.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core.document_model import (
    Bookmark,
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
from app.core.exceptions import FileTooLargeError
from app.core.safe_zip import read_zip_member, zip_inflate_session
from app.core import xml_safe


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"


def enrich_docx_model(model: DocumentModel, source_bytes: bytes) -> DocumentModel:
    """Mutate/return model with OOXML-derived comments, revisions, section props, theme."""
    footnotes: list[Footnote] = []
    charts: list = []
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
                if any(n.startswith("word/charts/chart") for n in names):
                    from app.exporters.docx_chart import parse_docx_charts

                    charts = parse_docx_charts(zf)
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
    for hf in header_footers:
        model.nodes.append(hf)
    if not header_footers and header_text:
        # v6/T2-#8 / D-05: also emit a real HeaderFooter AST node (was metadata-only).
        model.nodes.append(HeaderFooter(kind="header", text=header_text[:500]))
    for rs in run_styles[:80]:
        model.nodes.append(rs)
    for num in numberings[:50]:
        model.nodes.append(num)

    # v6/T2-#8: promote section / page / field / bookmark metadata into AST nodes
    # so zombie rate drops and exporters can round-trip chrome.
    for props in sections[:20]:
        if not isinstance(props, dict):
            continue
        model.nodes.append(Section(title="", props=dict(props)))
        orient = str(props.get("orientation") or "portrait")
        if orient not in ("portrait", "landscape"):
            orient = "portrait"
        page_size = "A4"
        w_mm = props.get("page_width_mm")
        h_mm = props.get("page_height_mm")
        if isinstance(w_mm, (int, float)) and isinstance(h_mm, (int, float)):
            # rough letter vs A4
            page_size = "Letter" if abs(float(w_mm) - 215.9) < 5 else "A4"
        margins = props.get("margins_mm") if isinstance(props.get("margins_mm"), dict) else {}
        model.nodes.append(
            PageSetup(
                page_size=page_size,
                orientation=orient,  # type: ignore[arg-type]
                margins_mm={str(k): float(v) for k, v in margins.items()},
            )
        )
    for fc in field_codes[:50]:
        instr = (fc.get("instr") or "").strip()
        result = (fc.get("text") or "").strip()
        ftype = instr.split()[0] if instr else "FIELD"
        model.nodes.append(
            FieldCode(field_type=ftype, instruction=instr, result_text=result)
        )
    for bm in bookmarks[:50]:
        model.nodes.append(Bookmark(name=bm, anchor=bm))

    for fn in footnotes[:80]:
        model.nodes.append(fn)
    for ch in charts[:20]:
        model.nodes.append(ch)

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
        if key not in math_existing:
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


def _parse_field_codes(zf: zipfile.ZipFile) -> list[dict[str, str]]:
    try:
        root = xml_safe.fromstring(read_zip_member(zf, "word/document.xml"))
    except ET.ParseError:
        return []
    out: list[dict[str, str]] = []
    for el in root.iter():
        if _local(el.tag) != "fldSimple":
            continue
        instr = el.get(f"{_W}instr") or el.get("instr") or ""
        text = _text_of(el)
        if instr or text:
            out.append({"instr": instr.strip(), "text": text})
    # Complex fields: instrText inside fldChar sequences
    instr_buf = ""
    for el in root.iter():
        ln = _local(el.tag)
        if ln == "instrText" and el.text:
            instr_buf += el.text
        elif ln == "fldChar":
            ftype = el.get(f"{_W}fldCharType") or el.get("fldCharType")
            if ftype == "end" and instr_buf.strip():
                out.append({"instr": instr_buf.strip(), "text": ""})
                instr_buf = ""
    return out[:50]


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
        from app.exporters.omml import extract_math_blocks_from_document_xml

        pairs = extract_math_blocks_from_document_xml(
            read_zip_member(zf, "word/document.xml")
        )
    except Exception:
        return []
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
    except Exception:
        pass
    out: list[OleObject] = []
    for el in root.iter():
        ln = _local(el.tag)
        if ln not in ("OLEObject", "object"):
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
            except Exception:
                data = b""
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
        if not text:
            continue
        out.append(Footnote(id=str(fid), text=text[:4000]))
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
