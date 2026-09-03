# 企业交付验收清单（合同附件 · v3.19 口径）

产品：md-bridge Enterprise  
报告对齐：`md-bridge-功能与性能全面评估报告-v3.html` **v3.19**  
版本：__________　客户：__________　部署日期：__________  

> **评分口径**：对外以 v6/v7 独立实测 **66.2** 为锚（见 `enterprise/SCORE_93.md`）。  
> **75 分可交付** 为下一阶段目标，验收须明示与 66.2 的差距，不得沿用「自评 88」。  
> **已知假象**：必须阅读并勾选 `enterprise/KNOWN_ILLUSIONS.md`（R-01–R-03）。

---

## A. 环境

- [ ] Linux x86_64，Docker 20+  
- [ ] 磁盘：全量镜像 ≥ 20 GB 或 slim ≥ 4 GB  
- [ ] 端口：API `8000` / Web `8080`（或 HTTPS 反代）  
- [ ] 生产 **禁止** `MDBRIDGE_REQUIRE_AUTH=0` / 空密钥演示配置  

## B. 三维验收（必须全部通过）

### B1. 保真度（自动化门禁，CI 同款）

| # | 门禁 | 通过标准 | 结果 |
|---|------|----------|------|
| F1 | AST×导出哨兵矩阵 | `pytest tests/test_ast_exporter_matrix.py` 全绿（19×14 哨兵可检索，含 ODS/ODP） | ☐ |
| F2 | §1.2 语义保真升级 | `pytest tests/test_fidelity_matrix_upgrades.py` 全绿 | ☐ |
| F3 | 输出安全 | `pytest tests/test_output_security_t4.py tests/test_p0_batch_fixes.py` 全绿（CSV/HTML/LaTeX） | ☐ |
| F4 | 深度写入 | `pytest tests/test_docx_stub_writes.py tests/test_docx_depth_v311.py tests/test_depth_v317.py` 全绿 | ☐ |
| F5 | 金标抽样 | `pytest tests/test_golden_xlsx_pipeline.py tests/test_golden_odt_json_deep.py tests/test_exporter_spans.py` 全绿 | ☐ |
| F6 | 验收样张库 | `pytest tests/test_sample_pack_t39.py` 全绿（v1.2：ods/odp/txt + audit proxy ≥90%） | ☐ |
| F7 | HTTP E2E | `pytest tests/test_api_e2e_t40.py` 全绿（鉴权转换 + Job Token 拉结果） | ☐ |
| F8 | 浏览器 E2E | `cd frontend && npm run build && npm run test:e2e`（Playwright：鉴权 UI + 格式选择 + 转换 CTA） | ☐ |
| F9 | 假象默认关 + 回读 | `pytest tests/test_sprint_illusion_ab_weasy.py`（sidecar/_AST 默认关；identity 剔除；A/B 回读） | ☐ |
| F10 | xml_safe 禁令 | `pytest tests/test_xml_safe_no_et_fromstring.py`（禁止裸 `ET.fromstring`） | ☐ |

现场抽检（人工）：

| # | 用例 | 通过标准 | 结果 |
|---|------|----------|------|
| B2 | 中文 MD → PDF | 文本层含汉字；非 `??????` | ☐ |
| B3 | MD → DOCX → MD | 标题/正文可读 | ☐ |
| B4 | 数字 PDF 导入（无外网） | &lt; 5s 出结果或明确错误；不挂死 | ☐ |

### B2. 安全默认值

| # | 用例 | 通过标准 | 结果 |
|---|------|----------|------|
| S1 | 强制认证 | 默认 `MDBRIDGE_REQUIRE_AUTH=true`；无 Key → 401（`tests/test_sec01_auth_default.py`） | ☐ |
| S2 | Job Token | 创建返回 `access_token`；无 token 不可拉结果（`tests/test_job_token_t21.py`） | ☐ |
| S3 | CORS / 中间件 | OPTIONS 短路；CORS 在认证外侧（现场 curl 复核） | ☐ |
| S4 | SSRF | Webhook/`safe_fetch` 拒绝内网与危险重定向（`tests/test_p0_batch_fixes.py` 相关） | ☐ |

