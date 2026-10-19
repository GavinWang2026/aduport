# Prometheus / Grafana 运维模板（P2-3）

AduPort 在 `GET /api/metrics` 暴露 Prometheus 文本格式指标（与 `/api/health` 内 `metrics` 快照同源）。

## 企业 compose 一键启用

```bash
docker compose -f enterprise/docker-compose.yml --profile observability up -d
```

会启动 `prometheus` + `alertmanager`，并挂载本目录 `alert_rules.yml` 与 `enterprise/prometheus.yml`。

## 抓取配置

```yaml
scrape_configs:
  - job_name: aduport
    metrics_path: /api/metrics
    static_configs:
      - targets: ["aduport:8080"]
```

若启用 API Key / Basic Auth，在 `scrape_configs` 中配置 `basic_auth` 或 `authorization`。

## 核心指标

| 指标 | 类型 | 用途 |
|------|------|------|
| `aduport_anydoc_hit_rate` | gauge | anydoc 命中比 hits/(hits+degrades) |
| `aduport_anydoc_channel_share` | gauge | importer 桶中 anydoc* 占比 |
| `aduport_ocr_enabled` | gauge | OCR 开关（1=开） |
| `aduport_ocr_ready` | gauge | 至少一个 OCR 引擎可用 |
| `aduport_conversions_failed_by_code{code="ocr_unavailable"}` | counter | OCR 不可用失败次数 |
| `aduport_conversion_latency_p95_seconds` | gauge | 转换 p95 延迟 |
| `aduport_conversions_failed` / `aduport_conversions_total` | counter | 按路由成功/失败 |

## 告警规则

见同目录 `alert_rules.yml`。加载示例：

```bash
promtool check rules docs/ops/prometheus/alert_rules.yml
# prometheus.yml:
# rule_files:
#   - /etc/prometheus/rules/aduport.yml
```

## Grafana 看板

导入 `grafana_aduport.json`（Dashboard → Import → Upload JSON）。

看板侧重 **anydoc_share** 与 **ocr_unavailable / ocr_ready** 运维面板。
