# 验收样张库（冲 95 · 客户抽检代理）

本目录为**可复现的验收样张库**，替代对外承诺中的「客户样张抽检 ≥90%」在仓库内的工程代理。

| 路径 | 说明 |
|------|------|
| `backend/tests/fixtures/sample_pack/` | 样张源文件 + `manifest.json`（**v1.2**：ods/odp + txt 冷路径 + audit_proxy_min_pass_rate） |
| `backend/tests/test_sample_pack_t39.py` | 按 manifest 转多目标并断言哨兵；含 **≥90% 抽检代理率** 门禁 |
| `backend/tests/test_private_samples_optional.py` | 外置客户卷；`MDBRIDGE_PRIVATE_SAMPLES_REQUIRED=1` 时未挂载即 **fail** |
| `backend/tests/test_api_e2e_t40.py` | HTTP 面 E2E：health → formats → 鉴权转换 → 异步 job |
| `frontend/e2e/` | Playwright：鉴权 UI + 格式/CTA + **真转换+下载**（job API mock） |

## 私有客户样张（外置卷 / 硬门禁）

```bash
export MDBRIDGE_PRIVATE_SAMPLES_DIR=/path/to/private_samples
# 企业/夜跑硬门禁：未挂载或无效目录直接失败（不再 skip）
export MDBRIDGE_PRIVATE_SAMPLES_REQUIRED=1
# 目录需含 manifest.json（同 sample_pack schema）+ 样张文件
cd backend && pytest -q tests/test_private_samples_optional.py --tb=short
```

CI `fidelity-gates` / `backend` 将 `fixtures/sample_pack` 挂为 `MDBRIDGE_PRIVATE_SAMPLES_DIR` 并开启 REQUIRED，使 ≥90% 抽检代理成为硬门禁。真实客户样张放入私有卷 / 不入库；合同抽检时对照 `ACCEPTANCE.md` B1–B4 人工项。

```bash
cd backend
pytest -q tests/test_sample_pack_t39.py tests/test_api_e2e_t40.py tests/test_phase_c_gates.py \
  tests/test_cov_cold_paths_c.py tests/test_cov_anydoc_ast_c.py --tb=short

cd ../frontend
npm run build && npx playwright install chromium && npm run test:e2e
```
