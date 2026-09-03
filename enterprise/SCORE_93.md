# 评分基线（v7.2 · 对外锚 66.2 · 冲刺自评 ≈77.9 · 目标轨 98+）

对照 `outputs/md-bridge-功能与性能全面评估报告-v7.html`（v7.2 五维冲刺双轨）。
假象附录：`enterprise/KNOWN_ILLUSIONS.md`（R-01–R-03）。

**废止** 对外使用「自评 88 / 冲刺 77.9 / 目标 98」作为可承诺分数。  
独立实测锚仍为 **66.2**。v7.2 工程冲刺（ods/odp / spill / lazy 字体 / 回读扩面）
**不授权** 跳改对外分。全量 A/B 重测前禁止对外承诺 75+/88/95/98。

**废止** 对外使用「自评 88」或内部工程态 ~86 作为可承诺分数。  
v6 独立复评实测 **66.2**；T1–T4 / D-01–D-30 工程收口 **不授权** 跳回 88。下一季复测后再上调。

v6.5 / v7 工程叠层（V-A–G / §3.3 哨兵 / safe_zip 等）**不改变**本锚点。


## 对外口径（合同 / 发布 / 投资人）

| 项 | 值 |
|----|----|
| **当前对外分** | **66.2**（独立实测，C+） |
| 历史自评 88 | 仅作对照，**禁止**作为当前承诺 |
| 合同「75 可交付」 | 仍是下一阶段目标，须在验收中明示「当前独立实测 66.2，差距已知」 |
| 冲 95 | 不在本季度对外叙事 |

## 方法论（评分校准）

「测试全绿 ≠ 功能达标」。影响评分的关键结论须：静态 AST 取证 + 运行时实测 + 亲验复现。  
A/B 对拍三条前置条件见 **`enterprise/AB_TEST_CONTRACT.md`**（已入 CI：`tests/test_ab_contract.py`）。

## 三维（内部跟踪，不得替代 66.2）

| 维度 | 权重 | 说明 |
|------|------|------|
| 保真度 | 45% | AST 门禁 + 样张；嵌套 span / 僵尸节点已工程收口 |
| 安全默认值 | 35% | require_auth、zip/XSS/SSRF；历史 12 P0 = 12/12 |
| 实测覆盖 | 20% | fidelity-gates + `test_v6_t1_t4_closure.py` + `test_ab_contract.py` |

## §01–§04 工程收口对照（相对报告原文实测）

| 报告结论 | v6.3 工程态 | 诚实保留 |
|----------|-------------|----------|
| 边缘 importer 23%–53% | CLOSED* 边缘测试入 CI | mobi 行覆盖仍可能低于 75% |
| 7 僵尸 AST | CLOSED — docx_ooxml 产出真节点 | — |
| 嵌套 span 塌缩 D-01 | CLOSED — 栈式解析 + V-A 门禁 | — |
| 列表项代码块丢失 | CLOSED — ListItem.children | — |
| xlsx/md/pdf「假满分」 | 宣传口径已注明 sidecar/直通/单向 | 不把假象写进对外分 |
| docx 保真依赖 anydoc | CLOSED* — ANYDOC_RISK + formats notes | 自研降级仍弱于 anydoc |
| 并发 GIL 0.90× / 池=3 | CLOSED — CPU 池 + 长驻 ProcessPool | 审计 4.0× 非当前 G2 门槛 |
| isolate 杀死缓存 | CLOSED — 长驻 worker 复用缓存 | fork 旧路径仍无缓存 |
| safe_zip 形同虚设 | CLOSED — ×4 + ratio + 累计 session | — |

## A–F 决策执行态

| 方向 | v6.3 |
|------|------|
| **A–E** | **DONE** / **DONE\***（见报告页脚） |
| **F** anydoc | **DONE\*** — `enterprise/ANYDOC_RISK.md` |

## 明确不计入当前对外分

- 扫描 OCR 默认关  
- OLE 导出（ACCEPTED）  
- ods/odp 浅导出不得标 full  
- 自评 88 / 内部 ~86 / 旧「四维各 98」
- PDF AST sidecar / xlsx `_AST`（默认关；opt-in 非保真）
- 同格式 identity 直通（`identity_passthrough`，不进 conversions_total）
