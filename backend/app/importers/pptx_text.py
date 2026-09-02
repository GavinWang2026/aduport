"""Lightweight PPTX extract without docling — slide XML text nodes."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree as ET

from app.core.document_model import DocumentModel, Heading, Paragraph, RichText, TextBox

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

    nodes: list = []
    for i, path in enumerate(names[:50]):
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError:
            continue
        slide_num = i + 1
        shapes = _slide_text_shapes(root)
        if not shapes:
            continue
        if i == 0:
            nodes.append(Heading(level=1, text=RichText(text=shapes[0][1])))
            for kind, text, box in shapes[1:]:
                if kind == "textbox" and box:
                    nodes.append(TextBox(text=text, source=f"pptx:slide{slide_num}", **box))
                else:
                    nodes.append(Paragraph(text=RichText(text=text)))
        else:
            nodes.append(Heading(level=2, text=RichText(text=f"Slide {slide_num}")))
            for kind, text, box in shapes:
                if kind == "textbox" and box:
                    nodes.append(TextBox(text=text, source=f"pptx:slide{slide_num}", **box))
                else:
                    nodes.append(Paragraph(text=RichText(text=text)))
    zf.close()
    if not nodes:
        return None
    meta: dict[str, object] = {}
    try:
        zf2 = zipfile.ZipFile(BytesIO(data))
        layout_names = _pptx_layout_names(zf2)
        if layout_names:
            meta["pptx_slide_layouts"] = layout_names
        anims = _pptx_animation_meta(zf2)
        if anims:
            meta["pptx_animations"] = anims
        zf2.close()
    except zipfile.BadZipFile:
        pass
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


def _pptx_layout_names(zf: zipfile.ZipFile) -> list[str]:
    names: list[str] = []
    for path in sorted(zf.namelist()):
        if not re.match(r"ppt/slideLayouts/slideLayout\d+\.xml$", path):
            continue
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError:
            continue
        for el in root.iter():
            if el.tag.endswith("}cSld") or el.tag == "cSld":
                nm = el.get("name")
                if nm:
                    names.append(nm)
                    break
    return names[:30]


def _pptx_animation_meta(zf: zipfile.ZipFile) -> list[dict[str, object]]:
    """Extract p:timing animation tree summary per slide (XML preserved, no playback)."""
    out: list[dict[str, object]] = []
    for path in sorted(zf.namelist()):
        if not re.match(r"ppt/slides/slide\d+\.xml$", path):
            continue
        try:
            root = ET.fromstring(zf.read(path))
        except ET.ParseError:
            continue
        slide_num = int(re.search(r"slide(\d+)", path).group(1))  # type: ignore[union-attr]
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
