"""§2.1 AST node × exporter coverage: sentinel must appear in export body."""

from __future__ import annotations

import io
import re
import zipfile

import pytest

from app.core.document_model import (
    Blockquote,
    Chart,
    CodeBlock,
    Comment,
    DocumentModel,
    Footnote,
    Heading,
    HorizontalRule,
    HtmlBlock,
    Image,
    ListItem,
    MathBlock,
    OleObject,
    Paragraph,
    PivotTable,
    Revision,
    SmartArt,
    SpeakerNote,
    Table,
    TextBox,
)
from app.exporters import (
    docx_exporter,
    epub_exporter,
    html_exporter,
    json_exporter,
    latex_exporter,
    md_exporter,
    odp_exporter,
    ods_exporter,
    odt_exporter,
    pptx_exporter,
    rtf_exporter,
    txt_exporter,
    xlsx_exporter,
)
from app.exporters.pdf_simple import export_pdf_simple

TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)

_NODES = [
    ("Heading", Heading(level=1, text="SENT_H1_Alpha"), "SENT_H1_Alpha"),
    ("Paragraph", Paragraph(text="SENT_P_Bravo"), "SENT_P_Bravo"),
    ("ListItem", ListItem(text="SENT_LI_Charlie", ordered=False), "SENT_LI_Charlie"),
    ("Table", Table(rows=[["SENT_T_Delta", "B"], ["1", "2"]]), "SENT_T_Delta"),
    (
        "Image",
        Image(data=TINY_PNG, alt_text="SENT_IMG_Echo", mime_type="image/png"),
        "SENT_IMG_Echo",
    ),
    ("CodeBlock", CodeBlock(content="SENT_CB_Foxtrot", lang="py"), "SENT_CB_Foxtrot"),
    ("Blockquote", Blockquote(paragraphs=["SENT_BQ_Golf"]), "SENT_BQ_Golf"),
    ("MathBlock", MathBlock(content="SENT_MATH_Hotel", display=True), "SENT_MATH_Hotel"),
    ("Footnote", Footnote(id="1", text="SENT_FN_India"), "SENT_FN_India"),
    ("HtmlBlock", HtmlBlock(content="<p>SENT_HTML_Juliet</p>"), "SENT_HTML_Juliet"),
    ("Comment", Comment(author="a", text="SENT_CM_Kilo"), "SENT_CM_Kilo"),
    ("Revision", Revision(change="insert", author="ed", text="SENT_REV_Lima"), "SENT_REV_Lima"),
    ("TextBox", TextBox(text="SENT_TB_Mike", source="test"), "SENT_TB_Mike"),
    (
        "PivotTable",
        PivotTable(name="SENT_PV_November", row_fields=["R"], data_fields=["D"]),
        "SENT_PV_November",
    ),
    ("HorizontalRule", HorizontalRule(), "---"),
    (
        "Chart",
        Chart(
            title="SENT_CH_Oscar",
            chart_type="bar",
            categories=["A"],
            series=[{"name": "S", "values": [1]}],
        ),
        "SENT_CH_Oscar",
    ),
    (
        "SmartArt",
        SmartArt(title="SENT_SA_Papa", layout="process", items=["one", "two"]),
        "SENT_SA_Papa",
    ),
    (
        "OleObject",
        OleObject(prog_id="Package", label="SENT_OLE_Quebec"),
        "SENT_OLE_Quebec",
    ),
    (
        "SpeakerNote",
        SpeakerNote(text="SENT_SN_Romeo", slide_index=1),
        "SENT_SN_Romeo",
    ),
]


def _as_bytes(out) -> bytes:
    return out.encode() if isinstance(out, str) else out


def _blob_text(fmt: str, data: bytes) -> str:
    if data[:2] == b"PK":
        parts: list[str] = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                raw = zf.read(name)
                try:
                    parts.append(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    parts.append(raw.decode("latin-1", "ignore"))
        return "\n".join(parts)
    if fmt == "pdf":
        return data.decode("latin-1", "ignore")
    return data.decode("utf-8", "ignore")


def _xlsx_body_text(data: bytes) -> str:
    """Visible worksheets only (exclude _AST / other _-prefixed sheets)."""
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        wb = zf.read("xl/workbook.xml").decode("utf-8", "ignore")
        names = re.findall(r'name="([^"]+)"', wb)
        for i, name in enumerate(names, 1):
            if name.startswith("_"):
                continue
            path = f"xl/worksheets/sheet{i}.xml"
            if path in zf.namelist():
                parts.append(zf.read(path).decode("utf-8", "ignore"))
    return "\n".join(parts)


_EXPORTERS = {
    "md": lambda m: _as_bytes(md_exporter.export_md(m)),
    "html": lambda m: _as_bytes(html_exporter.export_html(m)),
    "txt": lambda m: _as_bytes(txt_exporter.export_txt(m)),
    "docx": lambda m: docx_exporter.export_docx(m),
    "pdf": lambda m: export_pdf_simple(m),
    "xlsx": lambda m: xlsx_exporter.export_xlsx(m),
    "pptx": lambda m: pptx_exporter.export_pptx(m),
    "odt": lambda m: odt_exporter.export_odt(m),
    "ods": lambda m: ods_exporter.export_ods(m),
    "odp": lambda m: odp_exporter.export_odp(m),
    "rtf": lambda m: _as_bytes(rtf_exporter.export_rtf(m)),
    "latex": lambda m: _as_bytes(latex_exporter.export_latex(m)),
    "json": lambda m: _as_bytes(json_exporter.export_json(m)),
    "epub": lambda m: _as_bytes(epub_exporter.export_epub(m)),
}


@pytest.mark.parametrize("node_name,node,sentinel", _NODES, ids=[n[0] for n in _NODES])
@pytest.mark.parametrize("fmt", list(_EXPORTERS), ids=list(_EXPORTERS))
def test_ast_exporter_sentinel_in_body(node_name: str, node, sentinel: str, fmt: str):
    model = DocumentModel(title="T", nodes=[node])
    data = _EXPORTERS[fmt](model)
    body = _xlsx_body_text(data) if fmt == "xlsx" else _blob_text(fmt, data)
    assert sentinel in body, f"{fmt}/{node_name}: sentinel {sentinel!r} missing from body"
