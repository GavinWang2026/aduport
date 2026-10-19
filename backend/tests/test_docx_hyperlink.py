"""v9.1 回归：DOCX 超链接锚文本静默丢失 —— 锁 docx_text.py _paragraph_to_rich 修复。

实锤：Word 把超链接文本放在 ``<w:hyperlink>`` 内（非 ``w:p`` 直接子级 ``w:r``），
而旧实现 ``para.runs`` 只收直接 run，锚文本既没进 parts、全文 find 子串也永远
-1，纯超链接段落整段被吞、md 输出凭空缺字。本测试锁定新行为：
按文档序收 Run/Hyperlink、锚文本整段包 LINK mark、嵌套加粗保留。
"""

from __future__ import annotations

from io import BytesIO

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT

from aduport.core.document_model import InlineMarkType, Paragraph, RichText
from aduport.exporters.md_exporter import export_md
from aduport.importers.docx_importer import import_docx


def _add_hyperlink(paragraph, url: str, text: str, *, bold: bool = False) -> None:
    """在段落内以原生 OOXML 追加一个外部超链接（绕开 python-docx 无公开 API）。"""
    r_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    hl = OxmlElement("w:hyperlink")
    hl.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    if bold:
        rpr = OxmlElement("w:rPr")
        rpr.append(OxmlElement("w:b"))
        run.append(rpr)
    t = OxmlElement("w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    run.append(t)
    hl.append(run)
    paragraph._p.append(hl)


def _docx_with_links() -> bytes:
    doc = Document()
    # 混合段：直接 run + 超链接（加粗锚文本）
    p = doc.add_paragraph("前文 ")
    _add_hyperlink(p, "https://example.com/a", "A 站链接", bold=True)
    doc.add_paragraph("后文")
    # 纯超链接段落：旧逻辑因 parts 为空整段被 continue 吞掉
    p2 = doc.add_paragraph()
    _add_hyperlink(p2, "https://example.com/b", "只有链接的段落")
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _paragraphs_by_text(model) -> list[tuple[str, RichText]]:
    out: list[tuple[str, RichText]] = []
    for n in model.nodes:
        if isinstance(n, Paragraph) and isinstance(n.text, RichText):
            out.append((n.text.text, n.text))
        elif isinstance(n, Paragraph):
            out.append((str(n.text), None))
    return out


def test_docx_hyperlink_anchor_text_survives():
    """超链接锚文本必须完整出现在 AST（此前整段丢失）。"""
    model = import_docx(_docx_with_links(), filename="links.docx")
    texts = [t for t, _ in _paragraphs_by_text(model)]
    joined = " | ".join(texts)
    assert "前文 A 站链接" in joined, f"混合段锚文本丢失: {joined!r}"
    assert "只有链接的段落" in joined, f"纯超链接段落被吞: {joined!r}"


def test_docx_hyperlink_mark_and_href():
    """LINK mark 覆盖锚文本精确区间，且 href 从关系恢复。"""
    model = import_docx(_docx_with_links(), filename="links.docx")
    for text, rich in _paragraphs_by_text(model):
        if not text.startswith("前文 "):
            continue
        assert rich is not None
        links = [mk for mk in rich.marks if mk.type == InlineMarkType.LINK]
        assert len(links) == 1, f"期望 1 个 LINK mark, 实际 {len(links)}"
        lnk = links[0]
        assert rich.text[lnk.start : lnk.end] == "A 站链接"
        assert lnk.href == "https://example.com/a"
        # 加粗锚文本 → 链接内应保留 BOLD 子 mark
        bolds = [mk for mk in rich.marks if mk.type == InlineMarkType.BOLD]
        assert any(rich.text[mk.start : mk.end] == "A 站链接" for mk in bolds)


def test_docx_hyperlink_md_renders_link_syntax():
    """md 导出渲染为标准链接语法（此前锚文本凭空消失）。"""
    model = import_docx(_docx_with_links(), filename="links.docx")
    md = export_md(model)
    assert "[**A 站链接**](https://example.com/a)" in md, md
    assert "[只有链接的段落](https://example.com/b)" in md, md
