"""Lightweight PPTX extract without docling — slide XML text nodes."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core import xml_safe
from app.core.document_model import (
    DocumentModel,
    Heading,
    OleObject,
    Paragraph,
    RichText,
    SmartArt,
    SpeakerNote,
    TextBox,
)
from app.core.exceptions import FileTooLargeError
from app.core.safe_zip import ZipInflateBudget

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
            shapes = _slide_text_shapes(root)
            note_text = _notes_for_slide(zf, path, budget)
            objects = _slide_smartart_and_ole(root, slide_num)
            if not shapes and not note_text and not objects:
                continue
            if shapes:
                if i == 0:
                    nodes.append(Heading(level=1, text=RichText(text=shapes[0][1])))
                    for kind, text, box in shapes[1:]:
                        if kind == "textbox" and box:
                            nodes.append(
                                TextBox(text=text, source=f"pptx:slide{slide_num}", **box)
                            )
                        else:
                            nodes.append(Paragraph(text=RichText(text=text)))
                else:
                    nodes.append(
                        Heading(level=2, text=RichText(text=f"Slide {slide_num}"))
                    )
                    for kind, text, box in shapes:
                        if kind == "textbox" and box:
                            nodes.append(
                                TextBox(
                                    text=text, source=f"pptx:slide{slide_num}", **box
                                )
                            )
                        else:
                            nodes.append(Paragraph(text=RichText(text=text)))
            nodes.extend(objects)
            if note_text:
                nodes.append(SpeakerNote(text=note_text, slide_index=slide_num))
        layout_names = _pptx_layout_names(zf, budget)
        anims = _pptx_animation_meta(zf, budget)
    finally:
        zf.close()

    if not nodes:
        return None
    meta: dict[str, object] = {"importer": "pptx_native"}
    if layout_names:
        meta["pptx_slide_layouts"] = layout_names
    if anims:
        meta["pptx_animations"] = anims
    return DocumentModel(title=title or "pptx", nodes=nodes, metadata=meta)


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
