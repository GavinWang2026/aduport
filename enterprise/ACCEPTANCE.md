# 企业交付验收清单（合同附件建议稿）

产品：md-bridge Enterprise  
版本：__________  
客户：__________  
部署日期：__________  

## A. 环境

- [ ] Linux x86_64，Docker 20+ 可用  
- [ ] 磁盘预留 ≥ 20 GB（全量镜像）或 ≥ 4 GB（slim）  
- [ ] 端口约定：API `8000` / Web `8080`（或反代后 HTTPS）  

## B. 功能验收（必须全部通过）

| # | 用例 | 通过标准 | 结果 |
|---|------|----------|------|
| B1 | `GET /api/health` | `status=ok` | ☐ |
| B2 | 中文 MD → PDF | 提取文本含「中文」或标题汉字；非 `??????` | ☐ |
| B3 | MD → DOCX → MD | 往返后标题/正文仍可读 | ☐ |
| B4 | PDF 导入（无外网） | 不挂死；数字 PDF 文本层可导入或明确报错 &lt; 5s | ☐ |
| B5 | API Key | 未带 Key → 401；带 Key → 可转换（若已启用） | ☐ |
| B6 | 格式边界 | `/api/formats`：latex/azw3/kf8 可导入可导出；`export_only` 不含三者 | ☐ |
| B7 | DOCX 上限 | 超 `MDBRIDGE_DOCX_MAX_NODES` 返回明确错误码 `docx_too_large` | ☐ |
| B8 | 持久化 | 重启后 Job/审计目录仍在数据卷内 | ☐ |
| B9 | 扫描 OCR（可选） | 装引擎后 `pytest -m integration tests/test_scan_ocr_regression.py` 全绿；样张无文本层 | ☐ |
| B10 | 病毒扫描 fail-closed | 启用 `MDBRIDGE_VIRUS_SCAN=true` 且无 ClamAV 时返回 `virus_scan_unavailable` | ☐ |
| B11 | 租户 Job 隔离 | 不同 API Key / tenant 互不可见；SQLite `tenant_id` 列可过滤 | ☐ |

自动化对应：`pytest tests/test_acceptance_pack.py tests/test_error_contract.py tests/test_ocr_ci_contract.py tests/test_fidelity_98.py tests/test_tenant_jobs.py` + `enterprise/scripts/smoke_test.sh`。  
扫描回归：`backend/tests/fixtures/scan/` + `test_scan_ocr_regression.py`（见 [OCR.md](./OCR.md)）。

## C. 配置基线（企业默认）

- [ ] `MDBRIDGE_DOCLING_OFFLINE=true`  
- [ ] `MDBRIDGE_OCR_ENABLED=false`（扫描件需求可另开）  
- [ ] `MDBRIDGE_VIRUS_SCAN_FAIL_CLOSED=true`  
- [ ] `MDBRIDGE_JOB_DB_PATH` 指向持久卷  
- [ ] `MDBRIDGE_DOCX_MAX_NODES` 已告知客户（默认 50000）  
- [ ] 若售卖扫描 OCR：`MDBRIDGE_OCR_ENABLED=true` + 引擎已装；B9 回归通过  

## D. 签字

实施方：__________ 日期：__________  
客户方：__________ 日期：__________  
