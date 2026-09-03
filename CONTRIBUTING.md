# 贡献指南（Contributing）

对外评分锚点：`enterprise/SCORE_93.md` → **66.2**。贡献不得用「自评抬分」替代实测。

## 我能贡献什么

| 方向 | 入口 | 说明 |
|------|------|------|
| 格式导入/导出 | `backend/app/importers/` · `exporters/` | 注册到 `registry.py`；补 golden / fidelity 测试 |
| 原生加速 | `native/anydoc/` | 跟 `UPSTREAM_PIN`；见 `enterprise/ANYDOC_RISK.md` |
| 插件式深度引擎 | docling / OCR / LibreOffice 桥 | 可选依赖；**禁止**把 GPL/LibreOffice 打进 MIT 发行物 |
| 示例与语料 | `enterprise/samples/` | 脱敏样例 + README；私有语料走 `MDBRIDGE_PRIVATE_SAMPLES_DIR` |
| 文档 | `enterprise/*` · README | 对外分口径禁止虚高 |

## 本地开发

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
MDBRIDGE_REQUIRE_AUTH=0 pytest -q tests/test_v8_bc_roadmap_gates.py
```

CI 门禁：`.github/workflows/ci.yml` → `fidelity-gates`（含 golden xlsx / header / xlsb / B·C）。

## PR 清单

- [ ] 有失败用例复现或新门禁测试  
- [ ] 未引入裸 `ET.fromstring` / `extractall`  
- [ ] 未把 secrets / 客户样例提交进仓  
- [ ] 文档若改分数口径，必须仍锚 **66.2** 或说明「冲刺自评」双轨
