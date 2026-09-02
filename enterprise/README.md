# md-bridge 企业私有化交付包

本目录提供**打包前验收**、**离线交付**与**合同附件模板**。转换引擎仍用仓库根目录的 Docker / compose。

**从这里开始：** [DELIVERY.md](./DELIVERY.md)（私有化交付总览与文档地图）

仓库根目录还有精简入口：[ENTERPRISE.md](../ENTERPRISE.md)（能力边界一页纸）。

---

## 文档地图

| 文档 | 说明 |
|------|------|
| [DELIVERY.md](./DELIVERY.md) | 就绪结论、开单前检查、与开源边界 |
| [ACCEPTANCE.md](./ACCEPTANCE.md) | 合同验收清单 B1–B11 |
| [delivery/OFFLINE_PACK.md](./delivery/OFFLINE_PACK.md) | 全量离线包怎么打、怎么验 |
| [delivery/OPS_RUNBOOK.md](./delivery/OPS_RUNBOOK.md) | 安装 / 备份 / 升级 / 排障 |
| [delivery/SLA.md](./delivery/SLA.md) | SLA 模板 |
| [delivery/COMMERCIAL_LICENSE.md](./delivery/COMMERCIAL_LICENSE.md) | 商业授权书模板 |
| [delivery/PRICING_BOUNDARY.md](./delivery/PRICING_BOUNDARY.md) | 报价边界与加购 |
| [LICENSE.md](./LICENSE.md) | 一机一码操作说明 |
| [OCR.md](./OCR.md) | 扫描 OCR（可选加购） |
| [../docs/ops/prometheus/README.md](../docs/ops/prometheus/README.md) | Prometheus 指标 / Grafana / 告警规则 |
| [SCORE_93.md](./SCORE_93.md) | 技术四维冲分 |

---

## 打包前必过（冲正确性 / 质量）

在开发机（有 Python 依赖）执行：

```bash
# 1) 验收用例
cd backend && pytest -q tests/test_acceptance_pack.py tests/test_p0_commercial.py \
  tests/test_error_contract.py tests/test_surface_webhooks_results.py

# 2) 全量回归（可选）
pytest -q --tb=short -m "not integration"

# 3) 可选：对已启动的 API 做 HTTP 冒烟
../enterprise/scripts/smoke_test.sh http://127.0.0.1:8000
```

全部绿灯后再打**全量**离线包（见 [delivery/OFFLINE_PACK.md](./delivery/OFFLINE_PACK.md)）。

---

## 离线交付（客户侧）

```bash
# 构建方（有网）
./enterprise/scripts/pack.sh
# 仅文档/脚本（Docker 未就绪时，不能代替正式交付）
./enterprise/scripts/pack.sh --docs-only

# 客户方（内网）— 以包内说明为准
gunzip -c md-bridge-enterprise-*-bundle.tar.gz | tar -x
# docker load …
cp .env.example .env   # 填写 API Key / License
./scripts/install.sh
./scripts/smoke_test.sh http://127.0.0.1:8000
```

产物：`enterprise/dist/md-bridge-enterprise-<ver>-bundle.tar.gz` + `SHA256SUMS.txt`。

---

## 可观测（Prometheus + Alertmanager）

企业 compose 内置可选 profile，开箱抓取 `/api/metrics` 并加载仓库告警规则：

```bash
docker compose -f enterprise/docker-compose.yml --profile observability up -d
# Prometheus → http://127.0.0.1:9090
# Alertmanager → http://127.0.0.1:9093
```

配置：`enterprise/prometheus.yml`（抓取 backend:8000）、`enterprise/alertmanager.yml`（路由占位，生产替换 webhook）、`docs/ops/prometheus/alert_rules.yml`（anydoc_share / ocr_ready 等）。Grafana 看板 JSON 见 `docs/ops/prometheus/grafana_mdbridge.json`。

CI 证据：`backend/tests/test_observability_pack.py`。

---

## 与开源版差异（卖点）

| 项 | 说明 |
|----|------|
| 预置模型 + CJK 字体 | 全量镜像 bake，内网可离线 |
| 数据卷 | Job DB / 审计日志持久化 |
| 企业默认配置 | OCR 默认关、docling 离线、fail_closed |
| 验收与合同附件 | ACCEPTANCE / SLA / 商业授权可签字 |
| License | 一机一码，可到期 |
