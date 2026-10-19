"""Gates from 格式裁剪决策-复评v2, section 08.

The 741-cell corpus is not in the repo. ``format_matrix.py --check`` runs the
bounded probes (content, non-inherent structure, truncated OLE) and the
evaluator that CI applies to a saved matrix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aduport.config import settings
from aduport.core.exceptions import CorruptedFileError
from aduport.importers import ocr_engine as oe
from aduport.importers.image_importer import import_image


def _matrix():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "outputs" / "format_matrix.py"
    spec = importlib.util.spec_from_file_location("format_matrix_mod", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_find_spec_does_not_mark_mineru_ready(monkeypatch):
    monkeypatch.setattr(oe, "_module_spec_exists", lambda _name: True)
    monkeypatch.setattr(oe.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(oe, "_cached_probe", lambda *_args, **_kwargs: False)
    assert oe.MinerUEngine().available() is False


def test_mineru_probe_imports_the_pipeline(monkeypatch):
    seen: list[str] = []

    class _Proc:
        returncode = 1
        stderr = b"No module named 'six'"
        stdout = b""

    def _run(cmd, **_kwargs):
        seen.append(cmd[2] if len(cmd) > 2 else "")
        return _Proc()

    monkeypatch.setattr(oe, "_module_spec_exists", lambda _name: True)
    monkeypatch.setattr(oe.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("subprocess.run", _run)
    oe.clear_ocr_probe_cache()
    assert oe.MinerUEngine().available() is False
    assert seen
    assert "pipeline_analyze" in seen[0]
    assert "find_spec" not in seen[0]


def test_doctor_enabled_ocr_is_not_ready_when_probe_fails(monkeypatch):
    from typer.testing import CliRunner

    from aduport.cli import app

    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(settings, "license_required", False)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(oe.MinerUEngine, "available", lambda self: False)
    monkeypatch.setattr(oe.PaddleOcrVlEngine, "available", lambda self: False)
    monkeypatch.setattr(oe.DoclingOcrEngine, "available", lambda self: False)
    result = CliRunner().invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    assert payload["ocr_ready"] is False
    ocr = next(item for item in payload["checks"] if item["name"] == "ocr")
    assert ocr["ready"] is False
    assert "mineru" not in ocr["detail"]


def test_image_says_ocr_unavailable_when_enabled_but_unusable(monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(oe.MinerUEngine, "available", lambda self: False)
    monkeypatch.setattr(oe.PaddleOcrVlEngine, "available", lambda self: False)
    monkeypatch.setattr(oe.DoclingOcrEngine, "available", lambda self: False)
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf"
        b"\xc0\x00\x00\x00\x03\x00\x01\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00"
        b"\x00\x00IEND\xaeB`\x82"
    )
    model = import_image(png, "dot.png")
    text = " ".join(node.plain_text() for node in model.nodes if hasattr(node, "plain_text"))
    assert "OCR 不可用，仅嵌入图片" in text
    assert any(node.type == "image" for node in model.nodes)
    from aduport.exporters.csv_exporter import export_csv
    from aduport.exporters.txt_exporter import export_txt

    for rendered in (export_txt(model), export_csv(model)):
        assert "仅嵌入图片" not in rendered
        assert "OCR 不可用，图片以占位符保留" in rendered
        assert "[Image:" in rendered


def test_truncated_doc_errors_and_hides_directory_names():
    fixture = (
        Path(__file__).resolve().parents[2]
        / "native/anydoc/tests/fixtures/doc/text.doc"
    )
    if not fixture.is_file():
        pytest.skip("text.doc fixture missing")
    from aduport.importers.doc_importer import import_doc

    raw = fixture.read_bytes()
    intact = import_doc(raw, "text.doc")
    body = " ".join(node.plain_text() for node in intact.nodes if hasattr(node, "plain_text"))
    assert "Root Entry" not in body
    assert "WordDocument" not in body
    with pytest.raises(CorruptedFileError, match="文件被截断或不完整"):
        import_doc(raw[: len(raw) // 2], "s.doc")


def test_check_cells_whitelist_and_bars():
    matrix = _matrix()
    assert matrix.check_cells({"md->odp": {"l3": False, "l4": True}})
    assert matrix.check_cells({"pages->docx": {"l3": False, "l4": False}}) == []
    assert matrix.check_cells({"md->rag": {"l3": False, "l4": False}}) == []
    csv_loss = matrix.check_cells(
        {
            "md->odp": {"l3": True, "l4": True},
            "md->csv": {"l3": True, "l4": False},
            "md->txt": {"l3": True, "l4": False},
        }
    )
    assert csv_loss
    assert any("md->csv" in item or "md->txt" in item for item in csv_loss)
    pdf_loss = matrix.check_cells(
        {
            "md->odp": {"l3": True, "l4": True},
            "md->pdf": {"l3": True, "l4": False},
            "md->rtf": {"l3": True, "l4": False},
        }
    )
    assert pdf_loss
    assert any("md->pdf" in item for item in pdf_loss)


def test_inherent_targets_keep_heading_table_and_image():
    from aduport.core.converter import convert
    from aduport.core.document_model import (
        Blockquote,
        CodeBlock,
        DocumentModel,
        Heading,
        Image,
        ListItem,
        Paragraph,
        RichText,
        Table,
    )
    from aduport.core.registry import ensure_builtin_plugins, get_importer
    from aduport.exporters.md_exporter import export_md

    ensure_builtin_plugins()
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT\x08\xd7c\xf8\xcf"
        b"\xc0\x00\x00\x00\x03\x00\x01\x00\x05\xfe\x02\xfe\xdc\xccY\xe7\x00\x00"
        b"\x00\x00IEND\xaeB`\x82"
    )
    model = DocumentModel(
        nodes=[
            Heading(level=1, text="ALPHA"),
            Paragraph(text=RichText(text="BRAVO")),
            ListItem(text=RichText(text="ECHO")),
            Blockquote(paragraphs=[RichText(text="FOXTROT")]),
            CodeBlock(lang="text", content="GOLF"),
            Table(rows=[["区域", "Q1"], ["DELTA", "31415"]]),
            Image(alt_text="dot", data=png, mime_type="image/png"),
        ]
    )
    seed = export_md(model).encode()
    for target in ("rtf", "pdf"):
        out = convert(seed, "md", target, "seed.md", cache_bypass=True)
        back = get_importer(target)(out)
        kinds = {node.type for node in back.nodes}
        assert {"heading", "table", "image"} <= kinds, target
        images = [node for node in back.nodes if node.type == "image"]
        assert images[0].data, target
        if target != "pdf":
            assert images[0].data == png
            assert images[0].alt_text == "dot"
    for target in ("txt", "csv"):
        out = convert(seed, "md", target, "seed.md", cache_bypass=True)
        text = out.decode("utf-8")
        assert "data:image" not in text
        assert "[Image: dot]" in text
        back = get_importer(target)(out)
        kinds = {node.type for node in back.nodes}
        assert {"heading", "table"} <= kinds, target
        assert "image" not in kinds, target


def test_matrix_generator_uses_declared_denominator():
    matrix = _matrix()
    assert "rag" not in matrix.TARGETS
    assert len(matrix.TARGETS) == 18
    for name in ("pages", "numbers", "keynote"):
        assert name not in matrix.IMPORT_ONLY
        assert name in matrix.EXCLUDED_SOURCES
    assert "rag" in matrix.EXCLUDED_TARGETS
    assert matrix.INHERENT_STRUCT_TARGETS == frozenset()
    assert matrix.STRUCT_IMAGE_NA_TARGETS == frozenset({"csv", "txt"})


def test_format_matrix_check_command(capsys):
    matrix = _matrix()
    assert matrix.run_bounded_check() == 0
    banner = capsys.readouterr().out
    assert "(no format whitelist)" in banner
    assert "whitelisted" not in banner


def test_csv_and_txt_keep_the_structure_the_red_boxes_lost():
    """00:05 csv/txt structure misses: prose must not become one table, and a
    one-column txt table must not be read as a setext heading.
    """
    from aduport.core.converter import convert
    from aduport.core.document_model import DocumentModel, Heading, ListItem, Paragraph, RichText, Table
    from aduport.core.registry import ensure_builtin_plugins, get_importer
    from aduport.exporters.md_exporter import export_md

    ensure_builtin_plugins()
    prose = DocumentModel(
        nodes=[
            Heading(level=1, text=RichText(text="标题")),
            ListItem(text=RichText(text="Alpha")),
            Paragraph(text=RichText(text="Beta master body text")),
            Paragraph(text=RichText(text="こんにちは世界。")),
        ]
    )
    csv_out = convert(export_md(prose).encode(), "md", "csv", "prose.md", cache_bypass=True)
    csv_back = get_importer("csv")(csv_out, "prose.csv")
    csv_kinds = {node.type for node in csv_back.nodes}
    assert {"heading", "list_item", "paragraph"} <= csv_kinds
    assert "table" not in csv_kinds
    txt_prose = convert(export_md(prose).encode(), "md", "txt", "prose.md", cache_bypass=True)
    txt_kinds = {node.type for node in get_importer("txt")(txt_prose, "prose.txt").nodes}
    assert {"heading", "list_item", "paragraph"} <= txt_kinds

    grid = convert(b"Name,Value\nAda,1\n", "csv", "csv", "grid.csv", cache_bypass=True)
    grid_back = get_importer("csv")(grid, "grid.csv")
    assert {node.type for node in grid_back.nodes} == {"table"}

    one_col = DocumentModel(
        nodes=[Table(rows=[["Only"], ["cell"]], header_rows=1)]
    )
    txt_out = convert(export_md(one_col).encode(), "md", "txt", "col.md", cache_bypass=True)
    text = txt_out.decode("utf-8")
    assert text.splitlines()[0].startswith("|")
    txt_back = get_importer("txt")(txt_out, "col.txt")
    assert any(node.type == "table" for node in txt_back.nodes)


def test_rtf_negative_unicode_round_trip():
    from aduport.core.converter import convert
    from aduport.core.document_model import DocumentModel, Heading, RichText, Table
    from aduport.core.registry import ensure_builtin_plugins, get_importer
    from aduport.exporters.md_exporter import export_md

    ensure_builtin_plugins()
    model = DocumentModel(
        nodes=[
            Heading(level=1, text=RichText(text="这是标题")),
            Table(rows=[["这", "Q1"], ["区", "31415"]], header_rows=1),
        ]
    )
    raw = convert(export_md(model).encode(), "md", "rtf", "cjk.md", cache_bypass=True)
    assert b"\\u-28711?" in raw
    back = get_importer("rtf")(raw, "cjk.rtf")
    headings = [node.plain_text() for node in back.nodes if node.type == "heading"]
    assert "这是标题" in headings
    tables = [node for node in back.nodes if node.type == "table"]
    assert tables
    blob = " ".join(cell for row in tables[0].rows for cell in row)
    assert "这" in blob
    assert "区" in blob


def test_truncated_docx_pdf_xlsx_and_zip_are_rejected(monkeypatch):
    import io
    import zipfile

    from aduport.core.converter import convert
    from aduport.core.exceptions import InvalidDocumentError
    from aduport.core.registry import ensure_builtin_plugins, get_importer

    monkeypatch.setattr(settings, "pdf_engine", "text")
    ensure_builtin_plugins()
    md = b"# T\n\nHello body paragraph that is long enough.\n"
    docx = convert(md, "md", "docx", "a.md", cache_bypass=True)
    pdf = convert(md, "md", "pdf", "a.md", cache_bypass=True)
    xlsx = convert(md, "md", "xlsx", "a.md", cache_bypass=True)
    with pytest.raises(CorruptedFileError, match="DOCX 文件已损坏"):
        get_importer("docx")(docx[: len(docx) // 2], "a.docx")
    with pytest.raises(CorruptedFileError, match="无法打开 PDF"):
        get_importer("pdf")(pdf[: len(pdf) // 2], "a.pdf")
    import struct

    bad = bytearray(xlsx)
    eocd = bad.rfind(b"PK\x05\x06")
    assert eocd >= 16
    cd_off = struct.unpack_from("<I", bad, eocd + 16)[0]
    assert bad[cd_off : cd_off + 4] == b"PK\x01\x02"
    bad[cd_off : cd_off + 4] = b"\x00\x00\x00\x00"
    with pytest.raises(CorruptedFileError, match="XLSX 损坏"):
        get_importer("xlsx")(bytes(bad), "a.xlsx")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("note.txt", "hello")
    packed = buf.getvalue()
    with pytest.raises(InvalidDocumentError, match="损坏的 ZIP"):
        get_importer("zip")(packed[: len(packed) // 2], "a.zip")


def test_external_pdf_tall_short_lines_become_headings():
    """Third-party text.pdf: taller short lines are headings, aligned rows a table."""
    from aduport.importers.pdf_text_fallback import extract_pdf_text_model

    fixture = (
        Path(__file__).resolve().parents[2]
        / "native/anydoc/tests/fixtures/pdf/text.pdf"
    )
    if not fixture.is_file():
        pytest.skip("text.pdf fixture missing")
    model = extract_pdf_text_model(fixture.read_bytes(), "text.pdf")
    assert model.metadata.get("pdf_cues") != "folded"
    headings = [node.plain_text() for node in model.nodes if node.type == "heading"]
    for title in (
        "Fixture Document",
        "Notes and special text",
        "Objects",
        "Quote and code",
    ):
        assert title in headings, headings
    tables = [node for node in model.nodes if node.type == "table"]
    assert tables
    assert tables[0].cells and tables[0].cells[0][0].text == "Wide head"
    blob = " ".join(cell for row in tables[0].rows for cell in row)
    for word in ("Wide head", "End", "Tall", "B2", "C2", "B3", "C3"):
        assert word in blob, tables[0].rows
    lists = [node.plain_text() for node in model.nodes if node.type == "list_item"]
    for item in ("First numbered", "Alpha sub one", "Roman sub sub", "Bullet one", "Nested bullet"):
        assert item in lists, lists
    quotes = []
    for node in model.nodes:
        if node.type != "blockquote":
            continue
        for para in node.paragraphs:
            quotes.append(para if isinstance(para, str) else getattr(para, "text", str(para)))
    assert "\u200c" not in quotes
    from aduport.exporters.md_exporter import export_md

    markdown = export_md(model)
    assert "| Wide head |" in markdown
    assert "| Tall" in markdown and "| B3" in markdown
    assert "1. First numbered" in markdown
    from aduport.core.converter import convert

    converted = convert(
        fixture.read_bytes(), "pdf", "md", filename="text.pdf", cache_bypass=True
    ).decode("utf-8")
    assert "| Wide head |" in converted
    assert "| Tall" in converted
    assert "Wide head End Tall" not in converted
    assert "1. First numbered" in converted


def test_external_pdf_keeps_bold_italic_and_strike():
    """Font face and a line through the glyphs, not anydoc's markdown channel."""
    from aduport.core.document_model import InlineMarkType
    from aduport.exporters.md_exporter import export_md
    from aduport.importers.pdf_text_fallback import extract_pdf_text_model

    fixture = (
        Path(__file__).resolve().parents[2]
        / "native/anydoc/tests/fixtures/pdf/text.pdf"
    )
    if not fixture.is_file():
        pytest.skip("text.pdf fixture missing")
    model = extract_pdf_text_model(fixture.read_bytes(), "text.pdf")

    def spans(node, kind):
        text = node.text.text
        return [text[mark.start : mark.end] for mark in node.text.marks if mark.type == kind]

    body = next(node for node in model.nodes if node.plain_text().startswith("Plain paragraph"))
    assert "bold" in spans(body, InlineMarkType.BOLD)
    assert "italic" in spans(body, InlineMarkType.ITALIC)
    assert "struck" in spans(body, InlineMarkType.STRIKETHROUGH)
    styled = next(node for node in model.nodes if "NotBold" in node.plain_text())
    bold_spans = spans(styled, InlineMarkType.BOLD)
    assert any(span.startswith("Style-bold") for span in bold_spans)
    assert not any("NotBold" in span for span in bold_spans)
    markdown = export_md(model)
    assert "**bold**" in markdown
    assert "*italic*" in markdown
    assert "~~struck~~" in markdown
    assert "## Fixture Document" in markdown
    assert "## **Fixture Document**" not in markdown


