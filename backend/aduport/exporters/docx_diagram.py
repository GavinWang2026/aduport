"""DOCX SmartArt DrawingML visuals + OLE binary embedding helpers."""

from __future__ import annotations

import zipfile
from io import BytesIO
from xml.sax.saxutils import escape as xml_escape

from docx.oxml import parse_xml

from aduport.core.document_model import OleObject, SmartArt

_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
_WPG = "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
_DGM = "http://schemas.openxmlformats.org/drawingml/2006/diagram"


def append_smartart_drawing(paragraph, node: SmartArt, *, shape_id: int = 1) -> None:
    """Attach a visible process/list DrawingML group (SmartArt-like layout)."""
    items = [str(x).strip() for x in (node.items or []) if str(x).strip()][:8]
    if not items:
        items = [node.title or "Item"]
    layout = (node.layout or "process").lower()
    xml = _drawing_group_xml(items, layout=layout, shape_id=shape_id, title=node.title or "SmartArt")
    drawing = parse_xml(xml)
    run = paragraph.add_run()
    run._r.append(drawing)


def _drawing_group_xml(items: list[str], *, layout: str, shape_id: int, title: str) -> str:
    n = max(1, len(items))
    box_w = 1_371_600  # 1.5"
    box_h = 685_800  # 0.75"
    gap = 228_600  # 0.25"
    if layout in ("list", "hierarchy"):
        # vertical stack
        total_w = box_w + 200_000
        total_h = n * box_h + (n - 1) * gap + 100_000
        shapes = []
        for i, text in enumerate(items):
            y = i * (box_h + gap)
            shapes.append(_wsp_rect(shape_id + i, text, x=0, y=y, cx=box_w, cy=box_h, fill="5B9BD5"))
    elif layout in ("cycle",):
        # simple 2x2 / row wrap
        cols = min(4, n)
        total_w = cols * box_w + (cols - 1) * gap
        rows = (n + cols - 1) // cols
        total_h = rows * box_h + (rows - 1) * gap
        shapes = []
        for i, text in enumerate(items):
            r, c = divmod(i, cols)
            shapes.append(
                _wsp_rect(
                    shape_id + i,
                    text,
                    x=c * (box_w + gap),
                    y=r * (box_h + gap),
                    cx=box_w,
                    cy=box_h,
                    fill="70AD47",
                )
            )
    else:
        # process: horizontal boxes + chevron gaps
        total_w = n * box_w + (n - 1) * gap
        total_h = box_h + 80_000
        shapes = []
        for i, text in enumerate(items):
            x = i * (box_w + gap)
            fill = "5B9BD5" if i % 2 == 0 else "2E75B6"
            shapes.append(_wsp_rect(shape_id + i, text, x=x, y=0, cx=box_w, cy=box_h, fill=fill))
            if i < n - 1:
                # small triangle-ish connector as thin rect
                shapes.append(
                    _wsp_rect(
                        shape_id + 100 + i,
                        "→",
                        x=x + box_w,
                        y=box_h // 3,
                        cx=gap,
                        cy=box_h // 3,
                        fill="A9D08E",
                    )
                )

    body = "".join(shapes)
    return (
        f'<w:drawing xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        f'xmlns:wp="{_WP}" xmlns:a="{_A}" xmlns:wps="{_WPS}" xmlns:wpg="{_WPG}">'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{total_w}" cy="{total_h}"/>'
        f'<wp:docPr id="{shape_id}" name="{xml_escape(title)[:40]}"/>'
        f'<a:graphic><a:graphicData uri="{_WPG}">'
        f"<wpg:wgp>"
        f"<wpg:cNvGrpSpPr/><wpg:grpSpPr>"
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{total_w}" cy="{total_h}"/>'
        f'<a:chOff x="0" y="0"/><a:chExt cx="{total_w}" cy="{total_h}"/></a:xfrm>'
        f"</wpg:grpSpPr>"
        f"{body}"
        f"</wpg:wgp>"
        f"</a:graphicData></a:graphic>"
        f"</wp:inline></w:drawing>"
    )


def _wsp_rect(sid: int, text: str, *, x: int, y: int, cx: int, cy: int, fill: str) -> str:
    t = xml_escape((text or "")[:80])
    return (
        f"<wps:wsp>"
        f'<wps:cNvPr id="{sid}" name="Box{sid}"/>'
        f"<wps:cNvSpPr/>"
        f"<wps:spPr>"
        f'<a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>'
        f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>'
        f"<a:ln><a:solidFill><a:srgbClr val=\"2F5496\"/></a:solidFill></a:ln>"
        f"</wps:spPr>"
        f"<wps:txbx>"
        f'<w:txbxContent xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:p><w:pPr><w:jc w:val=\"center\"/></w:pPr>"
        f'<w:r><w:rPr><w:sz w:val="18"/><w:color w:val="FFFFFF"/></w:rPr>'
        f"<w:t>{t}</w:t></w:r></w:p>"
        f"</w:txbxContent>"
        f"</wps:txbx>"
        f"<wps:bodyPr anchor=\"ctr\" lIns=\"36000\" rIns=\"36000\" tIns=\"36000\" bIns=\"36000\"/>"
        f"</wps:wsp>"
    )


