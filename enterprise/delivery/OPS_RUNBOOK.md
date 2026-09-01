# 运维手册（企业私有化）

适用角色：客户运维、实施工程师  
配套：`enterprise/docker-compose.yml`、`install.sh`、`smoke_test.sh`

---

## 1. 架构与端口

| 组件 | 默认地址 | 说明 |
|------|----------|------|
| API | `http://127.0.0.1:8000` | FastAPI；健康检查 `/api/health` |
| Web UI | `http://127.0.0.1:8080` | Nginx 静态前端（反代到 API 按部署调整） |
| 数据卷 | `mdbridge-data` → 容器内 `/data` | Job DB、审计日志、可选模型挂载 |

生产建议：仅内网监听或置于反向代理（HTTPS）之后；不要对公网裸露 8000。

---

## 2. 首次安装

1. 确认 Docker 可用，磁盘满足验收环境要求（slim ≥4GB / 全量 ≥20GB）。  
2. 加载镜像（离线包）或在构建机 `docker compose build`。  
3. 复制环境文件：

```bash
cp .env.example .env
# 必改：MDBRIDGE_API_KEY 或 MDBRIDGE_API_KEYS
# 建议：MDBRIDGE_LICENSE_REQUIRED=true 及签发密钥（见 ../LICENSE.md）
```

4. 启动：

```bash
./scripts/install.sh
./scripts/smoke_test.sh http://127.0.0.1:8000
```

5. 按 [../ACCEPTANCE.md](../ACCEPTANCE.md) 勾选 B1–B11（合同要求项）。

---

## 3. 关键配置项

| 变量 | 建议企业默认 | 说明 |
|------|--------------|------|
| `MDBRIDGE_DOCLING_OFFLINE` | `true` | 禁止运行时拉模型 |
| `MDBRIDGE_OCR_ENABLED` | `false` | 扫描 OCR 加购再开 |
| `MDBRIDGE_JOB_DB_PATH` | `/data/jobs.sqlite3` | 持久化 |
| `MDBRIDGE_AUDIT_LOG_PATH` | `/data/logs/audit.jsonl` | 审计 |
| `MDBRIDGE_VIRUS_SCAN` | `false` | 有 ClamAV 再开 |
| `MDBRIDGE_VIRUS_SCAN_FAIL_CLOSED` | `true` | 开启扫描后缺二进制则拒绝上传 |
| `MDBRIDGE_DOCX_MAX_NODES` | `50000` | 超限返回 `docx_too_large` |
| `MDBRIDGE_FETCH_REMOTE_IMAGES` | `false` | 内网默认关外链拉图 |
| `MDBRIDGE_OCR_MAX_PAGES` | `20` | 开启 OCR 时限制页数，防整书卡死 |
| `MDBRIDGE_OCR_ALLOW_PADDLE` | Linux GPU 按需 `true` | macOS 默认勿开 |

多租户 Key 示例：

```bash
MDBRIDGE_API_KEYS=acme:sk-aaa,beta:sk-bbb
```

---

## 4. 日常运维

### 4.1 健康检查

```bash
curl -s http://127.0.0.1:8000/api/health | jq .
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8000/api/formats | jq '.export_only'
```

### 4.2 查看日志

```bash
docker compose -f enterprise/docker-compose.yml logs -f --tail=200 backend
```

审计文件（若启用）：数据卷内 `logs/audit.jsonl`。

### 4.3 备份

至少备份：

- `/data/jobs.sqlite3`（或 `MDBRIDGE_JOB_DB_PATH`）  
- `/data/logs/`  
- `.env`（离线加密保管，勿入库）  
- License 文件  

建议每日冷备 + 变更前手动快照。

### 4.4 升级

1. 备份数据卷与 `.env`  
2. 加载新版本镜像 / 替换 compose  
3. `docker compose up -d`  
4. 跑 `smoke_test.sh` + 抽样中文 PDF、MD⇄DOCX  
5. 保留上一版本镜像 tag 以便回滚  

### 4.5 回滚

```bash
docker tag md-bridge-backend:enterprise md-bridge-backend:enterprise.bak  # 升级前
# 回滚时重新 tag 旧镜像为 :enterprise 并 compose up -d
```

---

## 5. 排障速查

| 现象 | 排查 |
|------|------|
| `/api/health` 不通 | `docker compose ps`；端口占用；看 backend 日志 |
| 上传 401 | API Key / `MDBRIDGE_API_KEYS` 是否匹配请求头 |
| License 403 | `machine_id` 是否与签发一致；密钥是否过期；`LICENSE_SECRET` 是否一致 |
| 中文 PDF 异常 | 确认企业镜像含 CJK 字体路径；看错误码是否为明确 422 |
| PDF 导入卡住 | 确认 `DOCLING_OFFLINE=true`；无模型时应快失败而非长时间挂起 |
| OCR 很慢 / 卡死 | 是否误开 OCR；`OCR_MAX_PAGES`；大教材勿默认全书 OCR |
| `virus_scan_unavailable` | 开了扫描但未装 ClamAV；装好或关 `VIRUS_SCAN` |
| 磁盘涨 | Job TTL、审计日志轮转、临时 spill 目录 |

---

## 6. 安全基线

- 仅内网或 VPN 访问；TLS 终止放在网关  
- 定期轮换 API Key；按租户发放，勿共用一把全局 Key 给所有部门  
- 生产关闭 `MDBRIDGE_DEBUG`  
- 远程图片拉取默认关；若开启须知 SSRF 防护已启用但仍建议白名单业务场景  
- 病毒扫描为可选加固，不是默认依赖  

---

## 7. 支持升级路径

合同 SLA 范围内：提供日志片段、`/api/health` JSON、版本号、复现文件（脱敏）与验收编号。  
超出范围（定制格式、现场扫描仪调优、二次开发）走变更单，见 [PRICING_BOUNDARY.md](./PRICING_BOUNDARY.md)。
