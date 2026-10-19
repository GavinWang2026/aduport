"""v0.6 AST semantic nodes + format auto-detect."""

from __future__ import annotations

from aduport.core.document_model import (
    Blockquote,
    Footnote,
    HorizontalRule,
    ListItem,
    MathBlock,
)
from aduport.core.format_detect import detect_source_format
from aduport.exporters.md_exporter import export_md
from aduport.importers.md_importer import import_md


def test_blockquote_roundtrip():
    src = b"> hello quote\n>\n> second line\n"
    model = import_md(src)
    bqs = [n for n in model.nodes if isinstance(n, Blockquote)]
    assert bqs
    assert any("hello" in (p if isinstance(p, str) else p.text) for p in bqs[0].paragraphs)
    out = export_md(model)
    assert "> hello" in out or ">hello" in out.replace(" ", "")


def test_horizontal_rule():
    model = import_md(b"a\n\n---\n\nb\n")
    assert any(isinstance(n, HorizontalRule) for n in model.nodes)
    assert "---" in export_md(model)


def test_task_list_checked():
    model = import_md(b"- [x] done\n- [ ] todo\n")
    items = [n for n in model.nodes if isinstance(n, ListItem)]
    assert len(items) >= 2
    assert items[0].checked is True
    assert "done" in items[0].plain_text()
    assert items[1].checked is False
    out = export_md(model)
    assert "[x]" in out
    assert "[ ]" in out


def test_footnote_def():
    model = import_md(b"Hi[^1]\n\n[^1]: note body\n")
    fns = [n for n in model.nodes if isinstance(n, Footnote)]
    assert fns
    assert fns[0].id == "1"
    assert "note body" in fns[0].text
    assert "[^1]:" in export_md(model)


def test_math_display():
    model = import_md(b"before\n\n$$a^2+b^2$$\n\nafter\n")
    maths = [n for n in model.nodes if isinstance(n, MathBlock)]
    assert maths
    assert "a^2" in maths[0].content
    out = export_md(model)
    assert "$$" in out


def test_html_heading_to_ast():
    model = import_md(b"<h2>Title</h2>\n<p>Body</p>\n")
    from aduport.core.document_model import Heading, Paragraph

    assert any(isinstance(n, Heading) and n.plain_text() == "Title" for n in model.nodes)
    assert any(isinstance(n, Paragraph) and "Body" in n.plain_text() for n in model.nodes)


def test_detect_source_format_ext_and_magic():
    assert detect_source_format("a.markdown") == "md"
    assert detect_source_format("x.azw3") == "azw3"
    assert detect_source_format("n.bin", b"%PDF-1.4\n") == "pdf"
    assert detect_source_format("n.bin", b"<!DOCTYPE html><html></html>") == "html"
