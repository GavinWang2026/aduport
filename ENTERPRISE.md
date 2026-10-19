# AduPort Enterprise

企业私有化能力入口。开源仓库提供 **license / multi-tenant API key** 钩子；OCR、运维脚本与能力说明见 `enterprise/`。

## 能力边界（可发现）

| 已具备 | 说明 |
|--------|------|
| 一机一码 License | `ADUPORT_LICENSE_*`；CLI `machine-id` / `license-issue` / `license-check` |
| 多租户 API Key | `ADUPORT_API_KEYS`；任务按 `tenant_id` 隔离 |
| HttpOnly 会话 | `POST /api/auth/session`；`ADUPORT_SESSION_SECRET`（≥16）跨 worker HMAC；`ADUPORT_COOKIE_SECURE`（默认开） |
| Job token | 创建 返回一次性 `access_token`；前端仅内存短寿命，不进 `sessionStorage` |
| 多 worker 纵深 | `ADUPORT_REDIS_URL`：限流 + session revoke + license 席位跨进程（无 Redis 则进程内） |
| 异步任务 + Webhook | `POST /api/jobs` + `callback_url`；结果落盘 `ADUPORT_JOB_RESULT_DIR` |
| OCR 加购路径 | `enterprise/OCR.md`；health `ocr_ready` / `install_hint` |
| 离线预检 / 探活 | `enterprise/scripts/offline_preflight.sh`、`probe_availability.sh` |
| 可观测 profile | `docker compose -f enterprise/docker-compose.yml --profile observability up -d`；见 `docs/ops/prometheus/` |

| 明确不做（开源默认） | 说明 |
|----------------------|------|
| 公有云多租户计费 / SaaS 账单 | 企业交付可选定制 |
| Pandoc / LibreOffice 打进主镜像 | 体积与许可边界 |

## 从这里开始

1. [enterprise/README.md](enterprise/README.md) — 文档地图  
2. [enterprise/OCR.md](enterprise/OCR.md) — OCR 部署  
3. [enterprise/ANYDOC_RISK.md](enterprise/ANYDOC_RISK.md) — anydoc 依赖边界  
4. [enterprise/delivery/](enterprise/delivery/) — 商业许可 / SLA / 定价边界 / 离线包 / 运维手册  
5. 根目录 `./start.sh` — 一键 Docker / 本地启动  
