# PDF 三态策略（v8 §B）

对外评分锚点仍为 `enterprise/SCORE_93.md` → **66.2**。本文件固化产品口径，不抬分。

## 三态

| 态 | `MDBRIDGE_PDF_ENGINE` | 实现 | 适用 |
|----|----------------------|------|------|
| **text** | `text` | `pdf_text_fallback` / pypdfium2 | 可检索 PDF、合同正文层 |
| **docling** | `docling` | Docling（可选模型） | 版面/表格增强；需本地 artifacts |
| **ocr** | `ocr` / `mineru` / `paddleocr_vl` | `ocr_engine` | 扫描件（企业可选） |

`auto`（默认）：文本层字数 ≥ `MDBRIDGE_PDF_TEXT_MIN_CHARS` → **text**；否则若 `MDBRIDGE_OCR_ENABLED` → **ocr**；再否则 **docling**（模型就绪时）或退回 text / 明确报错。

## 元数据

导入结果 `DocumentModel.metadata`：

- `pdf_pipeline`: `text` | `docling` | `ocr`
- `pdf_engine`: 归一化后的配置值

运行时摘要：`app.importers.pdf_importer.pdf_tristate_summary()`。

## 「2 个 Paragraph」回读

纯 text 层对多栏/图文混排会塌缩。深度版面依赖 **docling** 或企业 **OCR**；不得把 sidecar / 假象路径算作排版保真（见 `KNOWN_ILLUSIONS.md` R-03）。