def test_pdf_list_line_does_not_swallow_the_next_block():
    from aduport.importers.markdown_rehydrate import parse_markdown_nodes
    from aduport.importers.pdf_text_fallback import _separate_markdown_blocks

    text = _separate_markdown_blocks("- ECHO 二\nFOXTROT 引用行\n")
    nodes = parse_markdown_nodes(text)
    item = next(node for node in nodes if node.type == "list_item")
    assert item.plain_text() == "ECHO 二"
    assert any(
        node.type == "paragraph" and "FOXTROT" in node.plain_text() for node in nodes
    )


def test_pptx_list_text_does_not_keep_the_bullet():
    from aduport.core.converter import convert
    from aduport.core.document_model import DocumentModel, ListItem, RichText
    from aduport.exporters.md_exporter import export_md
    from aduport.exporters.txt_exporter import export_txt
    from aduport.importers.list_markers import strip_redundant_list_prefix

    assert strip_redundant_list_prefix("- ECHO 一", ordered=False) == "ECHO 一"
    assert strip_redundant_list_prefix("well-known phrase", ordered=False) == "well-known phrase"
    cleaned = DocumentModel(
        nodes=[
            ListItem(
                text=RichText(
                    text=strip_redundant_list_prefix("- ECHO 一", ordered=False)
                )
            )
        ]
    )
    assert export_md(cleaned).strip() == "- ECHO 一"
    assert export_txt(cleaned).strip() == "- ECHO 一"
    blob = convert(
        "# ALPHA\n\n- ECHO 一\n".encode(),
        "md",
        "pptx",
        filename="a.md",
        cache_bypass=True,
    )
    import zipfile
    from io import BytesIO

    with zipfile.ZipFile(BytesIO(blob)) as zf:
        slides = [
            zf.read(name).decode("utf-8")
            for name in zf.namelist()
            if name.startswith("ppt/slides/slide")
        ]
    joined = "\n".join(slides)
    assert "ECHO" in joined
    assert "- ECHO" not in joined
