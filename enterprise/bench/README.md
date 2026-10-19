# AduPort ↔ anydoc 质量评测说明

上游 Firecrawl anydoc 的 `bench/` 用固定语料对比 markitdown / pandoc / docling / LibreOffice 等。语料不随仓发布；本目录只保留**以 AduPort 为主**的跑法，避免再以第三方 CLI 名义对外发榜。

## 目标

- 速度：AduPort `convert … -t md`（含 anydoc 快通道）相对其它工具的中位耗时  
- 结构：标题/表格/列表/链接计数 + 与参考输出的 trigram 覆盖  
- （可选）LLM 盲评：同一页渲染图，成对交换位置

## 最小跑法

```bash
# 1) 准备私有语料目录（勿提交客户文件）
export ADUPORT_PRIVATE_SAMPLES_DIR=/path/to/samples

# 2) 确保原生扩展已编译
bash scripts/build_native.sh

# 3) 对每个样本跑 AduPort（示例）
cd backend && .venv/bin/AduPort convert "$ADUPORT_PRIVATE_SAMPLES_DIR/a.docx" -t md -o /tmp/out.md
```

完整对照 harness 可从上游 anydoc `bench/` 拷贝后，把「anydoc CLI」一行改成调用 `AduPort convert`（或 `python -m aduport.cli convert`）。竞品命令保持不变即可。

## 与产品边界

| 通道 | 评测关注点 |
|------|------------|
| Office → MD 快通道 | GFM 完整性、列表编号、合并单元格 |
| PDF → MD（`ANYDOC_PDF_MARKDOWN`） | 文本层抽取；扫描件应降级 OCR，勿记为失败 |
| 非 MD 目标 | 必须走 AST；PDF 无 anydoc 模型 |

详见 `enterprise/ANYDOC_RISK.md`、`native/anydoc/README.md`。
