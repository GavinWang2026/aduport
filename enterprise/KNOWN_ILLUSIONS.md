# 合同附录 · 已知假象与统计口径（R-01–R-03）

对齐：`enterprise/ACCEPTANCE.md` · `enterprise/SCORE_93.md`（对外分 **66.2**）  
工程：`MDBRIDGE_XLSX_AST_SHEET` / `MDBRIDGE_PDF_AST_SIDECAR` 默认 **关**；identity 不进 `conversions_total`。

本附录供商务 / 验收签字引用。下列能力**存在于代码**，但**不得**写成「单元格保真 / 排版保真 / 转换成功率」满分依据。

| ID | 项 | 默认行为 | 允许说法 | 禁止说法 |
|----|----|----------|----------|----------|
| **R-01** | xlsx `_AST` meta sheet | `MDBRIDGE_XLSX_AST_SHEET=0`：仅表体 / Content | 「表值级导出；开启 `_AST` 可调试 AST 往返」 | 「xlsx 单元格保真含批注/图表/公式全语义」 |
| **R-02** | 同格式 identity（`src==tgt`） | 校验后原样返回；记 `identity_passthrough` | 「同格式直通（非转换）」 | 计入广度/保真「成功率」分子 |
| **R-03** | PDF `%%MDBRIDGE-AST` sidecar | `MDBRIDGE_PDF_AST_SIDECAR=0` | 「WeasyPrint/pdf_simple 排版；sidecar 仅 debug」 | 「PDF 往返保真 = 排版保真」 |

## 验收勾选

- [ ] 合同正文或本附录已书面告知客户上述三项  
- [ ] 交付材料中的矩阵统计已剔除 identity 对  
- [ ] `/api/formats` notes / `/api/health/detail` 与默认配置一致（sidecar / `_AST` 关）  
- [ ] 门禁：`pytest tests/test_sprint_illusion_ab_weasy.py` 全绿（Weasy 无 pango 时允许 skip 标定项）

## 相关门禁

- A/B 契约：`enterprise/AB_TEST_CONTRACT.md`（含假象剔除 ④–⑥）  
- CI：`fidelity-gates` 含  
  - `test_sprint_illusion_ab_weasy.py` + `test_xml_safe_no_et_fromstring.py`  
  - **v8 阻断项**：`test_golden_xlsx_pipeline.py`（mergeCells 六路）· `test_exporter_spans.py` / `test_v11_features.py`（表头开关）· `test_cov_breadth_redbox.py`（xlsb 校验）  
  - **v8 §B/C**：`test_v8_bc_roadmap_gates.py`（PDF 三态 / trusted isolate / Weasy 曲线文档 / docx style-map）

相关战略文档（不抬分）：`ECOSYSTEM_STRATEGY.md` · `PDF_TRISTATE.md` · `WEASY_PERF_CURVE.md`