def build_diagram_parts(art: SmartArt, index: int) -> dict[str, str]:
    """Return path→xml for data/layout/colors/quickStyle diagram parts."""
    from xml.sax.saxutils import escape as _esc

    items = [str(x) for x in (art.items or [])][:50]
    pts = ['<dgm:pt modelId="0" type="doc"/>']
    cxns = []
    for j, item in enumerate(items, start=1):
        pts.append(
            f'<dgm:pt modelId="{j}" type="node">'
            f"<dgm:prSet phldrT=\"{ _esc(item) }\"/>"
            f"<dgm:t><a:bodyPr/><a:lstStyle/>"
            f"<a:p><a:pPr><a:defRPr/></a:pPr>"
            f"<a:r><a:rPr lang=\"en-US\" sz=\"1400\"/><a:t>{_esc(item)}</a:t></a:r>"
            f"<a:endParaRPr lang=\"en-US\" sz=\"1400\"/></a:p></dgm:t>"
            f"</dgm:pt>"
        )
        cxns.append(
            f'<dgm:cxn modelId="{1000 + j}" srcId="0" destId="{j}" '
            f'srcOrd="{j - 1}" destOrd="0"/>'
        )
    data = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<dgm:dataModel xmlns:dgm="{_DGM}" xmlns:a="{_A}">'
        f'<dgm:ptLst>{"".join(pts)}</dgm:ptLst>'
        f'<dgm:cxnLst>{"".join(cxns)}</dgm:cxnLst>'
        "</dgm:dataModel>"
    )
    layout_uid = {
        "list": "urn:microsoft.com/office/officeart/2005/8/layout/default",
        "cycle": "urn:microsoft.com/office/officeart/2005/8/layout/cycle2",
        "hierarchy": "urn:microsoft.com/office/officeart/2005/8/layout/hierarchy1",
        "process": "urn:microsoft.com/office/officeart/2005/8/layout/default",
    }.get((art.layout or "process").lower(), "urn:microsoft.com/office/officeart/2005/8/layout/default")
    layout = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<dgm:layoutDef xmlns:dgm="{_DGM}" uniqueId="{layout_uid}">'
        f'<dgm:catLst><dgm:cat type="{(art.layout or "process")}" pri="10000"/></dgm:catLst>'
        f'<dgm:title lang="en-US" val="{_esc(art.title or "SmartArt")}"/>'
        f'<dgm:desc lang="en-US" val="AduPort generated"/>'
        "<dgm:layoutNode name=\"diagram\">"
        "<dgm:varLst/><dgm:alg type=\"composite\"/><dgm:shape/><dgm:presOf/>"
        "<dgm:constrLst/><dgm:ruleLst/>"
        "</dgm:layoutNode>"
        "</dgm:layoutDef>"
    )
    colors = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<dgm:colorsDef xmlns:dgm="{_DGM}" uniqueId="urn:microsoft.com/office/officeart/2005/8/colors/accent1_2">'
        '<dgm:title lang="en-US" val="Accent 1"/><dgm:desc lang="en-US" val=""/>'
        "</dgm:colorsDef>"
    )
    style = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<dgm:styleDef xmlns:dgm="{_DGM}" uniqueId="urn:microsoft.com/office/officeart/2005/8/style/quickStyleSimple1">'
        '<dgm:title lang="en-US" val="Simple"/><dgm:desc lang="en-US" val=""/>'
        "</dgm:styleDef>"
    )
    i = index
    return {
        f"word/diagrams/data{i}.xml": data,
        f"word/diagrams/layout{i}.xml": layout,
        f"word/diagrams/colors{i}.xml": colors,
        f"word/diagrams/quickStyle{i}.xml": style,
    }


