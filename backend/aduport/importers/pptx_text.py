"""Lightweight PPTX extract without docling — slide XML text nodes."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from aduport.core import xml_safe
from aduport.core.document_model import (
    DocumentModel,
    Heading,
    OleObject,
    Paragraph,
    RichText,
    SmartArt,
    SpeakerNote,
    Table,
)
from aduport.core.exceptions import FileTooLargeError
from aduport.core.safe_zip import ZipInflateBudget

_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_A_T = f"{{{_A}}}t"
_EMU_PER_MM = 36000.0


def try_extract_pptx_model(data: bytes, *, title: str = "") -> DocumentModel | None:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return None
    names = sorted(
        n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)
    )
    if not names:
        zf.close()
        return None

    budget = ZipInflateBudget()
    nodes: list = []
    try:
        for i, path in enumerate(names[:50]):
            try:
                root = xml_safe.fromstring(budget.read(zf, path))
            except (ET.ParseError, FileTooLargeError, KeyError):
                continue
            slide_num = i + 1
            flow = _slide_flow(
                root, slide_index=i, links=_slide_hyperlinks(zf, path, budget)
            )
            note_text = _notes_for_slide(zf, path, budget)
            objects = _slide_smartart_and_ole(root, slide_num)
            if not flow and not note_text and not objects:
                continue
            if (
                flow
                and isinstance(flow[0], Heading)
                and _last_heading_text(nodes) == flow[0].plain_text()
            ):
                flow = flow[1:]
            nodes.extend(flow)
            nodes.extend(objects)
            if note_text:
                nodes.append(SpeakerNote(text=note_text, slide_index=slide_num))
        layout_names = _pptx_layout_names(zf, budget)
        anims = _pptx_animation_meta(zf, budget)
    finally:
        zf.close()

    from aduport.importers.zip_images import images_in_zip

    have = set()
    for image in images_in_zip(data, ("ppt/media/",)):
        if image.data in have:
            continue
        have.add(image.data)
        nodes.append(image)

    if not nodes:
        return None
    _strip_copied_list_markers(nodes)
    meta: dict[str, object] = {"importer": "pptx_native"}
    if layout_names:
        meta["pptx_slide_layouts"] = layout_names
    if anims:
        meta["pptx_animations"] = anims
    from aduport.core.title_source import resolve_title

    return DocumentModel(
        title=resolve_title(
            "" if (title or "").casefold() in {"pptx", ""} else title,
            nodes,
            filename=f"{title or 'input'}.pptx",
        ),
        nodes=nodes,
        metadata=meta,
    )


def _strip_copied_list_markers(nodes: list) -> None:
    """A placeholder bullet plus a copied ``- `` in the run is one marker too many."""
    from aduport.core.document_model import ListItem, ensure_rich_text
    from aduport.importers.list_markers import rich_without_redundant_list_prefix

    for node in nodes:
        if not isinstance(node, ListItem):
            continue
        rich = ensure_rich_text(node.text)
        node.text = rich_without_redundant_list_prefix(
            rich, ordered=bool(node.ordered and node.checked is None)
        )


def _last_heading_text(nodes: list) -> str:
    for node in reversed(nodes):
        if isinstance(node, Heading):
            return node.plain_text()
    return ""


def _slide_flow(root: ET.Element, *, slide_index: int, links: dict[str, str] | None = None) -> list:
    """Walk shapes and tables in document order and restore Markdown structure.

    Content placeholders hold list lines. Text boxes carry real bold/italic/code
    runs. ``a:tbl`` is a table, not a flattened paragraph.
    """
    from aduport.importers.markdown_rehydrate import looks_structural, parse_markdown_nodes

    title = ""
    buf: list[str] = []
    nodes: list = []

    def flush() -> None:
        blob = "\n\n".join(part for part in buf if part.strip())
        buf.clear()
        if not blob.strip():
            return
        if looks_structural(blob) or "**" in blob or "`" in blob or "*" in blob:
            nodes.extend(parse_markdown_nodes(blob))
        else:
            nodes.append(Paragraph(text=RichText(text=blob)))

    for el in root.iter():
        if el.tag.endswith("}graphicFrame"):
            flush()
            table = _graphic_table(el)
            if table is not None:
                nodes.append(table)
            continue
        if not el.tag.endswith("}sp"):
            continue
        ph_type, ph_idx, is_tx = _shape_role(el)
        if ph_type in ("ctrTitle", "title"):
            title = _shape_plain(el) or title
            continue
        if ph_type == "subTitle":
            text = _shape_plain(el)
            if text and text.casefold() != "AduPort":
                buf.append(text)
            continue
        listish = ph_type in ("", "body", "obj") and ph_idx is not None and not is_tx
        md = _shape_markdown(el, listish=listish, links=links or {})
        if md:
            buf.append(md)
    flush()

    heading: list = []
    if title and not re.fullmatch(r"Slide \d+", title) and title.casefold() != "footnotes":
        level = 1 if slide_index == 0 else 2
        heading.append(Heading(level=level, text=RichText(text=title)))
    return heading + nodes


def _shape_role(sp: ET.Element) -> tuple[str, str | None, bool]:
    ph_type = ""
    ph_idx = None
    is_tx = False
    for el in sp.iter():
        if el.tag.endswith("}cNvSpPr") and el.get("txBox") in ("1", "true"):
            is_tx = True
        if el.tag.endswith("}ph"):
            ph_type = el.get("type") or ""
            ph_idx = el.get("idx")
    return ph_type, ph_idx, is_tx


def _shape_plain(sp: ET.Element) -> str:
    texts = [el.text.strip() for el in sp.iter(_A_T) if el.text and el.text.strip()]
    return " ".join(texts).strip()


def _slide_hyperlinks(zf: zipfile.ZipFile, slide_path: str, budget: ZipInflateBudget) -> dict[str, str]:
    base = slide_path.rsplit("/", 1)[-1]
    try:
        rels_root = xml_safe.fromstring(budget.read(zf, f"ppt/slides/_rels/{base}.rels"))
    except (KeyError, ET.ParseError, FileTooLargeError):
        return {}
    links: dict[str, str] = {}
    for rel in rels_root:
        if not (rel.get("Type") or "").endswith("/hyperlink"):
            continue
        rid = rel.get("Id") or ""
        target = rel.get("Target") or ""
        if rid and target:
            links[rid] = target
    return links


def _shape_markdown(sp: ET.Element, *, listish: bool, links: dict[str, str] | None = None) -> str:
    lines: list[str] = []
    tx = None
    for child in sp.iter():
        if child.tag.endswith("}txBody"):
            tx = child
            break
    if tx is None:
        return ""
    collected: list[tuple[str, str, bool, ET.Element]] = []
    for p in tx:
        if not p.tag.endswith("}p"):
            continue
        marked, plain, courier = _paragraph_markdown(p, links or {})
        if not marked and not plain:
            continue
        collected.append((marked, plain, courier, p))
    i = 0
    while i < len(collected):
        marked, plain, courier, para = collected[i]
        if courier and not listish:
            block: list[str] = []
            while i < len(collected) and collected[i][2]:
                block.append(collected[i][1])
                i += 1
            lang = ""
            if block and block[0].startswith("```"):
                lang = block[0][3:].strip()
                block = block[1:]
            if block and block[-1].strip() == "```":
                block = block[:-1]
            lines.append(f"```{lang}\n" + "\n".join(block) + "\n```")
            continue
        line = _with_list_level(para, marked)
        if line.strip() in {"---", "***", "___"}:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append("---")
            lines.append("")
        elif listish and not re.match(r"^\s*([-*+]|\d+\.)\s", line) and not line.lstrip().startswith(">"):
            from aduport.importers.list_markers import strip_redundant_list_prefix

            body = strip_redundant_list_prefix(line, ordered=False)
            lines.append(f"- {body if body.strip() else line}")
        else:
            lines.append(line)
        i += 1
    return "\n".join(lines)


def _with_list_level(p: ET.Element, line: str) -> str:
    """OOXML stores nesting on ``a:pPr lvl``, which markdown needs as indent."""
    level = 0
    for child in p:
        if child.tag.endswith("}pPr"):
            raw = child.get("lvl") or "0"
            level = int(raw) if str(raw).isdigit() else 0
            break
    if level <= 0:
        return line
    return ("    " * level) + line


def _paragraph_markdown(p: ET.Element, links: dict[str, str] | None = None) -> tuple[str, str, bool]:
    parts: list[str] = []
    plain_parts: list[str] = []
    saw_text = False
    all_courier = True
    for child in p:
        if not child.tag.endswith("}r"):
            continue
        text = "".join(t.text or "" for t in child.iter(_A_T))
        if not text:
            continue
        saw_text = True
        rpr = next((el for el in child if el.tag.endswith("}rPr")), None)
        bold = italic = courier = False
        href = ""
        if rpr is not None:
            bold = rpr.get("b") in ("1", "true")
            italic = rpr.get("i") in ("1", "true")
            for lat in rpr.iter():
                if lat.tag.endswith("}latin") and "courier" in (lat.get("typeface") or "").casefold():
                    courier = True
                if lat.tag.endswith("}hlinkClick"):
                    href = (links or {}).get(
                        lat.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
                        or "",
                        "",
                    )
        if not courier:
            all_courier = False
        piece = text
        if courier:
            piece = f"`{piece}`"
        if italic:
            piece = f"*{piece}*"
        if bold:
            piece = f"**{piece}**"
        if href:
            piece = f"[{piece}]({href})"
        parts.append(piece)
        plain_parts.append(text)
    if not saw_text:
        return "", "", False
    return "".join(parts).strip(), "".join(plain_parts).strip(), all_courier


def _graphic_table(frame: ET.Element) -> Table | None:
    rows: list[list[str]] = []
    for tr in frame.iter():
        if not tr.tag.endswith("}tr"):
            continue
        row: list[str] = []
        for tc in list(tr):
            if not tc.tag.endswith("}tc"):
                continue
            row.append("".join(t.text or "" for t in tc.iter(_A_T)).strip())
        if any(row):
            rows.append(row)
    if not rows:
        return None
    return Table(rows=rows, header_rows=1 if len(rows) > 1 else 0)


def _slide_text_shapes(root: ET.Element) -> list[tuple[str, str, dict[str, float] | None]]:
    """Per-shape text: (kind, text, optional box mm dict)."""
    out: list[tuple[str, str, dict[str, float] | None]] = []
    for sp in root.iter():
        if not sp.tag.endswith("}sp"):
            continue
        tx_body = None
        for child in sp:
            if child.tag.endswith("}txBody"):
                tx_body = child
                break
        if tx_body is None:
            continue
        texts = [el.text for el in tx_body.iter(_A_T) if el.text and el.text.strip()]
        if not texts:
            texts = [
                el.text
                for el in tx_body.iter()
                if el.tag.endswith("}t") and el.text and el.text.strip()
            ]
        if not texts:
            continue
        text = "\n".join(t.strip() for t in texts)
        box = _shape_box_mm(sp)
        kind = "textbox" if box else "body"
        out.append((kind, text, box))
    return out


def _slide_smartart_and_ole(root: ET.Element, slide_num: int) -> list:
    """Capture diagram/OLE graphic frames as AST placeholders (no silent drop)."""
    out: list = []
    for gd in root.iter():
        if not gd.tag.endswith("}graphicData"):
            continue
        uri = (gd.get("uri") or "").lower()
        texts = [
            (el.text or "").strip()
            for el in gd.iter()
            if el.tag.endswith("}t") and el.text and el.text.strip()
        ]
        if "diagram" in uri or "smartart" in uri:
            items = texts[:40]
            title = items[0] if items else f"slide{slide_num}"
            out.append(
                SmartArt(
                    layout="process",
                    title=title[:120],
                    items=items[1:] if len(items) > 1 else items,
                )
            )
        elif "ole" in uri or "package" in uri:
            label = texts[0] if texts else f"ole-slide{slide_num}"
            prog = ""
            for el in gd.iter():
                if el.tag.endswith("}oleObj"):
                    prog = el.get("progId") or prog
            out.append(OleObject(prog_id=prog or "Package", label=label[:200]))
    for el in root.iter():
        if not el.tag.endswith("}oleObj"):
            continue
        prog = el.get("progId") or "OLE"
        name = el.get("name") or el.get("id") or prog
        # Avoid duplicates if already captured via graphicData
        if any(
            isinstance(n, OleObject) and n.label == str(name)[:200] for n in out
        ):
            continue
        out.append(OleObject(prog_id=prog, label=str(name)[:200]))
    return out


def _shape_box_mm(sp: ET.Element) -> dict[str, float] | None:
    for el in sp.iter():
        if not el.tag.endswith("}xfrm"):
            continue
        ox, oy, w, h = 0, 0, 0, 0
        for child in el:
            if child.tag.endswith("}off"):
                try:
                    ox = int(child.get("x") or 0)
                    oy = int(child.get("y") or 0)
                except ValueError:
                    pass
            elif child.tag.endswith("}ext"):
                try:
                    w = int(child.get("cx") or 0)
                    h = int(child.get("cy") or 0)
                except ValueError:
                    pass
        if w > 0 and h > 0:
            return {
                "x_mm": round(ox / _EMU_PER_MM, 2),
                "y_mm": round(oy / _EMU_PER_MM, 2),
                "width_mm": round(w / _EMU_PER_MM, 2),
                "height_mm": round(h / _EMU_PER_MM, 2),
            }
    return None


def _notes_for_slide(
    zf: zipfile.ZipFile, slide_path: str, budget: ZipInflateBudget
) -> str:
    """Resolve notesSlide via slide relationships and extract plain text."""
    base = slide_path.rsplit("/", 1)[-1]
    rels_path = f"ppt/slides/_rels/{base}.rels"
    try:
        rels_root = xml_safe.fromstring(budget.read(zf, rels_path))
    except (KeyError, ET.ParseError, FileTooLargeError):
        return ""
    notes_target = ""
    for rel in rels_root:
        typ = rel.get("Type") or ""
        if typ.endswith("/notesSlide"):
            notes_target = (rel.get("Target") or "").lstrip("/")
            break
    if not notes_target:
        return ""
    if notes_target.startswith("../"):
        notes_path = "ppt/" + notes_target[3:]
    elif notes_target.startswith("ppt/"):
        notes_path = notes_target
    else:
        notes_path = f"ppt/slides/{notes_target}"
    try:
        root = xml_safe.fromstring(budget.read(zf, notes_path))
    except (KeyError, ET.ParseError, FileTooLargeError):
        return ""
    texts = [
        el.text.strip()
        for el in root.iter()
        if el.tag.endswith("}t") and el.text and el.text.strip()
    ]
    cleaned = [t for t in texts if "master" not in t.lower() and len(t) > 0]
    return "\n".join(cleaned).strip()


def _pptx_layout_names(zf: zipfile.ZipFile, budget: ZipInflateBudget) -> list[str]:
    names: list[str] = []
    for path in sorted(zf.namelist()):
        if not re.match(r"ppt/slideLayouts/slideLayout\d+\.xml$", path):
            continue
        try:
            root = xml_safe.fromstring(budget.read(zf, path))
        except (ET.ParseError, FileTooLargeError, KeyError):
            continue
        for el in root.iter():
            if el.tag.endswith("}cSld") or el.tag == "cSld":
                nm = el.get("name")
                if nm:
                    names.append(nm)
                    break
    return names[:30]


def _pptx_animation_meta(
    zf: zipfile.ZipFile, budget: ZipInflateBudget
) -> list[dict[str, object]]:
    """Extract p:timing animation tree summary per slide (XML preserved, no playback)."""
    out: list[dict[str, object]] = []
    for path in sorted(zf.namelist()):
        if not re.match(r"ppt/slides/slide\d+\.xml$", path):
            continue
        try:
            root = xml_safe.fromstring(budget.read(zf, path))
        except (ET.ParseError, FileTooLargeError, KeyError):
            continue
        m = re.search(r"slide(\d+)", path)
        if not m:
            continue
        slide_num = int(m.group(1))
        timing_nodes = [el for el in root.iter() if el.tag.endswith("}timing")]
        if not timing_nodes:
            continue
        effects: list[str] = []
        node_count = 0
        for timing in timing_nodes:
            for el in timing.iter():
                ln = el.tag.rsplit("}", 1)[-1]
                if ln in ("par", "seq", "cTn"):
                    node_count += 1
                if ln in ("animEffect", "set", "anim", "animClr", "animMotion"):
                    transition = el.get("transition") or el.get("filter") or ln
                    if transition:
                        effects.append(str(transition))
        if node_count or effects:
            out.append(
                {
                    "slide": slide_num,
                    "node_count": node_count,
                    "effects": effects[:10],
                    "has_timing_xml": True,
                }
            )
    return out[:50]


def _ole_markers(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    """(progId, name) for each slide OLE object, in slide order."""
    markers: list[tuple[str, str]] = []
    names = sorted(
        n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)
    )
    for name in names:
        try:
            root = xml_safe.fromstring(zf.read(name))
        except (ET.ParseError, KeyError, OSError):
            continue
        for el in root.iter():
            if not el.tag.endswith("}oleObj"):
                continue
            prog = el.get("progId") or "OLE"
            label = el.get("name") or el.get("id") or prog
            markers.append((prog, str(label)[:200]))
    return markers


def _is_ole_blob(node) -> bool:
    from aduport.core.document_model import Image

    if not isinstance(node, Image):
        return False
    mime = (node.mime_type or "").lower()
    data = node.data or b""
    if "ole" in mime:
        return True
    return data.startswith(b"OLE") and not data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8"))


def _lift_ole_images(model: DocumentModel, zf: zipfile.ZipFile) -> DocumentModel:
    """anydoc stores OLE embeddings as images. Keep them as OleObject labels."""
    from aduport.core.document_model import Image

    if not any(isinstance(n, Image) and _is_ole_blob(n) for n in model.nodes):
        return model
    markers = _ole_markers(zf)
    cursor = 0
    lifted = 0
    nodes = []
    for node in model.nodes:
        if isinstance(node, Image) and _is_ole_blob(node):
            if cursor < len(markers):
                prog, label = markers[cursor]
            else:
                prog, label = "OLE", node.alt_text or "object"
            cursor += 1
            nodes.append(
                OleObject(
                    prog_id=prog,
                    label=label or node.alt_text or "object",
                    data=node.data or b"",
                    mime_type=node.mime_type or "application/vnd.ms-ole-object",
                )
            )
            lifted += 1
        else:
            nodes.append(node)
    if not lifted:
        return model
    model.nodes = nodes
    meta = dict(model.metadata or {})
    meta["pptx_ole"] = "lifted"
    model.metadata = meta
    return model


def enrich_pptx_model(model: DocumentModel, source_bytes: bytes) -> DocumentModel:
    """Restore DrawingML charts anydoc flattens into a table, and say so."""
    from aduport.core.document_model import Chart
    from aduport.exporters.docx_chart import parse_ooxml_charts
    from aduport.exporters.export_warnings import warn_export

    try:
        zf = zipfile.ZipFile(BytesIO(source_bytes))
    except zipfile.BadZipFile:
        return model
    try:
        charts = parse_ooxml_charts(zf, "ppt/charts/chart")
        model = _lift_ole_images(model, zf)
    finally:
        zf.close()
    if not charts:
        return model

    def _key(ch: Chart) -> tuple:
        series: list[tuple] = []
        for ser in ch.series or []:
            if isinstance(ser, dict):
                vals = ser.get("values") if "values" in ser else ser.get("data")
                series.append((str(ser.get("name") or ""), tuple(vals or ())))
            else:
                series.append((str(ser), ()))
        return (ch.title, ch.chart_type, tuple(ch.categories or []), tuple(series))

    seen = {_key(n) for n in model.nodes if isinstance(n, Chart)}
    for ch in charts:
        key = _key(ch)
        if key in seen:
            continue
        model.nodes.append(ch)
        seen.add(key)
    message = "pptx: 图表在正文里降级为表格，已从 ppt/charts 恢复 Chart 节点"
    warn_export(model, message)
    meta = dict(model.metadata or {})
    notes = list(meta.get("fidelity_warnings") or [])
    if message not in notes:
        notes.append(message)
    meta["fidelity_warnings"] = notes
    model.metadata = meta
    return model
