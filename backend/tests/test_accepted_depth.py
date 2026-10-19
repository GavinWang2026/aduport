"""Depth upgrades for formerly ACCEPTED vertical gaps (PARTIAL implementations)."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

import pytest

from aduport.config import settings
from aduport.core.document_model import DocumentModel, Image, Paragraph, PivotTable, Revision, Table, TableCell
from aduport.exporters.md_exporter import export_md
from aduport.exporters.html_render import render_body_html
from aduport.exporters.xlsx_exporter import export_xlsx, import_xlsx
from aduport.importers.docx_ooxml import enrich_docx_model
from aduport.importers.epub_text import _epub_drm_meta
from aduport.importers.pdf_text_fallback import extract_pdf_text_model


_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@pytest.fixture(autouse=True)
def _enable_xlsx_ast_sheet(monkeypatch: pytest.MonkeyPatch):
    """These depth tests exercise the opt-in `_AST` meta sheet."""
    monkeypatch.setattr(settings, "xlsx_ast_sheet", True)


def _minimal_docx_zip() -> bytes:
    doc = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{_W}">
  <w:body>
    <w:p><w:ins w:author="alice"><w:r><w:t>tracked insert</w:t></w:r></w:ins></w:p>
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='xml' ContentType='application/xml'/></Types>",
        )
        zf.writestr("word/document.xml", doc)
    return buf.getvalue()


def test_docx_enrich_track_changes_and_section_props():
    raw = _minimal_docx_zip()
    model = enrich_docx_model(DocumentModel(nodes=[]), raw)
    revs = [n for n in model.nodes if isinstance(n, Revision)]
    assert revs and revs[0].change == "insert" and "tracked" in revs[0].text
    assert model.metadata.get("section_props", {}).get("page_width_mm")


def test_revision_md_and_html_export():
    model = DocumentModel(
        nodes=[Revision(change="insert", author="bob", text="new bit")]
    )
    md = export_md(model)
    assert "++new bit++" in md
    html = render_body_html(model)
    assert "<ins" in html and "new bit" in html


def test_image_float_wrap_html():
    model = DocumentModel(
        nodes=[
            Image(
                data=b"x",
                alt_text="pic",
                mime_type="image/png",
                wrap="left",
            )
        ]
    )
    html = render_body_html(model)
    assert "float:left" in html


def test_xlsx_formula_roundtrip():
    sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData>
<row r="1"><c r="A1" t="inlineStr"><is><t>A</t></is></c></row>
<row r="2"><c r="A2"><f>SUM(1,2)</f><v>3</v></c></row>
</sheetData></worksheet>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Override PartName='/xl/workbook.xml' "
            "ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'/>"
            "<Override PartName='/xl/worksheets/sheet1.xml' "
            "ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>"
            "</Types>",
        )
        zf.writestr(
            "xl/workbook.xml",
            "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
            "<sheets><sheet name='S1' sheetId='1'/></sheets></workbook>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
    model = import_xlsx(buf.getvalue(), "f.xlsx")
    assert model.metadata.get("export_policy") == "values_and_formulas"
    tbl = model.nodes[0]
    assert isinstance(tbl, Table) and tbl.cells
    assert tbl.cells[1][0].formula == "SUM(1,2)"
    back = import_xlsx(export_xlsx(model), "out.xlsx")
    back_tbl = back.nodes[0]
    assert isinstance(back_tbl, Table) and back_tbl.cells[1][0].formula == "SUM(1,2)"


def test_epub_drm_metadata():
    meta = _epub_drm_meta({"META-INF/encryption.xml", "OEBPS/chap.xhtml"})
    assert meta.get("drm_protected") is True


def test_pdf_layout_metadata_keys():
    from aduport.exporters.pdf_simple import export_pdf_simple

    pdf = export_pdf_simple(
        DocumentModel(title="t", nodes=[Paragraph(text="Hello PDF layout meta")])
    )
    model = extract_pdf_text_model(pdf, "t.pdf")
    layout = model.metadata.get("pdf_layout") or {}
    assert layout.get("page_count", 0) >= 1
    assert layout.get("pixel_mode") == "charbox_v1"
    blocks = layout.get("blocks") or []
    assert isinstance(blocks, list)


def test_docx_content_control():
    doc = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{_W}">
  <w:body>
    <w:sdt>
      <w:sdtPr><w:tag w:val="cc1"/><w:alias w:val="Title"/></w:sdtPr>
      <w:sdtContent><w:p><w:r><w:t>controlled</w:t></w:r></w:p></w:sdtContent>
    </w:sdt>
  </w:body>
</w:document>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='xml' ContentType='application/xml'/></Types>",
        )
        zf.writestr("word/document.xml", doc)
    model = enrich_docx_model(DocumentModel(nodes=[]), buf.getvalue())
    ccs = [n for n in model.nodes if n.type == "content_control"]
    assert ccs and ccs[0].tag == "cc1" and "controlled" in ccs[0].text


def test_xlsx_pivot_table_ast():
    pivot = """<?xml version="1.0" encoding="UTF-8"?>
