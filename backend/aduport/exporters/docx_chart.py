"""Inject real DrawingML charts into a DOCX package (word/charts + inline drawing)."""

from __future__ import annotations

import io
import re
import zipfile
from xml.sax.saxutils import escape

from aduport.core.document_model import Chart
from aduport.exporters.xlsx_exporter import _chart_space_xml

_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_NS_R_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_MARKER_RE = re.compile(r"«ADUPORT_CHART_(\d+)»")


def chart_placeholder(index: int) -> str:
    """Visible marker written into document body; replaced by inject_word_charts."""
    return f"«ADUPORT_CHART_{index}»"


def chart_anchor(index: int) -> str:
    """Hidden run kept in the drawing paragraph so import can put the chart back."""
    return f"ADUPORT-CHART-{index}"


def inject_word_charts(docx_bytes: bytes, charts: list[Chart]) -> bytes:
    """Replace chart placeholders with inline DrawingML + word/charts/chartN.xml."""
    if not charts:
        return docx_bytes

    with zipfile.ZipFile(io.BytesIO(docx_bytes), "r") as zin:
        files = {n: zin.read(n) for n in zin.namelist()}

    doc_xml = files.get("word/document.xml", b"").decode("utf-8")
    rels_path = "word/_rels/document.xml.rels"
    rels = files.get(rels_path, b"").decode("utf-8")
    if not rels.strip():
        rels = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{_NS_R}"></Relationships>'
        )

    found = [int(x) for x in re.findall(r'Id="rId(\d+)"', rels)]
    next_id = (max(found) if found else 1) + 1
    ct = files.get("[Content_Types].xml", b"").decode("utf-8")

    for i, ch in enumerate(charts, start=1):
        chart_path = f"word/charts/chart{i}.xml"
        files[chart_path] = _chart_space_xml(ch).encode("utf-8")
        rid = f"rId{next_id}"
        next_id += 1
        rel = (
            f'<Relationship Id="{rid}" '
            f'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" '
            f'Target="charts/chart{i}.xml"/>'
        )
        if "</Relationships>" in rels:
            rels = rels.replace("</Relationships>", rel + "</Relationships>")
        else:
            rels += rel
        if f"/word/charts/chart{i}.xml" not in ct:
            override = (
                f'<Override PartName="/word/charts/chart{i}.xml" '
                f'ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
            )
            ct = ct.replace("</Types>", override + "</Types>")

        drawing = _inline_chart_drawing(rid, title=ch.title or ch.chart_type or "Chart")
        marker = chart_placeholder(i)
        anchor = chart_anchor(i)
        drawing_para = (
            f'<w:p xmlns:w="{_NS_W}" xmlns:r="{_NS_R_REL}" '
            f'xmlns:wp="{_NS_WP}" xmlns:a="{_NS_A}" xmlns:c="{_NS_C}">'
            f"<w:r><w:rPr><w:vanish/></w:rPr>"
            f'<w:t xml:space="preserve">{escape(anchor)}</w:t></w:r>'
            f"<w:r>{drawing}</w:r></w:p>"
        )
        doc_xml, replaced = _replace_single_marker_paragraph(doc_xml, marker, drawing_para)
        if not replaced:
            if "</w:body>" in doc_xml:
                doc_xml = doc_xml.replace("</w:body>", drawing_para + "</w:body>", 1)
            else:
                doc_xml += drawing_para

    files["word/document.xml"] = doc_xml.encode("utf-8")
    files[rels_path] = rels.encode("utf-8")
    files["[Content_Types].xml"] = ct.encode("utf-8")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for name, data in files.items():
            zout.writestr(name, data)
    return out.getvalue()


def _replace_single_marker_paragraph(
    doc_xml: str, marker: str, drawing_para: str
) -> tuple[str, bool]:
    """Replace the single ``<w:p>…marker…</w:p>`` without crossing other paragraphs."""
    for needle in (escape(marker), marker):
        idx = doc_xml.find(needle)
        if idx < 0:
            continue
        start = doc_xml.rfind("<w:p", 0, idx)
        end = doc_xml.find("</w:p>", idx)
        if start < 0 or end < 0:
            return doc_xml.replace(needle, drawing_para, 1), True
        return doc_xml[:start] + drawing_para + doc_xml[end + len("</w:p>") :], True
    return doc_xml, False


def _inline_chart_drawing(r_id: str, *, title: str) -> str:
    """w:drawing wrapping a chart reference (cx/cy ≈ 5\"×3\")."""
    cx, cy = 4572000, 2743200
    name = escape((title or "Chart")[:60])
    return (
        f'<w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="1" name="{name}"/>'
        f"<wp:cNvGraphicFramePr/>"
        f'<a:graphic>'
        f'<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        f'<c:chart xmlns:c="{_NS_C}" xmlns:r="{_NS_R_REL}" r:id="{r_id}"/>'
        f"</a:graphicData>"
        f"</a:graphic>"
        f"</wp:inline>"
        f"</w:drawing>"
    )


def parse_docx_charts(zf: zipfile.ZipFile) -> list[Chart]:
    """Best-effort Chart AST from word/charts/chart*.xml (same literal caches as xlsx)."""
    return parse_ooxml_charts(zf, "word/charts/chart")


def parse_ooxml_charts(zf: zipfile.ZipFile, prefix: str) -> list[Chart]:
    """Chart AST from DrawingML chart parts (docx ``word/charts`` or pptx ``ppt/charts``)."""
    out: list[Chart] = []
    paths = sorted(
        p
        for p in zf.namelist()
        if p.startswith(prefix) and p.endswith(".xml") and "/_rels/" not in p
    )
    for path in paths[:20]:
        try:
            raw = zf.read(path).decode("utf-8", "ignore")
        except Exception:
            continue
        title = ""
        tm = re.search(r"<a:t>([^<]*)</a:t>", raw)
        if tm:
            title = tm.group(1).strip()
        ctype = "bar"
        if "<c:lineChart" in raw:
            ctype = "line"
        elif "<c:pieChart" in raw or "<c:doughnutChart" in raw:
            ctype = "pie"
        elif "<c:areaChart" in raw:
            ctype = "area"
        elif "<c:scatterChart" in raw:
            ctype = "scatter"
        elif "<c:barChart" in raw:
            ctype = "bar"
        cat_block = re.search(r"<c:cat\b[^>]*>(.*?)</c:cat>", raw, flags=re.DOTALL)
        categories: list[str] = []
        if cat_block:
            categories = re.findall(r"<c:v>([^<]*)</c:v>", cat_block.group(1))
        series: list[dict] = []
        for ser in re.findall(r"<c:ser>.*?</c:ser>", raw, flags=re.DOTALL)[:12]:
            nm = re.search(r"<c:tx>.*?<c:v>([^<]*)</c:v>", ser, flags=re.DOTALL)
            name = nm.group(1) if nm else f"S{len(series) + 1}"
            num = re.search(r"<c:numLit>(.*?)</c:numLit>", ser, flags=re.DOTALL)
            if not num:
                num = re.search(r"<c:numCache>(.*?)</c:numCache>", ser, flags=re.DOTALL)
            values: list[float] = []
            if num:
                for v in re.findall(r"<c:v>([^<]*)</c:v>", num.group(1)):
                    try:
                        values.append(float(v))
                    except ValueError:
                        values.append(0.0)
            series.append({"name": name, "values": values})
        out.append(
            Chart(
                title=title or path.rsplit("/", 1)[-1].replace(".xml", ""),
                chart_type=ctype,
                categories=categories,
                series=series,
            )
        )
    return out
