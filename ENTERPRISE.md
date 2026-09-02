# md-bridge Enterprise

企业私有化交付入口。开源仓库提供 **license / multi-tenant API key** 钩子；合同级交付物、离线包与 SLA 模板集中在 `enterprise/`。

## 能力边界（可发现）

| 已具备 | 说明 |
|--------|------|
| 一机一码 License | `MDBRIDGE_LICENSE_*`；CLI `machine-id` / `license-issue` / `license-check` |
| 多租户 API Key | `MDBRIDGE_API_KEYS`；任务按 `tenant_id` 隔离 |
| 异步任务 + Webhook | `POST /api/jobs` + `callback_url`；结果落盘 `MDBRIDGE_JOB_RESULT_DIR` |
| OCR 加购路径 | `enterprise/OCR.md`；health `ocr_ready` / `install_hint` |
| 离线包 / 运维 | `enterprise/delivery/OFFLINE_PACK.md`、`OPS_RUNBOOK.md` |
| 可观测 profile | `docker compose -f enterprise/docker-compose.yml --profile observability up -d`；见 `docs/ops/prometheus/` |

| 明确不做（开源默认） | 说明 |
|----------------------|------|
| 公有云多租户计费 | 见 `enterprise/delivery/PRICING_BOUNDARY.md` |
| SaaS 控制台 / 账单 | 企业交付可选定制 |
| Pandoc / LibreOffice 打进主镜像 | 体积与许可边界 |

## 从这里开始

1. [enterprise/README.md](enterprise/README.md) — 文档地图  
2. [enterprise/DELIVERY.md](enterprise/DELIVERY.md) — 交付就绪结论  
3. [enterprise/delivery/PRICING_BOUNDARY.md](enterprise/delivery/PRICING_BOUNDARY.md) — 报价与加购边界  
