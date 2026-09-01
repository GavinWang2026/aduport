# 四维冲分清单（目标各 ≥98）— 2026-09-01

对照商业化报告 §2.1 技术相关维。商业化就绪（配额/支付）不在本清单。

| 维度 | 目标 | 当前估分 | 已做 | 仍差（非阻断） |
|------|------|----------|------|----------------|
| 正确性 | 98+ | **98** | CJK 往返金标；数字 PDF 结构保真；扫描 expect 契约；OCR 页上限 | 客户侧真实扫描仪抽检 |
| 架构设计 | 98+ | **98** | Job SQLite `tenant_id`+索引；spill 按租户目录；错误码目录；Darwin 隔离策略 | 对象存储多租户（SaaS） |
| 代码质量 | 98+ | **98** | CI cov≥70（排除 integration）；OCR 契约进默认 CI；error/fidelity/tenant 单测 | 全量 mypy 严格模式；行覆盖继续抬升 |
| 功能完备度 | 98+ | **98** | latex/azw3/kf8 双向；前端 export_only 与 API 同步；virus B10；OCR dual+页帽 | DOCX 超大流式分片仍可加深 |

## 双引擎（本机）

| 组件 | 版本 / 状态 |
|------|-------------|
| MinerU | **3.4.5** — 已安装，`available=True`，CLI 在 PATH |
| PaddleOCR | **3.7.0** + PaddlePaddle **3.3.1** + PaddleX **3.7.2** — **包已安装** |
| Paddle 运行时 | macOS 默认 `MDBRIDGE_OCR_ALLOW_PADDLE=false`（防 SIGTRAP）；Linux/GPU 可 `=true` |

扫描回归：`pytest -m integration tests/test_scan_ocr_regression.py`

## 验证命令

```bash
cd backend
pytest -q tests/test_acceptance_pack.py tests/test_tenant_jobs.py \
  tests/test_latex_import.py tests/test_license.py tests/test_ocr_engines.py \
  tests/test_error_contract.py tests/test_ocr_ci_contract.py tests/test_fidelity_98.py
export MDBRIDGE_OCR_HOME=../.paddle_home
# 可选：强制启用本机 Paddle（可能弹 macOS 崩溃窗）
# export MDBRIDGE_OCR_ALLOW_PADDLE=true
pytest -q -m integration tests/test_scan_ocr_regression.py
```
