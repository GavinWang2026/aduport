# A/B 行为对拍契约（CI 测试规范）

任何吞吐/保真/缓存/流式对比测试必须同时满足：

1. **`cache_enabled=False`**（或等价：唯一 payload / 绕过 `result_cache`）  
   否则第二次命中缓存会把「行为对拍」变成「缓存命中对拍」。
2. **文档大小超过相关阈值**  
   例：测 `stream_convert` 时 `len(payload) >= stream_min_bytes`（默认 256000）；测 spill 时超过 `spill_threshold_mb`。
3. **目标设置落在所测转换路径上**  
   例：测进程隔离必须走 `convert_isolated` / API `routes` 热路径，不可只调 `converter.convert` 却断言 isolate 行为；测 `html_katex` / `docx_auto_toc` 时缓存键与导出路径均须读取该设置。

缺一则结论不可信。门禁用例见 `tests/test_ab_contract.py`（含 V-A 嵌套 span 中文六例）与 `tests/test_v6_t1_t4_closure.py`（G2 显式关缓存 + 足够大的 workload；缓存键含 katex/toc）。

## 假象剔除（v7 sprint）

回读 / 广度统计额外约束：

4. **禁止把同格式 identity（`src==tgt` 直通）计入 `conversions_total` 或保真成功率**  
   identity 单独记 `identity_passthrough` / Prometheus `mdbridge_identity_passthrough`。
5. **禁止依赖 PDF `%%MDBRIDGE-AST` sidecar 或 xlsx `_AST` sheet 充当「保真」**  
   二者默认关闭（`MDBRIDGE_PDF_AST_SIDECAR=0`、`MDBRIDGE_XLSX_AST_SHEET=0`）；仅 opt-in 调试往返。
6. **WeasyPrint 标定**：有 pango 环境时 `export_pdf` 须报告 `pdf_engine=weasyprint`；不可用时明示 `pdf_simple` 降级，不得静默用 sidecar 冒充排版保真。

门禁：`tests/test_sprint_illusion_ab_weasy.py`。对外分仍锚 `enterprise/SCORE_93.md` **66.2**。
