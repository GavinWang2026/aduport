# 贡献指南（Contributing）

以可复现的测试与实测为准，勿用自评分数替代门禁。

## 我能贡献什么

| 方向 | 入口 | 说明 |
|------|------|------|
| 格式导入/导出 | `backend/aduport/importers/` · `exporters/` | 注册到 `registry.py`；补 golden / fidelity 测试 |
| 原生加速 | `native/anydoc/` | 跟 `UPSTREAM_PIN`；见 `enterprise/ANYDOC_RISK.md`；fuzz / snapshots 在 vendored 树内 |
| Agent skill | `skills/convert-documents/` | 指导 Agent 调 AduPort，勿调第三方 anydoc CLI |
| 插件式深度引擎 | docling / OCR / LibreOffice 桥 | 可选依赖；**禁止**把 GPL/LibreOffice 打进 Apache-2.0 发行物 |
| 示例与语料 | `enterprise/samples/` | 脱敏样例 + README；私有语料走 `ADUPORT_PRIVATE_SAMPLES_DIR` |
| 文档 | `enterprise/*` · README | 能力说明保持与代码一致 |

## 本地开发

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ADUPORT_REQUIRE_AUTH=0 pytest -q tests/test_v8_bc_roadmap_gates.py
```

CI 门禁：`.github/workflows/ci.yml` → `fidelity-gates`（含 golden xlsx / header / xlsb / B·C）。

## PR 清单

- [ ] 有失败用例复现或新门禁测试  
- [ ] 未引入裸 `ET.fromstring` / `extractall`  
- [ ] 未把 secrets / 客户样例提交进仓  
- [ ] **前端类型契约**：若改了 `api.ts` / 组件里用到的 payload 字段，必须同步 `frontend/src/types.ts`，并本地跑 `cd frontend && npm run build`（CI `frontend` job 含 `vue-tsc --noEmit`，漏类型会红）  
- [ ] **跨平台脚本**：新增/改 shell 时避免 GNU 扩展（如 grep `\s`），改用 `[[:space:]]`；在 macOS 上至少验证 `--help` 退出码 0  

## 贡献授权（DCO）

提交 PR 即表示你同意：

1. 你的贡献以 **Apache-2.0** 授权，并允许维护者在不改变开源授权的前提下，
   将其用于企业版 / 商业交付（含再许可）。
2. 提交信息需带 `Signed-off-by: 你的名字 <邮箱>`（`git commit -s`）。

**不加 DCO 行，PR 不合入。** 这不是形式主义：仓库目前是单作者，保留再许可能力才能在
日后调整授权（例如企业版分仓）；一旦混入未授权的第三方代码，整仓的授权边界就再也收不回来。

**例外**：`native/anydoc/` 是 firecrawl/anydoc 的 MIT fork，其源码与 `LICENSE` **保持 MIT 原样**，
不适用本条再许可约定；同步请走 `scripts/sync_anydoc.sh`，勿手改上游文件。
