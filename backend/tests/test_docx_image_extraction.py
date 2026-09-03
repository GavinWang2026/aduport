"""P0(v9): DOCX 内嵌图片抽取 —— 回归 docx_text.py 静默丢图（w:drawing 媒体）。

v9 实测竞技发现：docx importer 此前从不解析 w:drawing/a:blip/r:embed，
AST 全树 Image=0，docx→md/html 全链路无图。本测试锁定修复行为。
"""

from __future__ import annotations

import base64
from io import BytesIO

from docx import Document
from docx.shared import Inches

from app.core.document_model import Image
from app.exporters.md_exporter import export_md
from app.importers.docx_importer import import_docx

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _docx_with_images() -> bytes:
    doc = Document()
    doc.add_heading("图文样本", level=1)
    p = doc.add_paragraph("上图：")
    p.add_run().add_picture(BytesIO(_PNG), width=Inches(0.4))
    # 纯图片段落（无任何文本）——旧逻辑因 rich.text 为空被 continue 直接吞掉
    pic_only = doc.add_paragraph()
    pic_only.add_run().add_picture(BytesIO(_PNG), width=Inches(0.3))
    doc.add_paragraph("图注：内嵌位图")
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_image_nodes_extracted():
    model = import_docx(_docx_with_images(), filename="imgs.docx")
    imgs = [n for n in model.nodes if isinstance(n, Image)]
    assert len(imgs) == 2, f"期望 2 个 Image 节点, 实际 {len(imgs)}"
    assert all(len(i.data) > 0 for i in imgs), "Image 节点应携带像素字节"
    assert all(i.mime_type == "image/png" for i in imgs)
    assert all(i.origin_part == "word/document.xml" for i in imgs)


def test_docx_pure_image_paragraph_not_swallowed():
    """纯图片段落不再因空文本被 continue：图注 Paragraph 仍在最后一个 Image 之后。

    enrich_docx_model 可能向 nodes 尾部追加 PageSetup/Section 等元节点，因此只断言
    相对顺序（Image → 后续图注），不依赖绝对末尾。
    """
    model = import_docx(_docx_with_images(), filename="imgs.docx")
    seq = [n.__class__.__name__ for n in model.nodes]
    assert "Image" in seq
    last_img = max(i for i, t in enumerate(seq) if t == "Image")
    assert "Paragraph" in seq[last_img:], f"最后一个 Image 之后应有图注, 序列={seq}"


def test_docx_image_survives_md_html_docx_targets():
    """P0 upgrade: images must reach major targets, not only AST."""
    from app.core.converter import convert
    from app.core.document_model import Image
    from app.exporters.docx_exporter import export_docx
    from app.exporters.html_exporter import export_html
    from app.importers.docx_importer import import_docx

    raw = _docx_with_images()
    model = import_docx(raw, filename="multi.docx")
    assert sum(1 for n in model.nodes if isinstance(n, Image)) >= 2

    md = convert(raw, "docx", "md", filename="multi.docx", skip_magic=True).decode()
    assert "data:image" in md

    html = export_html(model)
    assert "data:image" in html or "<img" in html

    back = export_docx(model)
    assert b"word/media/" in back or b"image" in back.lower()


def _docx_with_table_image() -> bytes:
    """DOCX with a picture inside a table cell (w:tc → w:drawing)."""
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "左栏"
    run = table.cell(0, 1).paragraphs[0].add_run()
    run.add_picture(BytesIO(_PNG), width=Inches(0.35))
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_table_cell_image_extracted():
    from app.core.document_model import Table

    model = import_docx(_docx_with_table_image(), filename="tbl-img.docx")
    tables = [n for n in model.nodes if isinstance(n, Table)]
    assert tables, "应有 Table 节点"
    cell_imgs = [img for row in (tables[0].cells or []) for c in row for img in (c.images or [])]
    assert len(cell_imgs) >= 1, "表内 w:tc drawing 应进入 TableCell.images"
    assert all(len(i.data) > 0 for i in cell_imgs)

    from app.core.converter import convert

    md = convert(
        _docx_with_table_image(), "docx", "md", filename="tbl-img.docx", skip_magic=True
    ).decode()
    assert "data:image" in md
