# AduPort Enterprise（私有化）

运维与能力说明（不含合同签字包 / 评分报告）。

| 文档 | 用途 |
|------|------|
| [OCR.md](./OCR.md) | 扫描 OCR 引擎与部署 |
| [RAG.md](./RAG.md) | RAG / embedding 导出 |
| [ANYDOC_RISK.md](./ANYDOC_RISK.md) | vendored anydoc 依赖边界 |
| [PDF_TRISTATE.md](./PDF_TRISTATE.md) | PDF 文本 / docling / OCR 三态 |
| [LICENSE.md](./LICENSE.md) | 企业 License 开关说明 |
| [bench/README.md](./bench/README.md) | 质量评测跑法 |

## 组件

- Compose：`enterprise/docker-compose.yml`（可叠加仓库根 `docker-compose*.yml`）
- 探活：`enterprise/scripts/probe_availability.sh`、`summarize_availability.sh`
- 离线预检：`enterprise/scripts/offline_preflight.sh`
- 本地依赖自检：`aduport doctor`（WeasyPrint / native / OCR / Redis / License）
- 预编译 native：`scripts/install_native_wheel.sh`（Release wheels）；打包：`scripts/pack_native_wheels.sh` → `enterprise/dist/`
- Prometheus / Alertmanager 样例：本目录 `prometheus.yml`、`alertmanager.yml`
- 环境变量样例：`.env.example`

## 与开源边界

企业镜像可含 OCR / 更大依赖；开源默认路径不强制 License、不捆绑托管云 OCR。详见根目录 `ENTERPRISE.md`、`README.md`。
