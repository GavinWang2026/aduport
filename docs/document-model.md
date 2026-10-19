# DocumentModel — 一等公民中间 AST

AduPort 所有导入器最终产出 **`DocumentModel`**，所有导出器从同一 AST 消费。  
`convert(bytes, src, tgt)` 是薄封装：import →（可选 enrich）→ export。

## 快速上手

```python
from aduport.core.document_model import DocumentModel, Heading, Paragraph, Table
from aduport.core.registry import get_exporter  # or convert()
from aduport.exporters.md_exporter import export_md
from aduport.exporters.docx_exporter import export_docx

model = DocumentModel(
    title="Demo",
    nodes=[
        Heading(level=1, text="Hello"),
        Paragraph(text="Body with **marks** via RichText."),
        Table(rows=[["A", "B"], ["1", "2"]]),
    ],
    metadata={"source": "hand-built"},
)

md = export_md(model)
docx = export_docx(model, theme="academic")
```

从文件走完整管线：

```python
from aduport.core.converter import convert

out = convert(open("in.docx", "rb").read(), "docx", "md", filename="in.docx")
```

## 节点一览

| 类型 | 用途 |
|------|------|
| `Heading` | 标题 level 1–6 |
| `Paragraph` | 段落；`text` 可为 `str` 或 `RichText`+`InlineMark` |
| `ListItem` | 有序/无序/任务列表；`number` / `start` 保留起始序号；可嵌套 `children` |
| `PageBreak` | 显式分页（`type=page_break`）；Markdown 为 `<!-- pagebreak -->` |
| `Table` | `rows: list[list[str]]`；anydoc 还可填 `cells`（含 rowspan/colspan） |
| `Image` | `data` / `mime_type` / `alt_text` |
| `CodeBlock` / `Blockquote` / `HorizontalRule` | 代码、引用、分隔线 |
| `MathBlock` | 数学（LaTeX 字符串） |
| `Footnote` / `Comment` / `HtmlBlock` | 脚注、批注、透传 HTML |
| `Revision` | Track changes（`insert`/`delete` + author）；DOCX OOXML 导入 |
| `TextBox` | PPTX shape / DOCX txbx 文本框 |
| `ContentControl` | Word w:sdt 内容控件（tag/alias/control_type/text） |
| `PivotTable` | xlsx 透视表对象（name/location/row/col/data fields） |
| `Image.wrap` | `inline` / `left` / `right` 浮动提示（HTML/PDF CSS） |
| `TableCell.formula` / `cell_kind` | 公式文本与透视/图表元数据（xlsx） |
| `metadata.pdf_layout.blocks` | PDF charbox 像素级行块；HTML 导出含定位预览 |

完整定义见 `backend/aduport/core/document_model.py`。

## 插件边界

- 注册：`register_importer` / `register_exporter`（`aduport.core.registry`）
- 自定义导入：返回 `DocumentModel`；自定义导出：接收 `DocumentModel` → `bytes`
- anydoc 快通道（目标=md）可跳过 AST；模型通道仍会映射到 `DocumentModel`

## 示例

见 [`examples/sdk_demo.py`](../examples/sdk_demo.py) 中的 `demo_document_model()`。