<pivotTable xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 name="Pivot1" cacheId="1">
 <location ref="B3:E7"/>
 <pivotFields count="2">
  <pivotField name="Region" axis="axisRow"/>
  <pivotField name="Sales" axis="axisValues"/>
 </pivotFields>
</pivotTable>"""
    cache = """<?xml version="1.0" encoding="UTF-8"?>
<pivotCacheDefinition xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <cacheSource type="worksheet"><worksheetSource ref="A1:D10"/></cacheSource>
</pivotCacheDefinition>"""
    sheet = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>x</t></is></c></row></sheetData>
</worksheet>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Override PartName='/xl/workbook.xml' "
            "ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'/>"
            "<Override PartName='/xl/worksheets/sheet1.xml' "
            "ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/>"
            "</Types>",
        )
        zf.writestr(
            "xl/workbook.xml",
            "<workbook xmlns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'>"
            "<sheets><sheet name='S1' sheetId='1'/></sheets></workbook>",
        )
        zf.writestr("xl/worksheets/sheet1.xml", sheet)
        zf.writestr("xl/pivotTables/pivotTable1.xml", pivot)
        zf.writestr("xl/pivotCache/pivotCacheDefinition1.xml", cache)
    model = import_xlsx(buf.getvalue(), "p.xlsx")
    pivots = [n for n in model.nodes if isinstance(n, PivotTable)]
    assert pivots and pivots[0].name == "Pivot1"
    assert pivots[0].row_fields == ["Region"]
    assert pivots[0].source_range == "A1:D10"


def test_xlsx_pivot_export_roundtrip():
    model = DocumentModel(
        nodes=[
            Table(rows=[["Region", "Sales"], ["East", "100"]], name="Data"),
            PivotTable(
                name="Pivot1",
                cache_id="1",
                source_range="A1:B2",
                location="B3:E7",
                row_fields=["Region"],
                data_fields=["Sales"],
            ),
        ]
    )
    back = import_xlsx(export_xlsx(model), "out.xlsx")
    pivots = [n for n in back.nodes if isinstance(n, PivotTable)]
    assert pivots and pivots[0].name == "Pivot1"
    assert pivots[0].row_fields == ["Region"]
    assert pivots[0].source_range == "A1:B2"


def test_pdf_layout_html_preview():
    from aduport.exporters.pdf_simple import export_pdf_simple

    pdf = export_pdf_simple(DocumentModel(nodes=[Paragraph(text="Layout preview text")]))
    model = extract_pdf_text_model(pdf, "t.pdf")
    html = render_body_html(model)
    assert "pdf-layout-preview" in html
    assert "pdf-block" in html


def test_pptx_animation_metadata():
    from aduport.importers.pptx_text import try_extract_pptx_model

    slide = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Hi</a:t></a:r></a:p></p:txBody></p:sp></p:spTree>
 <p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite"><p:childTnLst>
 <p:seq><p:cTn id="2"><p:childTnLst><p:par><p:cTn><p:childTnLst>
 <p:animEffect transition="in" filter="fade"/></p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:seq>
 </p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>
 </p:cSld></p:sld>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", slide)
    model = try_extract_pptx_model(buf.getvalue())
    assert model and model.metadata.get("pptx_animations")


def test_textbox_ast_docx():
    doc = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{_W}">
  <w:body>
    <w:p><w:r><w:txbxContent><w:p><w:r><w:t>box text</w:t></w:r></w:p></w:txbxContent></w:r></w:p>
  </w:body>
</w:document>"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
            "<Default Extension='xml' ContentType='application/xml'/></Types>",
        )
        zf.writestr("word/document.xml", doc)
    model = enrich_docx_model(DocumentModel(nodes=[]), buf.getvalue())
    boxes = [n for n in model.nodes if n.type == "text_box"]
    assert boxes and "box text" in boxes[0].text


def test_docx_export_revision_ins():
    from aduport.exporters.docx_exporter import export_docx

    model = DocumentModel(nodes=[Revision(change="insert", author="a", text="added")])
    raw = export_docx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        xml = zf.read("word/document.xml").decode()
    assert "w:ins" in xml and "added" in xml


def test_docx_export_content_control_sdt():
    from aduport.core.document_model import ContentControl
    from aduport.exporters.docx_exporter import export_docx

    model = DocumentModel(
        nodes=[ContentControl(tag="t1", alias="Title", text="hello control")]
    )
    raw = export_docx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        xml = zf.read("word/document.xml").decode()
    assert "w:sdt" in xml and "hello control" in xml


def test_docx_export_content_control_dropdown():
    from aduport.core.document_model import ContentControl
    from aduport.exporters.docx_exporter import export_docx

    model = DocumentModel(
        nodes=[
            ContentControl(
                tag="status",
                alias="Status",
                control_type="dropdown",
                text="Draft",
            )
        ]
    )
    raw = export_docx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        xml = zf.read("word/document.xml").decode()
    assert "w:dropDownList" in xml and "Draft" in xml


def test_docx_export_textbox_txbx():
    from aduport.core.document_model import TextBox
    from aduport.exporters.docx_exporter import export_docx

    model = DocumentModel(nodes=[TextBox(text="box text", source="docx:txbx")])
    raw = export_docx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        xml = zf.read("word/document.xml").decode()
    assert "w:txbxContent" in xml and "box text" in xml


def test_xlsx_pivot_cache_fields_export():
    model = DocumentModel(
        nodes=[
            Table(rows=[["A"]], name="S"),
            PivotTable(
                name="P1",
                source_range="A1:B2",
                location="C3",
                row_fields=["Region"],
                data_fields=["Sales"],
            ),
        ]
    )
    raw = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        cache = zf.read("xl/pivotCache/pivotCacheDefinition1.xml").decode()
    assert 'cacheField name="Region"' in cache
    assert 'cacheField name="Sales"' in cache


def test_xlsx_pivot_cache_records_with_data():
    model = DocumentModel(
        nodes=[
            Table(rows=[["Region", "Sales"], ["East", "100"], ["West", "200"]]),
            PivotTable(
                name="P1",
                source_range="A1:B3",
                location="C3",
                row_fields=["Region"],
                data_fields=["Sales"],
            ),
        ]
    )
    raw = export_xlsx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        records = zf.read("xl/pivotCache/pivotCacheRecords1.xml").decode()
    assert 'count="2"' in records
    assert 'v="East"' in records and 'v="100"' in records


def test_html_footnotes_end_section():
    from aduport.core.document_model import Footnote

    model = DocumentModel(
        nodes=[
            Paragraph(text="Body"),
            Footnote(id="1", text="Note one"),
        ]
    )
    html = render_body_html(model)
    assert "section class=\"footnotes\"" in html
    assert "Note one" in html
    assert html.index("Body") < html.index("footnotes")


def test_txt_latex_comment_export():
    from aduport.core.document_model import Comment
    from aduport.exporters.latex_exporter import export_latex
    from aduport.exporters.txt_exporter import export_txt

    model = DocumentModel(
        nodes=[Comment(author="alice", text="fix typo", anchor="p1")]
    )
    txt = export_txt(model)
    assert "alice" in txt and "fix typo" in txt
    tex = export_latex(model)
    assert "alice" in tex and "fix typo" in tex


def test_pptx_footnotes_and_animations_slides():
    from aduport.core.document_model import Footnote
    from aduport.exporters.pptx_exporter import export_pptx

    model = DocumentModel(
        nodes=[Footnote(id="a", text="see appendix")],
        metadata={
            "pptx_animations": [
                {"slide": 1, "node_count": 2, "effects": ["fade"]},
            ]
        },
    )
    raw = export_pptx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        slide_xml = b"".join(
            zf.read(n) for n in zf.namelist() if n.startswith("ppt/slides/slide")
        )
    assert b"Footnotes" in slide_xml or b"appendix" in slide_xml
    assert b"Animation" in slide_xml or b"fade" in slide_xml


def test_docx_pivot_table_and_footnotes_export():
    from aduport.core.document_model import Footnote
    from aduport.exporters.docx_exporter import export_docx

    model = DocumentModel(
        nodes=[
            Paragraph(text="Body"),
            Footnote(id="1", text="See note"),
            PivotTable(
                name="SalesPivot",
                source_range="A1:C10",
                location="E3",
                row_fields=["Region"],
                data_fields=["Amount"],
            ),
        ]
    )
    raw = export_docx(model)
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        xml = zf.read("word/document.xml").decode()
        fn_xml = zf.read("word/footnotes.xml").decode()
    assert "SalesPivot" in xml and "Region" in xml and "Amount" in xml
    assert "footnoteReference" in xml
    assert "See note" in fn_xml


def test_mobi_footnotes_css_embedded():
    from aduport.core.document_model import Footnote
    from aduport.exporters.mobi_exporter import export_mobi

    model = DocumentModel(
        nodes=[
            Paragraph(text="Chapter"),
            Footnote(id="n1", text="Endnote text"),
        ]
    )
    raw = export_mobi(model)
    assert b".footnotes" in raw and b"Endnote text" in raw


def test_pdf_simple_pivot_line():
    from aduport.exporters.pdf_simple import _model_lines

    model = DocumentModel(
        nodes=[
            PivotTable(name="P", row_fields=["A"], data_fields=["B"]),
        ]
    )
    lines = _model_lines(model)
    assert any("PivotTable" in ln and "A" in ln for ln in lines)


def test_xlsx_revision_content_control_roundtrip():
    from aduport.core.document_model import ContentControl

    model = DocumentModel(
        nodes=[
            Table(rows=[["x"]], name="Data"),
            Revision(change="insert", author="alice", anchor="p1", text="added"),
            Revision(change="delete", author="bob", text="removed"),
            ContentControl(tag="t1", alias="Title", control_type="plain", text="hello"),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    revs = [n for n in back.nodes if isinstance(n, Revision)]
    ctrls = [n for n in back.nodes if isinstance(n, ContentControl)]
    assert len(revs) == 2
    assert revs[0].change == "insert" and revs[0].author == "alice"
    assert revs[1].change == "delete"
    assert len(ctrls) == 1 and ctrls[0].tag == "t1" and ctrls[0].text == "hello"
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        wb = zf.read("xl/workbook.xml").decode()
    assert "_AST" in wb


def test_xlsx_unified_ast_all_node_types():
    from aduport.core.document_model import ContentControl, TextBox

    model = DocumentModel(
        nodes=[
            Table(rows=[["x"]], name="Data"),
            Revision(change="insert", author="a", text="ins"),
            ContentControl(tag="t", alias="T", text="ctrl"),
            TextBox(text="box", source="docx:body", x_mm=1.0),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    assert sum(isinstance(n, Revision) for n in back.nodes) == 1
    assert sum(isinstance(n, ContentControl) for n in back.nodes) == 1
    assert sum(isinstance(n, TextBox) for n in back.nodes) == 1
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        # Prefer sheet named _AST (may not be sheet2 when Content sheet exists)
        wb = zf.read("xl/workbook.xml").decode()
        names = re.findall(r'name="([^"]+)"', wb)
        idx = names.index("_AST") + 1 if "_AST" in names else 2
        ast = zf.read(f"xl/worksheets/sheet{idx}.xml").decode()
    assert "revision" in ast and "content_control" in ast and "text_box" in ast


def test_xlsx_ast_pivot_table_row():
    model = DocumentModel(
        nodes=[
            Table(rows=[["Region", "Sales"], ["East", "100"]], name="Data"),
            PivotTable(
                name="SalesPivot",
                source_range="A1:B2",
                location="D3",
                row_fields=["Region"],
                col_fields=[],
                data_fields=["Sales"],
            ),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    pivots = [n for n in back.nodes if isinstance(n, PivotTable)]
    assert len(pivots) == 1
    assert pivots[0].name == "SalesPivot"
    assert pivots[0].row_fields == ["Region"]
    assert pivots[0].data_fields == ["Sales"]
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        ast = zf.read("xl/worksheets/sheet2.xml").decode()
    assert "pivot_table" in ast and "SalesPivot" in ast


def test_xlsx_ast_comment_roundtrip():
    from aduport.core.document_model import Comment

    model = DocumentModel(
        nodes=[
            Table(rows=[["x"]], name="Data"),
            Comment(author="alice", anchor="p1", text="fix typo"),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    comments = [n for n in back.nodes if isinstance(n, Comment)]
    assert len(comments) == 1
    assert comments[0].author == "alice"
    assert comments[0].anchor == "p1"
    assert comments[0].text == "fix typo"
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        ast = zf.read("xl/worksheets/sheet2.xml").decode()
    assert "comment" in ast and "fix typo" in ast


def test_xlsx_ast_footnote_roundtrip():
    from aduport.core.document_model import Footnote

    model = DocumentModel(
        nodes=[
            Table(rows=[["x"]], name="Data"),
            Footnote(id="fn1", text="See appendix A"),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    fns = [n for n in back.nodes if isinstance(n, Footnote)]
    assert len(fns) == 1
    assert fns[0].id == "fn1"
    assert fns[0].text == "See appendix A"
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        ast = zf.read("xl/worksheets/sheet2.xml").decode()
    assert "footnote" in ast and "appendix" in ast


def test_xlsx_ast_math_roundtrip():
    from aduport.core.document_model import MathBlock

    model = DocumentModel(
        nodes=[
            Table(rows=[["x"]], name="Data"),
            MathBlock(content="E=mc^2", display=True),
            MathBlock(content="a+b", display=False),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    maths = [n for n in back.nodes if isinstance(n, MathBlock)]
    assert len(maths) == 2
    assert maths[0].content == "E=mc^2" and maths[0].display is True
    assert maths[1].content == "a+b" and maths[1].display is False
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        ast = zf.read("xl/worksheets/sheet2.xml").decode()
    assert "math_block" in ast and "E=mc^2" in ast


def test_xlsx_ast_image_html_roundtrip():
    from aduport.core.document_model import HtmlBlock, Image

    png = b"\x89PNG\r\n\x1a\n"
    model = DocumentModel(
        nodes=[
            Table(rows=[["x"]], name="Data"),
            Image(data=png, alt_text="logo", mime_type="image/png", wrap="right"),
            HtmlBlock(content="<div>frag</div>"),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    imgs = [n for n in back.nodes if isinstance(n, Image)]
    htmls = [n for n in back.nodes if isinstance(n, HtmlBlock)]
    assert len(imgs) == 1 and imgs[0].alt_text == "logo" and imgs[0].wrap == "right"
    assert imgs[0].data == png
    assert len(htmls) == 1 and htmls[0].content == "<div>frag</div>"
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        ast = zf.read("xl/worksheets/sheet2.xml").decode()
    assert "image" in ast and "html_block" in ast


def test_xlsx_ast_code_blockquote_roundtrip():
    from aduport.core.document_model import Blockquote, CodeBlock

    model = DocumentModel(
        nodes=[
            Table(rows=[["x"]], name="Data"),
            CodeBlock(lang="bash", content="echo hi"),
            Blockquote(paragraphs=["Quote one", "Quote two"]),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    codes = [n for n in back.nodes if isinstance(n, CodeBlock)]
    assert len(codes) == 1 and codes[0].lang == "bash" and codes[0].content == "echo hi"
    bqs = [n for n in back.nodes if isinstance(n, Blockquote)]
    assert len(bqs) == 1 and bqs[0].paragraphs == ["Quote one", "Quote two"]
    meta = back.metadata or {}
    assert meta.get("code_blocks_imported") == 1
    assert meta.get("blockquotes_imported") == 1
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        ast = zf.read("xl/worksheets/sheet2.xml").decode()
    assert "code_block" in ast and "blockquote" in ast


def test_xlsx_ast_structure_nodes_roundtrip():
    from aduport.core.document_model import Heading, HorizontalRule, ListItem, Paragraph

    model = DocumentModel(
        nodes=[
            Table(rows=[["x"]], name="Data"),
            Heading(level=2, text="Title", anchor="t1"),
            Paragraph(text="Body text."),
            ListItem(text="item A", ordered=False, checked=True),
            ListItem(text="item B", ordered=True, level=1),
            HorizontalRule(),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    assert sum(isinstance(n, Heading) for n in back.nodes) == 1
    assert sum(isinstance(n, Paragraph) for n in back.nodes) == 1
    assert sum(isinstance(n, ListItem) for n in back.nodes) == 2
    assert sum(isinstance(n, HorizontalRule) for n in back.nodes) == 1
    meta = back.metadata or {}
    assert meta.get("headings_imported") == 1
    assert meta.get("paragraphs_imported") == 1
    assert meta.get("list_items_imported") == 2
    assert meta.get("horizontal_rules_imported") == 1
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        ast = zf.read("xl/worksheets/sheet2.xml").decode()
    for token in ("heading", "paragraph", "list_item", "horizontal_rule"):
        assert token in ast


def test_xlsx_legacy_structure_sheets_import():
    from aduport.core.document_model import Heading, HorizontalRule, ListItem, Paragraph

    raw = export_xlsx(
        DocumentModel(
            nodes=[
                Table(
                    rows=[["level", "text", "anchor"], ["2", "Legacy H", "lh"]],
                    name="_Headings",
                ),
                Table(rows=[["Legacy paragraph."]], name="_Paragraphs"),
                Table(
                    rows=[["text", "ordered", "level", "checked"], ["Legacy li", "false", "0", ""]],
                    name="_ListItems",
                ),
                Table(rows=[["hr"], ["hr2"]], name="_Rules"),
            ]
        )
    )
    back = import_xlsx(raw, "legacy.xlsx")
    assert any(isinstance(n, Heading) and n.plain_text() == "Legacy H" for n in back.nodes)
    assert any(isinstance(n, Paragraph) and n.plain_text() == "Legacy paragraph." for n in back.nodes)
    assert any(isinstance(n, ListItem) and n.plain_text() == "Legacy li" for n in back.nodes)
    assert sum(isinstance(n, HorizontalRule) for n in back.nodes) == 2


def test_xlsx_legacy_code_blockquote_sheets_import():
    from aduport.core.document_model import Blockquote, CodeBlock

    raw = export_xlsx(
        DocumentModel(
            nodes=[
                Table(
                    rows=[["lang", "content"], ["js", "console.log(1)"]],
                    name="_CodeBlocks",
                ),
                Table(rows=[["To be or not."], ["Line two."]], name="_Blockquotes"),
            ]
        )
    )
    back = import_xlsx(raw, "legacy.xlsx")
    codes = [n for n in back.nodes if isinstance(n, CodeBlock)]
    assert len(codes) == 1 and codes[0].lang == "js"
    bqs = [n for n in back.nodes if isinstance(n, Blockquote)]
    assert len(bqs) == 2
    assert bqs[0].paragraphs == ["To be or not."]
    assert bqs[1].paragraphs == ["Line two."]


def test_xlsx_legacy_meta_sheets_import():
    """Backward compat: pre-v1.18 split meta sheets still import."""
    from aduport.core.document_model import ContentControl

    raw = export_xlsx(
        DocumentModel(
            nodes=[
                Table(
                    rows=[["change", "author", "anchor", "text"], ["insert", "u", "", "legacy-rev"]],
                    name="_Revisions",
                ),
                Table(
                    rows=[["tag", "alias", "control_type", "text"], ["t", "A", "plain", "legacy-ctrl"]],
                    name="_Controls",
                ),
            ]
        )
    )
    back = import_xlsx(raw, "legacy.xlsx")
    assert any(isinstance(n, Revision) and "legacy-rev" in n.text for n in back.nodes)
    assert any(isinstance(n, ContentControl) and n.text == "legacy-ctrl" for n in back.nodes)


def test_xlsx_table_cell_span_ast_roundtrip():
    model = DocumentModel(
        nodes=[
            Table(
                rows=[["Merged", ""], ["A", "B"]],
                cells=[
                    [
                        TableCell(text="Merged", col_span=2),
                        TableCell(covered=True, origin_row=0, origin_col=0),
                    ],
                    [TableCell(text="A"), TableCell(text="B")],
                ],
                name="SpanGrid",
            ),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    meta = back.metadata or {}
    assert meta.get("table_cells_imported") == 4
    span_tbl = next(n for n in back.nodes if isinstance(n, Table) and n.name == "SpanGrid")
    assert span_tbl.cells[0][0].col_span == 2
    assert span_tbl.cells[0][1].covered is True
    assert span_tbl.cells[1][0].text == "A"
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        ast = ""
        for path in sorted(zf.namelist()):
            if path.startswith("xl/worksheets/sheet") and "table_cell" in zf.read(path).decode():
                ast = zf.read(path).decode()
                break
    assert "table_cell" in ast and "SpanGrid" in ast


def test_xlsx_legacy_cell_spans_sheet_import():
    raw = export_xlsx(
        DocumentModel(
            nodes=[
                Table(
                    rows=[
                        [
                            "table",
                            "row",
                            "col",
                            "text",
                            "row_span",
                            "col_span",
                            "covered",
                            "origin_row",
                            "origin_col",
                            "formula",
                            "cell_kind",
                        ],
                        ["LegacySpan", "0", "0", "Wide", "1", "2", "0", "0", "0", "", "value"],
                        ["LegacySpan", "0", "1", "", "1", "1", "1", "0", "0", "", "value"],
                    ],
                    name="_CellSpans",
                ),
            ]
        )
    )
    back = import_xlsx(raw, "legacy.xlsx")
    span_tbl = next(n for n in back.nodes if isinstance(n, Table) and n.name == "LegacySpan")
    assert span_tbl.cells[0][0].col_span == 2
    assert span_tbl.cells[0][1].covered is True


def test_xlsx_textbox_roundtrip():
    from aduport.core.document_model import TextBox

    model = DocumentModel(
        nodes=[
            Table(rows=[["data"]], name="Data"),
            TextBox(
                text="floating note",
                source="pptx:slide2",
                x_mm=10.5,
                y_mm=20.0,
                width_mm=50.0,
                height_mm=12.0,
            ),
        ]
    )
    raw = export_xlsx(model)
    back = import_xlsx(raw, "out.xlsx")
    boxes = [n for n in back.nodes if isinstance(n, TextBox)]
    assert len(boxes) == 1
    assert boxes[0].text == "floating note"
    assert boxes[0].source == "pptx:slide2"
    assert boxes[0].x_mm == 10.5
    assert boxes[0].width_mm == 50.0
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        wb = zf.read("xl/workbook.xml").decode()
    assert "_AST" in wb
