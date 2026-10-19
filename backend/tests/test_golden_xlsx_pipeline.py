"""Golden pipeline: xlsx export → import → md/docx/html/pdf/txt/epub/pptx/odt/json via convert()."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO

import pytest

from aduport.config import settings
from aduport.core.converter import convert
from aduport.exporters.xlsx_exporter import export_xlsx
from tests.golden_xlsx_fixture import golden_xlsx_model


@pytest.fixture(autouse=True)
def _enable_xlsx_ast_for_golden(monkeypatch: pytest.MonkeyPatch):
    """Meta nodes (comment/revision/…) require _AST sheet; default off is Content-only."""
    monkeypatch.setattr(settings, "xlsx_ast_sheet", True)


def _export_bytes() -> bytes:
    return export_xlsx(golden_xlsx_model())


def _epub_spine_text(data: bytes) -> str:
    if data[:2] != b"PK":
        return ""
    parts: list[str] = []
    with zipfile.ZipFile(BytesIO(data)) as zf:
        for name in zf.namelist():
            lower = name.lower()
            if not lower.endswith((".xhtml", ".html", ".htm")):
                continue
            if "nav.xhtml" in lower or "/nav." in lower:
                continue
            parts.append(zf.read(name).decode("utf-8", errors="replace"))
    return "\n".join(parts)


def test_xlsx_to_md_golden_pipeline():
    md = convert(_export_bytes(), "xlsx", "md", filename="golden.xlsx").decode()

    assert "Region" in md and "East" in md and "100" in md
    assert "Merged" in md
    assert 'colspan="2"' in md
    assert "## Regional Summary" in md
    assert "Totals by region" in md
    assert "- Bullet item" in md
    assert "1. Numbered item" in md
    assert "---" in md
    assert "```python" in md and 'print("xlsx-golden")' in md
    assert "> Quoted wisdom." in md
    assert "<!-- comment:reviewer" in md and "check totals" in md
    assert "[^1]: See appendix" in md
    assert "++new clause++" in md and "ins:editor" in md
    assert "Status" in md and "content_control:dropdown" in md
    assert "Sidebar note" in md and "docx:txbx" in md
    assert "### Pivot: SalesPivot" in md


def test_xlsx_to_html_golden_pipeline():
    html = convert(_export_bytes(), "xlsx", "html", filename="golden.xlsx").decode()

    assert "Region" in html and "East" in html and "Merged" in html
    assert "<h2" in html and "Regional Summary" in html
    assert "<p>Totals by region" in html
    assert "<ul>" in html and "Bullet item" in html
    assert "<ol>" in html and "Numbered item" in html
    assert "<hr" in html
    assert "<pre>" in html and "xlsx-golden" in html
    assert "<blockquote>" in html and "Quoted wisdom." in html
    assert "comment" in html and "check totals" in html
    assert "See appendix" in html and "footnote" in html.lower()
    assert "SalesPivot" in html
    assert 'colspan="2"' in html


def test_xlsx_to_docx_golden_pipeline():
    raw = convert(_export_bytes(), "xlsx", "docx", filename="golden.xlsx")
    assert raw[:2] == b"PK"

    with zipfile.ZipFile(BytesIO(raw)) as zf:
        xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        fn_xml = zf.read("word/footnotes.xml").decode("utf-8", errors="replace")

    assert "Region" in xml and "East" in xml and "Merged" in xml
    assert "Regional Summary" in xml
    assert "Totals by region" in xml
    assert "Bullet item" in xml
    assert "Numbered item" in xml
    assert 'print("xlsx-golden")' in xml
    assert "Quoted wisdom." in xml
    assert "SalesPivot" in xml
    assert "See appendix" in fn_xml
    assert "footnoteReference" in xml


def test_xlsx_to_txt_golden_pipeline():
    txt = convert(_export_bytes(), "xlsx", "txt", filename="golden.xlsx").decode()

    assert "Region" in txt and "East" in txt and "Merged" in txt
    assert "Regional Summary" in txt
    assert "Bullet item" in txt
    assert "xlsx-golden" in txt
    assert "Quoted wisdom." in txt
    assert "check totals" in txt
    assert "See appendix" in txt
    assert "SalesPivot" in txt


def test_xlsx_to_epub_golden_pipeline():
    raw = convert(_export_bytes(), "xlsx", "epub", filename="golden.xlsx")
    assert raw[:2] == b"PK"
    body = _epub_spine_text(raw)
    assert body
    assert "Region" in body and "Regional Summary" in body
    assert "Bullet item" in body
    assert "xlsx-golden" in body
    assert "Quoted wisdom." in body
    assert "See appendix" in body
    assert "SalesPivot" in body


def test_xlsx_to_pdf_golden_pipeline():
    raw = convert(_export_bytes(), "xlsx", "pdf", filename="golden.xlsx")
    assert raw[:4] == b"%PDF"
    assert len(raw) > 512
    # Font-subset PDFs may not embed plaintext literals; structural smoke only.
    assert b"/Type" in raw and b"stream" in raw


def test_xlsx_to_rtf_golden_pipeline():
    raw = convert(_export_bytes(), "xlsx", "rtf", filename="golden.xlsx")
    text = raw.decode("utf-8", errors="replace")
    assert text.startswith("{\\rtf")
    assert "Region" in text and "Regional Summary" in text
    assert "Bullet item" in text and "Merged" in text


def test_xlsx_to_latex_golden_pipeline():
    tex = convert(_export_bytes(), "xlsx", "latex", filename="golden.xlsx").decode()

    assert r"\documentclass" in tex
    assert "Region" in tex and "Regional Summary" in tex
    assert "xlsx-golden" in tex
    assert "See appendix" in tex
    assert "SalesPivot" in tex


def test_xlsx_to_csv_golden_pipeline():
    csv_text = convert(_export_bytes(), "xlsx", "csv", filename="golden.xlsx").decode()

    assert "Region" in csv_text and "Sales" in csv_text
    assert "East" in csv_text and "100" in csv_text
    assert "Merged" in csv_text


def test_xlsx_to_pptx_golden_pipeline():
    raw = convert(_export_bytes(), "xlsx", "pptx", filename="golden.xlsx")
    assert raw[:2] == b"PK"

    with zipfile.ZipFile(BytesIO(raw)) as zf:
        slide_names = sorted(
            n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")
        )
        assert len(slide_names) >= 4
        blob = "".join(zf.read(name).decode("utf-8", errors="replace") for name in slide_names)

    assert "check totals" in blob
    assert "See appendix" in blob
    assert "Track changes" in blob and "new clause" in blob
    assert "Content controls" in blob and "Status" in blob
    assert "Text boxes" in blob and "Sidebar note" in blob
    assert "Pivot tables" in blob and "SalesPivot" in blob
    assert "Merged" in blob
    assert "gridSpan" in blob


def test_xlsx_to_odt_golden_pipeline():
    raw = convert(_export_bytes(), "xlsx", "odt", filename="golden.xlsx")
    assert raw[:2] == b"PK"

    with zipfile.ZipFile(BytesIO(raw)) as zf:
        content = zf.read("content.xml").decode("utf-8", errors="replace")

    assert "Region" in content and "East" in content and "Merged" in content
    assert 'table:number-columns-spanned="2"' in content
    assert "Regional Summary" in content
    assert "SalesPivot" in content
    assert "check totals" in content


def test_xlsx_to_json_golden_pipeline():
    text = convert(_export_bytes(), "xlsx", "json", filename="golden.xlsx").decode()
    data = json.loads(text)

    assert data.get("schema_version") in ("1.0", "1.1", "1.2")
    blocks = data.get("blocks") or []
    types = {b.get("type") for b in blocks}
    assert "table" in types
    assert "heading" in types
    assert "pivot_table" in types
    assert "comment" in types
    assert "revision" in types
    assert "content_control" in types
    assert "text_box" in types

    span_tbl = next(b for b in blocks if b.get("type") == "table" and b.get("name") == "SpanGrid")
    cells = span_tbl.get("cells") or []
    assert cells[0][0].get("col_span") == 2
