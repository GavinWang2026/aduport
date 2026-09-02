"""Post-pass OOXML enrichment for DOCX import (comments / headers / theme).

Runs on the raw ZIP bytes after (or without) docling body extraction so
Word Review comments, section headers, and theme accent survive into AST.
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core.document_model import Comment, ContentControl, DocumentModel, Paragraph, Revision, TextBox

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


def enrich_docx_model(model: DocumentModel, source_bytes: bytes) -> DocumentModel:
    """Mutate/return model with OOXML-derived comments, revisions, section props, theme."""
    try:
        with zipfile.ZipFile(BytesIO(source_bytes)) as zf:
            names = set(zf.namelist())
            comments = _parse_comments(zf) if "word/comments.xml" in names else []
            revisions = _parse_track_changes(zf) if "word/document.xml" in names else []
            content_controls = _parse_content_controls(zf) if "word/document.xml" in names else []
            field_codes = _parse_field_codes(zf) if "word/document.xml" in names else []
            text_boxes = _parse_text_boxes(zf) if "word/document.xml" in names else []
            section_props = _parse_section_props(zf) if "word/document.xml" in names else {}
            header_text = _parse_headers(zf, names)
            theme = _parse_theme_name(zf) if "word/theme/theme1.xml" in names else ""
    except zipfile.BadZipFile:
        return model

    meta = dict(model.metadata or {})
    if theme:
        meta["docx_theme"] = theme
    if section_props:
        meta["section_props"] = section_props
    if field_codes:
        meta["docx_field_codes"] = field_codes[:50]
    if meta:
        model.metadata = meta

    if header_text:
        meta = dict(model.metadata or {})
        meta["docx_header"] = header_text[:500]
        model.metadata = meta
        # Surface as leading paragraph so MD/PDF exporters show it
        note = f"[页眉] {header_text[:200]}"
        if not any(isinstance(n, Paragraph) and n.plain_text() == note for n in model.nodes):
            model.nodes.insert(0, Paragraph(text=note))

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
    return model


def _parse_text_boxes(zf: zipfile.ZipFile) -> list[TextBox]:
    try:
        root = ET.fromstring(zf.read("word/document.xml"))
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
        root = ET.fromstring(zf.read("word/document.xml"))
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
        root = ET.fromstring(zf.read("word/document.xml"))
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
        root = ET.fromstring(zf.read("word/document.xml"))
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
        root = ET.fromstring(zf.read("word/document.xml"))
    except ET.ParseError:
        return {}
    sect = None
    for el in root.iter(f"{_W}sectPr"):
        sect = el
        break
    if sect is None:
        return {}
    props: dict[str, object] = {}
    pg_sz = sect.find(f"{_W}pgSz")
    if pg_sz is not None:
        w = int(pg_sz.get(f"{_W}w") or pg_sz.get("w") or 0)
        h = int(pg_sz.get(f"{_W}h") or pg_sz.get("h") or 0)
        if w and h:
            props["page_width_mm"] = _twips_to_mm(w)
            props["page_height_mm"] = _twips_to_mm(h)
    pg_mar = sect.find(f"{_W}pgMar")
    if pg_mar is not None:
        margins = {}
        for side in ("top", "right", "bottom", "left"):
            raw = pg_mar.get(f"{_W}{side}") or pg_mar.get(side)
            if raw and str(raw).isdigit():
                margins[side] = _twips_to_mm(int(raw))
        if margins:
            props["margins_mm"] = margins
    orient = sect.find(f"{_W}pgSz")
    if orient is not None:
        o = orient.get(f"{_W}orient") or orient.get("orient")
        if o:
            props["orientation"] = str(o)
    return props


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
    raw = zf.read("word/comments.xml")
    try:
        root = ET.fromstring(raw)
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
            root = ET.fromstring(zf.read(path))
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


def _parse_theme_name(zf: zipfile.ZipFile) -> str:
    try:
        root = ET.fromstring(zf.read("word/theme/theme1.xml"))
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
