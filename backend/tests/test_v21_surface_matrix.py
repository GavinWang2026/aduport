"""Follow-up surface gate and the cues that the full 35×5×2 matrix still does not cover.

This file locks a sample of endpoints and the second-round fidelity fixes.
It is not an exhaustive matrix of every route, input class, and isolate mode.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from aduport.config import settings
from aduport.core.converter import convert, ensure_builtin_plugins
from aduport.core.document_model import (
    Blockquote,
    Chart,
    DocumentModel,
    Footnote,
    Heading,
    InlineMark,
    InlineMarkType,
    ListItem,
    Paragraph,
    RichText,
)
from aduport.core.exceptions import ERROR_CODES
from aduport.core.registry import get_exporter, get_importer
from aduport.exporters.html_render import _pdf_markdown_appendix
from aduport.exporters.rag_elements import export_rag_elements
from main import aduport


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "api_key", "")
    monkeypatch.setattr(settings, "basic_auth", "")
    monkeypatch.setattr(settings, "rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "audit_enabled", False)
    monkeypatch.setattr(settings, "require_auth", False)
    return TestClient(app)


def _code(response) -> str:
    body = response.json()
    return str(body.get("code") or "")


def test_health_formats_and_isolate_snapshot(client):
    health = client.get("/api/health")
    formats = client.get("/api/formats")
    detail = client.get("/api/health/detail")
    assert health.status_code == 200
    assert formats.status_code == 200
    assert detail.status_code == 200, detail.text[:300]
    isolate = detail.json()["isolate"]
    assert set(isolate) >= {"enabled", "workers", "started"}
    assert isinstance(isolate["enabled"], bool)
    assert isinstance(isolate["workers"], int)
    assert isinstance(isolate["started"], bool)


def test_convert_markdown_to_html(client):
    response = client.post(
        "/api/convert",
        files={"file": ("a.md", b"# Hi\n\nHello.\n", "text/markdown")},
        data={"source_format": "md", "target_format": "html"},
    )
    assert response.status_code == 200, response.text[:300]
    assert b"<" in response.content and b"Hello" in response.content


def test_empty_and_garbage_and_merge_and_missing_job_use_catalog(client):
    empty = client.post(
        "/api/convert",
        files={"file": ("empty.md", b"", "text/markdown")},
        data={"source_format": "md", "target_format": "html"},
    )
    assert empty.status_code == ERROR_CODES["empty_file"]
    assert _code(empty) == "empty_file"

    garbage = client.post(
        "/api/convert",
        files={"file": ("bad.docx", b"not-a-zip", "application/octet-stream")},
        data={"source_format": "docx", "target_format": "md"},
    )
    assert _code(garbage) in ERROR_CODES
    assert garbage.status_code == ERROR_CODES[_code(garbage)]

    merge = client.post(
        "/api/pdf/merge",
        files=[("files", ("only.pdf", b"%PDF-1.4\n", "application/pdf"))],
    )
    assert _code(merge) == "pdf_merge_input"
    assert merge.status_code == ERROR_CODES["pdf_merge_input"]

    missing = client.get("/api/jobs/does-not-exist")
    assert _code(missing) == "job_not_found"
    assert missing.status_code == ERROR_CODES["job_not_found"]


def test_diff_two_markdown_files(client):
    diff = client.post(
        "/api/diff",
        files={
            "left": ("a.md", b"# A\n\nleft\n", "text/markdown"),
            "right": ("b.md", b"# A\n\nright\n", "text/markdown"),
        },
    )
    assert diff.status_code == 200, diff.text[:300]
    assert diff.json()["equal"] is False


def test_pdf_appendix_keeps_marks_lists_tasks_quotes_footnotes():
    html = _pdf_markdown_appendix(
        [
            Heading(level=1, text="Cue"),
            Paragraph(
                text=RichText(
                    text="Hello bold end",
                    marks=[InlineMark(type=InlineMarkType.BOLD, start=6, end=10)],
                )
            ),
            ListItem(text="outer", level=0),
            ListItem(text="inner", level=1),
            ListItem(text="done", checked=True),
            Blockquote(paragraphs=["quoted line"]),
            Footnote(id="n", text="note body"),
        ]
    )
    assert "aduport-cues" in html
    assert "page-break-before" not in html
    assert "ADUPORT-CUES-v1" in html
    assert "**bold**" in html
    assert "- outer" in html
    assert "  - inner" in html
    assert "- [x] done" in html
    assert "quoted line" in html
    assert "[^n]: note body" in html


def test_pdf_text_layer_roundtrip_reads_cue_page():
    src = (
        "# Cue\n\n"
        "Hello **bold** end.\n\n"
        "- outer\n"
        "  - inner\n\n"
        "- [x] done\n\n"
        "> quoted line\n\n"
        "A sentence.[^n]\n\n"
        "[^n]: note body\n"
    ).encode()
    pdf = convert(src, "md", "pdf", filename="cues.md", cache_bypass=True)
    assert pdf[:5] == b"%PDF-"
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf)
    try:
        assert len(doc) == 1
    finally:
        doc.close()
    from pypdf import PdfReader
    from io import BytesIO

    attached = PdfReader(BytesIO(pdf)).attachments.get("aduport-cues.md") or []
    assert any(b"ADUPORT-CUES-v1" in blob for blob in attached)
    back = convert(
        pdf,
        "pdf",
        "md",
        filename="cues.pdf",
        skip_pdf_sidecar=True,
        pdf_engine="text",
        cache_bypass=True,
    )
    text = back.decode("utf-8", errors="ignore")
    assert "**bold**" in text
    assert "  - inner" in text or "- inner" in text
    assert "[x]" in text
    assert "quoted line" in text
    assert "[^n]:" in text and "note body" in text
    # The visual page and the cue page used to both survive, so these phrases
    # appeared twice. The cue page replaces the matching visual lines.
    assert text.count("Hello") == 1
    assert text.count("quoted line") == 1
    assert text.count("note body") == 1
    assert text.count("inner") == 1
    assert "Footnotes" not in text


def _as_model(loaded):
    return loaded[0] if isinstance(loaded, tuple) else loaded


def test_runstyle_is_before_the_first_paragraph():
    ensure_builtin_plugins()
    model = DocumentModel(nodes=[Paragraph(text=RichText(text="BODYTAIL"))])
    blob = get_exporter("docx")(model)
    loaded = _as_model(get_importer("docx")(blob, filename="styles.docx"))
    names = [type(node).__name__ for node in loaded.nodes]
    paragraphs = [
        i
        for i, node in enumerate(loaded.nodes)
        if type(node).__name__ == "Paragraph" and "BODYTAIL" in node.plain_text()
    ]
    styles = [i for i, name in enumerate(names) if name == "RunStyle"]
    assert styles and paragraphs
    assert styles[0] < paragraphs[0]


def test_chart_inserts_at_a_body_child_index():
    ensure_builtin_plugins()
    model = DocumentModel(
        nodes=[
            Paragraph(text=RichText(text="BEFORE")),
            Chart(
                title="C1",
                chart_type="bar",
                categories=["c"],
                series=[{"name": "S", "values": [1]}],
            ),
            Paragraph(text=RichText(text="AFTER")),
        ]
    )
    blob = get_exporter("docx")(model)
    loaded = _as_model(get_importer("docx")(blob, filename="chart.docx"))
    names = [type(node).__name__ for node in loaded.nodes]
    chart_at = names.index("Chart")
    after_at = next(
        i
        for i, node in enumerate(loaded.nodes)
        if type(node).__name__ == "Paragraph" and "AFTER" in node.plain_text()
    )
    assert chart_at < after_at
    assert any(
        type(node).__name__ == "Paragraph" and "BEFORE" in node.plain_text()
        for node in loaded.nodes[:chart_at]
    )


def test_rag_elements_carry_confidence():
    plain = export_rag_elements(
        DocumentModel(nodes=[Heading(level=1, text="T"), Paragraph(text="body")])
    )
    assert [item["metadata"]["confidence"] for item in plain] == [0.92, 0.80]
    low = export_rag_elements(
        DocumentModel(
            nodes=[Paragraph(text="flat")],
            metadata={"structure_fidelity": "below_text"},
        )
    )
    assert low[0]["metadata"]["confidence"] == 0.55
    assert export_rag_elements(
        DocumentModel(nodes=[Paragraph(text="x")])
    )[0]["metadata"]["confidence"] == 0.80


def test_isolate_status_does_not_start_the_pool():
    from aduport.core.convert_isolate import isolate_pool_status

    before = isolate_pool_status()
    again = isolate_pool_status()
    assert again["started"] is before["started"]
    assert "enabled" in again and "workers" in again


def test_cue_page_replaces_the_visual_duplicate():
    from aduport.core.document_model import Heading
    from aduport.importers.pdf_text_fallback import _fold_cue_nodes

    visual = [
        Heading(level=1, text="Cue"),
        Paragraph(text="Hello bold end"),
        Paragraph(text="outer"),
        Paragraph(text="quoted line"),
        Heading(level=2, text="Footnotes"),
        Paragraph(text="note body"),
    ]
    cues = [
        Paragraph(
            text=RichText(
                text="Hello bold end",
                marks=[InlineMark(type=InlineMarkType.BOLD, start=6, end=10)],
            )
        ),
        ListItem(text="outer"),
        Blockquote(paragraphs=["quoted line"]),
        Footnote(id="n", text="note body"),
    ]
    folded = _fold_cue_nodes(visual, cues)
    assert [type(node).__name__ for node in folded] == [
        "Heading",
        "Paragraph",
        "ListItem",
        "Blockquote",
        "Footnote",
    ]
    assert folded[1].plain_text() == "Hello bold end"
    assert folded[1].text.marks


def test_same_font_size_does_not_promote_descenders():
    """Ink-box height follows gjpq. The decision uses font size."""
    from aduport.importers.pdf_text_fallback import nodes_from_layout_blocks

    prose = "body paragraph with gjpq descenders " + ("y" * 20)
    nodes = nodes_from_layout_blocks(
        [
            {
                "page": 1,
                "left": 72,
                "font_size": 12,
                "height": 9.08,
                "text": "TITLE ALL CAPS",
                "columns": [],
            },
            {
                "page": 1,
                "left": 72,
                "font_size": 12,
                "height": 11.20,
                "text": prose,
                "columns": [],
            },
            {
                "page": 1,
                "left": 72,
                "font_size": 12,
                "height": 11.0,
                "text": "Second prose line is long enough to anchor the body size here.",
                "columns": [],
            },
            {
                "page": 1,
                "left": 72,
                "font_size": 12,
                "height": 11.0,
                "text": "Third prose line keeps the median on the font, not the ink.",
                "columns": [],
            },
            {
                "page": 1,
                "left": 72,
                "font_size": 22,
                "height": 9.0,
                "text": "Real Title",
                "columns": [],
            },
        ]
    )
    headings = [node.plain_text() for node in nodes if type(node).__name__ == "Heading"]
    assert headings == ["Real Title"]
    assert all(type(node).__name__ != "Blockquote" for node in nodes)


def test_layout_blocks_promote_heading_table_and_quote():
    from aduport.importers.pdf_text_fallback import nodes_from_layout_blocks

    blocks = [
        {"page": 1, "left": 72, "top": 740, "bottom": 718, "height": 22, "text": "Quarterly Review", "columns": []},
        {
            "page": 1,
            "left": 72,
            "top": 700,
            "bottom": 688,
            "height": 12,
            "text": "This body line is long enough to stay a paragraph in the text layer.",
            "columns": [],
        },
        {
            "page": 1,
            "left": 120,
            "top": 660,
            "bottom": 648,
            "height": 12,
            "text": "An indented remark from the margin.",
            "columns": [],
        },
        {"page": 1, "left": 72, "top": 620, "bottom": 608, "height": 12, "text": "Name Value", "columns": ["Name", "Value"]},
        {"page": 1, "left": 72, "top": 600, "bottom": 588, "height": 12, "text": "Ada Lovelace", "columns": ["Ada", "Lovelace"]},
    ]
    nodes = nodes_from_layout_blocks(blocks)
    assert [type(node).__name__ for node in nodes] == [
        "Heading",
        "Paragraph",
        "Blockquote",
        "Table",
    ]
    assert nodes[0].level == 1
    assert nodes[0].plain_text() == "Quarterly Review"
    assert nodes[2].paragraphs
    assert nodes[3].rows == [["Name", "Value"], ["Ada", "Lovelace"]]
    flat = nodes_from_layout_blocks(
        [
            {"page": 1, "left": 72, "height": 12, "text": "2024. Annual report", "columns": []},
            {"page": 1, "left": 72, "height": 12, "text": "Body stays a paragraph here too.", "columns": []},
        ]
    )
    assert [type(node).__name__ for node in flat] == ["Paragraph", "Paragraph"]


def test_third_party_pdf_text_layer_keeps_heading_table_and_quote():
    """A digital PDF that is not Markdown still yields those three nodes."""
    from io import BytesIO

    from aduport.importers.pdf_text_fallback import extract_pdf_text_model

    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab is required to build the third-party PDF fixture")
    buf = BytesIO()
    pdf = canvas.Canvas(buf, pagesize=letter)
    pdf.setFont("Helvetica", 22)
    pdf.drawString(72, 740, "Quarterly Review")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, 700, "This body line is long enough to stay a paragraph.")
    pdf.drawString(120, 660, "An indented remark from the margin.")
    pdf.drawString(72, 620, "Name")
    pdf.drawString(240, 620, "Value")
    pdf.drawString(72, 600, "Ada")
    pdf.drawString(240, 600, "Lovelace")
    pdf.save()
    model = extract_pdf_text_model(buf.getvalue(), "third.pdf")
    kinds = [type(node).__name__ for node in model.nodes]
    assert "Heading" in kinds
    assert "Table" in kinds
    assert "Blockquote" in kinds
    assert model.metadata["structure_fidelity"] == "below_text"
    assert model.metadata["pdf_text_structure"] == "layout"
    joined = " ".join(
        node.plain_text() if hasattr(node, "plain_text") else ""
        for node in model.nodes
    )
    assert "Quarterly Review" in joined
    assert "Ada" in joined or any(
        "Ada" in " ".join(row) for node in model.nodes if type(node).__name__ == "Table" for row in node.rows
    )


def test_visual_lines_promote_bullets_and_decimals():
    from aduport.importers.pdf_text_fallback import _node_from_visual_text, _page_to_paragraphs

    bullet = _node_from_visual_text("• nested item")
    numbered = _node_from_visual_text("12) second item")
    glued = _node_from_visual_text("1.First numbered")
    alpha = _node_from_visual_text("a)Alpha sub one")
    roman = _node_from_visual_text("i.Roman sub sub")
    glued_bullet = _node_from_visual_text("•Bullet one")
    year = _node_from_visual_text("2024. Annual report")
    sentence = _node_from_visual_text("Note. This is a sentence.")
    decimal = _node_from_visual_text("1.2 million")
    assert type(bullet).__name__ == "ListItem" and bullet.plain_text() == "nested item"
    assert type(glued).__name__ == "ListItem" and glued.plain_text() == "First numbered"
    assert glued.ordered is True
    assert type(alpha).__name__ == "ListItem" and alpha.plain_text() == "Alpha sub one"
    assert type(roman).__name__ == "ListItem" and roman.plain_text() == "Roman sub sub"
    assert type(glued_bullet).__name__ == "ListItem" and glued_bullet.plain_text() == "Bullet one"
    assert type(sentence).__name__ == "Paragraph"
    assert type(decimal).__name__ == "Paragraph"
    assert bullet.ordered is False
    assert type(numbered).__name__ == "ListItem" and numbered.ordered is True
    assert numbered.plain_text() == "second item"
    assert type(year).__name__ == "Paragraph"
    assert _page_to_paragraphs("• alpha item\n1. beta item\nJust a sentence") == [
        "• alpha item",
        "1. beta item",
        "Just a sentence",
    ]


def test_chart_slot_skips_an_empty_paragraph_the_importer_drops():
    """A raw w:body index counts the empty paragraph; the importer does not."""
    ensure_builtin_plugins()
    model = DocumentModel(
        nodes=[
            Paragraph(text=RichText(text="BEFORE")),
            Chart(
                title="C1",
                chart_type="bar",
                categories=["c"],
                series=[{"name": "S", "values": [1]}],
            ),
            Paragraph(text=RichText(text="AFTER")),
        ]
    )
    blob = get_exporter("docx")(model)
    with zipfile.ZipFile(io.BytesIO(blob)) as zin:
        files = {name: zin.read(name) for name in zin.namelist()}
    xml = files["word/document.xml"].decode("utf-8")
    xml = xml.replace("ADUPORT-CHART-1", "")
    chart_at = xml.find("<c:chart")
    assert chart_at > 0
    start = xml.rfind("<w:p", 0, chart_at)
    xml = xml[:start] + "<w:p/>" + xml[start:]
    files["word/document.xml"] = xml.encode("utf-8")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zout:
        for name, data in files.items():
            zout.writestr(name, data)
    loaded = _as_model(get_importer("docx")(out.getvalue(), filename="gap.docx"))
    names = [type(node).__name__ for node in loaded.nodes]
    chart_at = names.index("Chart")
    after_at = next(
        i
        for i, node in enumerate(loaded.nodes)
        if type(node).__name__ == "Paragraph" and "AFTER" in node.plain_text()
    )
    before_at = next(
        i
        for i, node in enumerate(loaded.nodes)
        if type(node).__name__ == "Paragraph" and "BEFORE" in node.plain_text()
    )
    assert before_at < chart_at < after_at
    assert "ADUPORT-CHART" not in " ".join(
        node.plain_text() for node in loaded.nodes if hasattr(node, "plain_text")
    )