### B3. 实测覆盖与交付面

| # | 用例 | 通过标准 | 结果 |
|---|------|----------|------|
| C1 | CI 覆盖率 | 后端 `cov-fail-under=78` 通过（OCR/OLE/TXT 冷路径已补；冲 85 仍专项） | ☐ |
| C2 | `/api/formats` | 列出 source/target；`builtin_coverage` 非空；目标含 ods/odp | ☐ |
| C3 | `/api/health` + `/api/health/ready` | liveness ok；detail 含 `pdf_export` | ☐ |
| C4 | 前端 smoke | `cd frontend && npm test` 全绿 | ☐ |
| C5 | 前端 Playwright | `npm run build && npm run test:e2e` 全绿 | ☐ |
| C6 | 租户隔离（若售多租户） | 不同 API Key 任务互不可见 | ☐ |

### B4. 可选增值（合同勾选才验）

| # | 用例 | 通过标准 | 结果 |
|---|------|----------|------|
| O1 | 扫描 OCR | `MDBRIDGE_OCR_ENABLED=true` + 引擎；`pytest -m integration tests/test_scan_ocr_regression.py` | ☐ |
| O2 | 病毒扫描 fail-closed | 无 ClamAV 时返回 `virus_scan_unavailable` | ☐ |
| O3 | Native wheel | `INSTALL_NATIVE=1` 镜像；`/api/health/ready` 在 `MDBRIDGE_NATIVE_REQUIRED=1` 下通过 | ☐ |

## C. 配置基线（企业默认）

- [ ] `MDBRIDGE_REQUIRE_AUTH=true` 且已配置 `MDBRIDGE_API_KEY` 或 `MDBRIDGE_API_KEYS` / Basic  
- [ ] `MDBRIDGE_JOB_TOKEN_REQUIRED=true`  
- [ ] `MDBRIDGE_DOCLING_OFFLINE=true`  
- [ ] `MDBRIDGE_OCR_ENABLED=false`（未售 OCR 时）  
- [ ] `MDBRIDGE_JOB_DB_PATH` / `MDBRIDGE_JOB_RESULT_DIR` 指向持久卷  
- [ ] `MDBRIDGE_DOCX_MAX_NODES` 已书面告知（默认 200000）  
- [ ] `MDBRIDGE_PDF_AST_SIDECAR=false`（未书面开启 debug sidecar）  
- [ ] `MDBRIDGE_XLSX_AST_SHEET=false`（未书面开启 `_AST` 往返）  
- [ ] 已签署 `enterprise/KNOWN_ILLUSIONS.md` 附录  

## D. 自动化命令（实施方自测）

```bash
cd backend
pytest -q --tb=short \
  tests/test_ast_exporter_matrix.py \
  tests/test_fidelity_matrix_upgrades.py \
  tests/test_output_security_t4.py \
  tests/test_p0_batch_fixes.py \
  tests/test_sec01_auth_default.py \
  tests/test_job_token_t21.py \
  tests/test_docx_stub_writes.py \
  tests/test_docx_depth_v311.py \
  tests/test_depth_v317.py \
  tests/test_acceptance_pack.py \
  tests/test_sample_pack_t39.py \
  tests/test_api_e2e_t40.py \
  tests/test_error_contract.py

cd ../frontend && npm test
```

> 已删除旧稿中无法核对的「虚高 98 分」引用；金标/门禁以本表文件名为准。样张说明见 `enterprise/samples/README.md`。

## E. 已知假象附录（强制）

见 **`enterprise/KNOWN_ILLUSIONS.md`**（R-01 xlsx `_AST` · R-02 identity · R-03 PDF sidecar）。

- [ ] 客户已确认：上述三项不计入保真/成功率对外承诺  

## F. 签字

实施方：__________ 日期：__________  
客户方：__________ 日期：__________  
