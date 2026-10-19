"""v0.12: HTML inline marks + task-list checkbox (Depth upgrades)."""

from __future__ import annotations

from aduport.core.converter import convert
from aduport.core.document_model import InlineMarkType, Paragraph
from aduport.exporters.html_render import render_body_html
from aduport.importers.html_importer import import_html
from aduport.importers.md_importer import import_md


def test_md_embedded_html_inline_marks():
    src = (
        "Before\n\n"
        '<p><b>粗体</b>与<i>斜体</i>和<code>码</code>与'
        '<a href="https://x.com">链</a></p>\n'
    ).encode("utf-8")
    model = import_md(src)
    paras = [n for n in model.nodes if isinstance(n, Paragraph)]
    assert paras
    # Find the rich paragraph with marks
    rich = None
    for p in paras:
        if hasattr(p.text, "marks") and p.text.marks:
            rich = p.text
            break
    assert rich is not None, f"expected marks, nodes={[getattr(n,'text',n) for n in paras]}"
    types = {m.type for m in rich.marks}
    assert InlineMarkType.BOLD in types
    assert InlineMarkType.ITALIC in types
    assert InlineMarkType.CODE in types
    assert InlineMarkType.LINK in types
    hrefs = [m.href for m in rich.marks if m.href]
    assert any(h and "x.com" in h for h in hrefs)


def test_html_importer_inline_marks():
    src = b"<p><strong>A</strong> <em>B</em> <code>C</code> <a href='https://ex.com'>D</a></p>"
    model = import_html(src)
    paras = [n for n in model.nodes if isinstance(n, Paragraph)]
    assert paras
    rich = paras[0].text
    assert hasattr(rich, "marks") and rich.marks
    types = {m.type for m in rich.marks}
    assert InlineMarkType.BOLD in types
    assert InlineMarkType.ITALIC in types
    assert InlineMarkType.CODE in types
    assert InlineMarkType.LINK in types


def test_task_list_html_checkbox():
    src = b"- [x] done\n- [ ] todo\n"
    out = convert(src, "md", "html").decode()
    assert 'type="checkbox"' in out
    assert "checked" in out
    body = render_body_html(import_md(src))
    assert 'type="checkbox"' in body
    assert body.count("checkbox") >= 2