def inject_smartart_package(docx_bytes: bytes, arts: list[SmartArt]) -> bytes:
    """Write full diagram parts (data+layout+colors+style) with cxnLst."""
    if not arts:
        return docx_bytes
    try:
        src = zipfile.ZipFile(BytesIO(docx_bytes), "r")
        out_buf = BytesIO()
        with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            existing = set(src.namelist())
            overrides: list[str] = []
            for i, art in enumerate(arts[:20], start=1):
                parts = build_diagram_parts(art, i)
                for path, xml in parts.items():
                    if path not in existing:
                        overrides.append(path)
                # write later
            for info in src.infolist():
                raw = src.read(info.filename)
                if info.filename == "[Content_Types].xml":
                    text = raw.decode("utf-8", "ignore")
                    ctype_map = {
                        "data": "application/vnd.openxmlformats-officedocument.drawingml.diagramData+xml",
                        "layout": "application/vnd.openxmlformats-officedocument.drawingml.diagramLayout+xml",
                        "colors": "application/vnd.openxmlformats-officedocument.drawingml.diagramColors+xml",
                        "quickStyle": "application/vnd.openxmlformats-officedocument.drawingml.diagramStyle+xml",
                    }
                    for path in overrides:
                        part = "/" + path
                        kind = "data"
                        for k in ctype_map:
                            if f"/{k}" in path or path.split("/")[-1].startswith(k):
                                kind = k
                                break
                        if path.split("/")[-1].startswith("quickStyle"):
                            kind = "quickStyle"
                        elif path.split("/")[-1].startswith("layout"):
                            kind = "layout"
                        elif path.split("/")[-1].startswith("colors"):
                            kind = "colors"
                        elif path.split("/")[-1].startswith("data"):
                            kind = "data"
                        if part not in text:
                            text = text.replace(
                                "</Types>",
                                f'<Override PartName="{part}" ContentType="{ctype_map[kind]}"/>'
                                "</Types>",
                                1,
                            )
                    raw = text.encode("utf-8")
                dst.writestr(info.filename, raw)
            for i, art in enumerate(arts[:20], start=1):
                for path, xml in build_diagram_parts(art, i).items():
                    if path not in existing:
                        dst.writestr(path, xml)
        src.close()
        return out_buf.getvalue()
    except Exception:
        return docx_bytes


def inject_ole_embeddings(docx_bytes: bytes, oles: list[OleObject]) -> bytes:
    """Embed oleObjectN.bin + o:OLEObject markers before </w:body>."""
    if not oles:
        return docx_bytes
    try:
        import re

        src = zipfile.ZipFile(BytesIO(docx_bytes), "r")
        out_buf = BytesIO()
        # Pre-read max rId so document + rels stay consistent regardless of zip order
        rels_raw = src.read("word/_rels/document.xml.rels").decode("utf-8", "ignore")
        ids = [int(x) for x in re.findall(r'Id="rId(\d+)"', rels_raw)]
        next_id = max(ids) + 1 if ids else 1
        ole_payloads: list[tuple[str, str, bytes, OleObject]] = []
        for i, ole in enumerate(oles[:20], start=1):
            payload = ole.data or (ole.label or ole.prog_id or "AduPort-ole").encode("utf-8")
            path = f"word/embeddings/oleObject{i}.bin"
            rid = f"rId{next_id + i - 1}"
            ole_payloads.append((path, rid, payload, ole))

        with zipfile.ZipFile(out_buf, "w", compression=zipfile.ZIP_DEFLATED) as dst:
            rels_path = "word/_rels/document.xml.rels"
            doc_path = "word/document.xml"
            for info in src.infolist():
                raw = src.read(info.filename)
                if info.filename == "[Content_Types].xml":
                    text = raw.decode("utf-8", "ignore")
                    if 'Extension="bin"' not in text:
                        text = text.replace(
                            "</Types>",
                            '<Default Extension="bin" '
                            'ContentType="application/vnd.openxmlformats-officedocument.oleObject"/>'
                            "</Types>",
                            1,
                        )
                    raw = text.encode("utf-8")
                elif info.filename == rels_path:
                    text = raw.decode("utf-8", "ignore")
                    inserts = []
                    for path, rid, _payload, _ole in ole_payloads:
                        target = path[len("word/") :]
                        inserts.append(
                            f'<Relationship Id="{rid}" '
                            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/oleObject" '
                            f'Target="{target}"/>'
                        )
                    text = text.replace("</Relationships>", "".join(inserts) + "</Relationships>")
                    raw = text.encode("utf-8")
                elif info.filename == doc_path:
                    text = raw.decode("utf-8", "ignore")
                    chunks = []
                    for j, (_path, rid, _payload, ole) in enumerate(ole_payloads):
                        prog = xml_escape(ole.prog_id or "Package")
                        label = xml_escape(ole.label or prog)
                        chunks.append(
                            "<w:p>"
                            f"<w:r><w:t>{label}</w:t></w:r>"
                            "</w:p>"
                            "<w:p><w:r>"
                            f'<o:OLEObject Type="Embed" ProgID="{prog}" '
                            f'ShapeID="_x0000_i{100 + j}" DrawAspect="Content" '
                            f'r:id="{rid}" '
                            'xmlns:o="urn:schemas-microsoft-com:office:office" '
                            f'xmlns:r="{_R}"/>'
                            "</w:r></w:p>"
                        )
                    if "</w:body>" in text:
                        text = text.replace("</w:body>", "".join(chunks) + "</w:body>", 1)
                    raw = text.encode("utf-8")
                dst.writestr(info.filename, raw)
            for path, _rid, payload, _ole in ole_payloads:
                dst.writestr(path, payload)
        src.close()
        return out_buf.getvalue()
    except Exception:
        return docx_bytes
