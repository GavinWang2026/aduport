"""Built-in breadth: formats work without anydoc/docling."""

from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from docx import Document

from aduport.core.converter import convert
from aduport.core.registry import ensure_builtin_plugins, registered_import_formats
from aduport.importers.docx_text import try_extract_docx_model
from aduport.importers.ods_importer import import_ods
from aduport.importers.odp_importer import import_odp


def _minimal_docx(text: str = "Hello builtin DOCX") -> bytes:
    doc = Document()
    doc.add_heading("Title", level=1)
    doc.add_paragraph(text)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _minimal_ods() -> bytes:
    content = """<?xml version="1.0"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
 <office:body><office:spreadsheet>
  <table:table table:name="S1">
   <table:table-row>
    <table:table-cell><text:p>A</text:p></table:table-cell>
    <table:table-cell><text:p>B</text:p></table:table-cell>
   </table:table-row>
   <table:table-row>
    <table:table-cell><text:p>1</text:p></table:table-cell>
    <table:table-cell><text:p>2</text:p></table:table-cell>
   </table:table-row>
  </table:table>
 </office:spreadsheet></office:body>
</office:document-content>"""
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.spreadsheet")
        zf.writestr("content.xml", content)
    return buf.getvalue()


def _minimal_odp() -> bytes:
    content = """<?xml version="1.0"?>
<office:document-content
 xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
 <office:body><office:presentation>
  <draw:page draw:name="P1">
   <text:p>Slide hello</text:p>
  </draw:page>
 </office:presentation></office:body>
</office:document-content>"""
    buf = BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.presentation")
        zf.writestr("content.xml", content)
    return buf.getvalue()


def test_docx_native_without_docling():
    raw = _minimal_docx("native path works")
    model = try_extract_docx_model(raw, title="t")
    assert model is not None
    blob = " ".join(
        getattr(getattr(n, "text", None), "text", None) or str(getattr(n, "text", ""))
        for n in model.nodes
    )
    assert "native path works" in blob
    out = convert(raw, "docx", "md", filename="t.docx")
    assert b"native path works" in out or "native path works" in out.decode()


def test_ods_odp_builtin_importers():
    ods = import_ods(_minimal_ods(), "t.ods")
    assert any(getattr(n, "type", None) == "table" for n in ods.nodes)
    odp = import_odp(_minimal_odp(), "t.odp")
    blob = " ".join(
        getattr(getattr(n, "text", None), "text", None) or ""
        for n in odp.nodes
        if hasattr(n, "text")
    )
    assert "Slide hello" in blob


def test_registry_exposes_former_anydoc_only_formats():
    ensure_builtin_plugins()
    have = registered_import_formats()
    for fmt in ("ods", "odp", "xls", "ppt", "xlsm", "docm", "ppsx"):
        assert fmt in have, fmt
