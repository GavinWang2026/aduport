"""v19 depth gates: object counts stay stable, and silent drops become visible."""

from __future__ import annotations

import io
import zipfile
from collections import Counter

from aduport.core.converter import convert, ensure_builtin_plugins, get_exporter
from aduport.core.document_model import (
    Chart,
    DocumentModel,
    FieldCode,
    Heading,
    ListItem,
    Paragraph,
    RichText,
    Section,
)
from aduport.core.registry import get_importer
from aduport.exporters.export_warnings import begin_export_warnings, take_export_warnings


def _counts(model: DocumentModel) -> Counter:
    return Counter(type(n).__name__ for n in model.nodes)


def test_docx_chart_and_section_do_not_double():
    ensure_builtin_plugins()
    model = DocumentModel(
        nodes=[
            Paragraph(text=RichText(text="P")),
            Chart(
                title="C1",
                chart_type="bar",
                categories=["c"],
                series=[{"name": "S", "values": [1]}],
            ),
        ]
    )
    blob = get_exporter("docx")(model)
    first = None
    for _ in range(3):
        loaded = get_importer("docx")(blob)
        model = loaded[0] if isinstance(loaded, tuple) else loaded
        counts = _counts(model)
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            parts = [
                n
                for n in zf.namelist()
                if n.startswith("word/charts/chart") and n.endswith(".xml")
            ]
        assert len(parts) == 1
        assert counts["Chart"] == 1
        assert counts["Section"] == 1
        if first is None:
            first = counts
        else:
            assert counts["Chart"] == first["Chart"]
            assert counts["Section"] == first["Section"]
        blob = get_exporter("docx")(model)


def test_headingless_roundtrip_adds_no_heading():
    for target, name in (("html", "myfile.html"), ("epub", "myfile.epub"), ("docx", "myfile.docx")):
        out = convert(
            convert(b"just a paragraph", "md", target, filename="myfile.md"),
            target,
            "md",
            filename=name,
        )
        text = out.decode()
        assert not text.lstrip().startswith("#")
        assert "just a paragraph" in text


def test_html_keeps_five_list_levels():
    src = "- L0\n  - L1\n    - L2\n      - L3\n        - L4\n"
    text = convert(
        convert(src.encode(), "md", "html", filename="a.md"),
        "html",
        "md",
        filename="a.html",
    ).decode()
    assert "# a" not in text
    indents = []
    for line in text.splitlines():
        if "L" in line and line.strip().startswith("-"):
            indents.append(len(line) - len(line.lstrip(" ")))
    assert indents == [0, 2, 4, 6, 8]


def test_pptx_chart_roundtrip_warns_and_skips_scaffold():
    ensure_builtin_plugins()
    model = DocumentModel(
        nodes=[
            Paragraph(text=RichText(text="P")),
            Chart(
                title="C1",
                chart_type="bar",
                categories=["c"],
                series=[{"name": "S", "values": [1]}],
            ),
        ]
    )
    blob = get_exporter("pptx")(model)
    loaded = get_importer("pptx")(blob)
    back = loaded[0] if isinstance(loaded, tuple) else loaded
    md = get_exporter("md")(back).decode()
    assert "Document" not in md
    assert "Slide 1" not in md
    assert "图表在正文里降级为表格" in md
    assert any(isinstance(n, Chart) for n in back.nodes)


def test_txt_keeps_heading_level_three():
    model = DocumentModel(
        nodes=[Heading(level=1, text="H1SENT"), Heading(level=3, text="H3SENT")]
    )
    text = get_exporter("txt")(model).decode()
    assert "H1SENT" in text
    assert "### H3SENT" in text
    assert "H3SENT\n------" not in text


def test_rtf_ordered_marker_keeps_the_number():
    text = convert(
        convert(b"1. OLSENT\n", "md", "rtf", filename="l.md"),
        "rtf",
        "md",
        filename="l.rtf",
    ).decode()
    assert "1. OLSENT" in text or "1\\. OLSENT" in text
    assert ". OLSENT" not in text.replace("1. OLSENT", "").replace("1\\. OLSENT", "")


def test_markdown_warns_when_word_chrome_is_skipped():
    model = DocumentModel(
        nodes=[
            Paragraph(text=RichText(text="P")),
            Section(title="Body", props={"orientation": "portrait"}),
            FieldCode(field_type="PAGE", instruction="PAGE", result_text="1"),
        ]
    )
    token = begin_export_warnings()
    text = get_exporter("md")(model)
    warnings = take_export_warnings(token)
    assert "section" in text.decode().lower() or any("section" in w for w in warnings)
    assert any("field" in w for w in warnings)
