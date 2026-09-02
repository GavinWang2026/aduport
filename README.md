# md-bridge

[![CI](https://github.com/crayfish-team/md-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/crayfish-team/md-bridge/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/md-bridge.svg)](https://pypi.org/project/md-bridge/)

**本地私有化文档交换枢纽** — 以统一 AST（`DocumentModel`）为中心，在 **29 种源格式** 与 **16 种目标格式** 之间做双向转换。提供 Python SDK、CLI、FastAPI 与 Vue3 Web UI；默认纯本地处理，不依赖第三方云端 API。

当前包版本：**0.16.0** · 后端回归：**366 pytest pass**（2026-09）

---

## 它能做什么

md-bridge 解决的是「多格式文档进 → 结构化中间表示 → 多格式出」：

```
Office / PDF / 电子书 / 表格 / 标记语言
        ↓  Importer（anydoc Rust + 原生 Python）
   DocumentModel（AST）
        ↓  Exporter
MD / DOCX / PDF / HTML / EPUB / PPTX / XLSX / JSON / …
```

**典型场景**

| 场景 | 做法 |
|------|------|
| 合同 DOCX → Markdown 知识库 | `docx → md`（anydoc 快通道或 AST） |
| Markdown → 可打印 PDF | `md → pdf`（WeasyPrint + CJK 字体/CSS） |
| 批量目录 Office → MD | CLI `batch` 或 API `/api/convert/batch` |
| 扫描 PDF → 可编辑文本 | OCR 双引擎（MinerU / PaddleOCR-VL，企业可选） |
| 私有化 API 服务 | Docker Compose + API Key / 多租户 / Prometheus |

---

## 功能概览

### 格式

| 方向 | 数量 | 格式 |
|------|------|------|
| **源** | 29 | `md` `txt` `html` `docx` `doc` `docm` `pdf` `pptx` `ppt` `pps` `pot` `pptm` `ppsx` `ppsm` `xlsx` `xls` `xlsb` `xlsm` `ods` `odp` `csv` `odt` `rtf` `epub` `mobi` `azw3` `kf8` `latex` `json` |
| **目标** | 16 | `md` `txt` `html` `docx` `pdf` `pptx` `epub` `mobi` `azw3` `kf8` `latex` `csv` `odt` `json` `xlsx` `rtf` |

权威列表见 `backend/app/core/converter.py` 的 `SUPPORTED_*`；运行时以 `GET /api/formats` / `mdbridge formats` 为准。

### 保真能力（AST + 导出）

- **表格合并单元格**：11 路目标格式原生 `colspan`/`rowspan`（含 MD/TXT HTML 嵌入、LaTeX `\multicolumn`、RTF `\clmgf`）；LaTeX/RTF/MD **import roundtrip** 已接
- **批注 / 脚注 / 修订 / 内容控件 / 文本框 / 透视表**：进 `DocumentModel` 后可在 HTML、EPUB、DOCX、PDF、PPTX、XLSX、JSON 等通道导出
- **数学公式**：MathBlock → HTML KaTeX、LaTeX、PDF 等
- **PDF 文本层**：数字 PDF 默认 `pypdfium2` 本地提取（无需 Hugging Face）；可选双栏启发式
- **DOCX 主题 / 批注导出 / 标题编号**：CLI 与 API 参数对齐

### 内置 anydoc（Rust / MIT）

源码 vendored 于 [`native/anydoc/`](native/anydoc/)，编译为 **`mdbridge_native._doc`**（Docker 默认编装；PyPI _wheel 不含 native，需自行 `bash scripts/build_native.sh`）。

| 通道 | 行为 |
|------|------|
| **快通道** `fast_md` | 目标 = Markdown 时直出 anydoc GFM，零 AST 损失 |
| **模型通道** | `to_document` → `DocumentModel` → 既有导出器 |
| **独占格式** | `doc` `ppt` `xls` `xlsb` `ods` `odp` 及宏变体等 — native 未覆盖时由 anydoc 接管 |
| **共享格式** | `docx` `odt` `rtf` `pptx` `epub` `csv` 等 — anydoc 优先，原生兜底；**`xlsx` 默认留原生**（表头行推断更准） |

配置：`MDBRIDGE_ANYDOC_*`（见 [.env.example](.env.example)）。

### 产品与运维

- **Web UI**：拖拽上传、批量队列、历史、MD/HTML 预览、PDF/DOCX 高级选项
- **HTTP API**：同步转换、批量 ZIP、异步 Job + **Webhook**（`callback_url`）
- **持久化**：`MDBRIDGE_JOB_RESULT_DIR` 任务结果落盘；`MDBRIDGE_OBJECT_STORAGE_URI` 可选归档（file / S3）
- **安全**：可选 `X-API-Key` / Basic Auth、多租户 `MDBRIDGE_API_KEYS`、限流、审计 JSONL、Magic bytes 校验
- **可观测**：`GET /api/metrics`（Prometheus 文本）、anydoc 命中率、OCR 就绪、按错误码统计；Grafana/告警见 [`docs/ops/prometheus/`](docs/ops/prometheus/)

### 性能与稳定性

| 机制 | 说明 |
|------|------|
| anydoc 快通道 | Office → MD 毫秒级，跳过 Python AST 构建 |
| 结果缓存 | 内存 LRU + 可选 gzip（`MDBRIDGE_CACHE_*`） |
| 流式转换 | 大 MD/TXT/HTML 分块处理（`MDBRIDGE_STREAM_CONVERT`） |
| 子进程隔离 | `MDBRIDGE_CONVERT_ISOLATE`：native 崩溃时 worker 隔离，失败则 in-process 回退 |
| 批量并发 | `MDBRIDGE_BATCH_WORKERS` 控制并行 worker |
| 启动预热 | docling / WeasyPrint 可选 warmup（`MDBRIDGE_WARMUP_ON_STARTUP`） |

---

## 架构

```
                    ┌─────────────────────────────────────┐
                    │  CLI / FastAPI / Vue Web UI         │
                    └─────────────────┬───────────────────┘
                                      │ convert()
                    ┌─────────────────▼───────────────────┐
                    │  Registry：importer / exporter 插件   │
                    └─────────────────┬───────────────────┘
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
   anydoc_bridge              native importers              ocr_engine
   (Rust → mdbridge_native)   docx/pdf/md/html/…           MinerU / Paddle / docling
          │                           │                           │
          └───────────────────────────┼───────────────────────────┘
                                      ▼
                         DocumentModel（AST）
                    Heading / Paragraph / Table+span
                    Comment / Footnote / Revision / PivotTable …
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          ▼                           ▼                           ▼
      md/docx/pdf/html            epub/mobi/azw3              xlsx/json/rtf/latex
```

AST 节点与手写示例见 [`docs/document-model.md`](docs/document-model.md)。

---

## 快速开始

### Docker Compose（推荐）

```bash
git clone <repo-url> md-bridge && cd md-bridge
cp .env.example .env
docker compose up --build
```

| 服务 | 地址 |
|------|------|
| Web UI | http://localhost:8080 |
| OpenAPI | http://localhost:8080/docs |
| 后端直连 | http://localhost:8000 |

**镜像分层**

```bash
# 瘦镜像（无 docling/torch，MD↔DOCX/PDF 主路径）
docker compose -f docker-compose.yml -f docker-compose.slim.yml up --build

# OCR 叠加（开发）
docker compose -f docker-compose.yml -f docker-compose.ocr.yml up --build

# 路径含非 ASCII 时避免 Docker 上下文问题
bash scripts/docker_build_safe.sh
```

默认 `INSTALL_NATIVE=1`：多阶段 Dockerfile 编译 `mdbridge_native`。

### pip / 源码

```bash
pip install md-bridge                    # PyPI：纯 Python，不含 Rust 扩展
cd backend && pip install -e ".[dev]"    # 开发安装

# 高保真 Office 导入（源码 / 企业路径）
bash scripts/build_native.sh
```

### 本地开发

**要求：** Python 3.11+

```bash
# macOS 系统依赖（PDF + OCR）
brew install pango gdk-pixbuf libffi tesseract

cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
./start.sh                               # API :8000

cd frontend && npm install && npm run dev  # UI  :7173，/api 代理到 8000
```

---

## 配置

复制 [.env.example](.env.example) → `.env`。常用项：

| 变量 | 说明 | 默认 |
|------|------|------|
| `MDBRIDGE_MAX_UPLOAD_MB` | 单文件上限（MB） | `100` |
| `MDBRIDGE_API_KEY` / `MDBRIDGE_API_KEYS` | 鉴权（空=关闭） | 空 |
| `MDBRIDGE_OCR_ENABLED` | 扫描 PDF OCR | `false`（开发）；企业 compose 默认 `true` |
| `MDBRIDGE_OCR_ENGINE` | `auto` / `mineru` / `paddleocr_vl` / `docling` | `auto` |
| `MDBRIDGE_PDF_ENGINE` | PDF 导入引擎 | `auto` |
| `MDBRIDGE_ANYDOC_ENABLED` | 内置 anydoc 总开关 | `true` |
| `MDBRIDGE_ANYDOC_FAST_MARKDOWN` | MD 目标快通道 | `true` |
| `MDBRIDGE_JOB_RESULT_DIR` | 异步任务结果目录 | `data/job_results` |
| `MDBRIDGE_OBJECT_STORAGE_URI` | 结果归档 `file://` / `s3://` | 空 |
| `MDBRIDGE_HTML_KATEX` | HTML 导出注入 KaTeX | `true` |
| `MDBRIDGE_CONVERT_ISOLATE` | 转换子进程隔离 | 见 `.env.example` |

完整列表与 enterprise 变量见 `.env.example`、`enterprise/.env.example`。

---

## CLI

```bash
mdbridge convert input.docx output.md
mdbridge convert input.md output.pdf --pdf-font "Noto Sans CJK SC" --pdf-toc
mdbridge convert input.md output.docx --docx-theme academic

mdbridge batch ./inbox ./outbox --from docx --to md -r
mdbridge formats
mdbridge machine-id          # 企业一机一码
```

PDF/DOCX 选项与 HTTP API 字段一致。

---

## Python SDK

```python
from app.core.converter import convert

with open("report.docx", "rb") as f:
    md_bytes = convert(f.read(), "docx", "md", filename="report.docx")
```

手写 AST、插件注册见 [`docs/document-model.md`](docs/document-model.md)；完整示例 [`examples/sdk_demo.py`](examples/sdk_demo.py)。

---

## HTTP API（摘要）

| 端点 | 用途 |
|------|------|
| `POST /api/convert` | 单文件 multipart 转换 |
| `POST /api/convert/batch` | 多文件 → ZIP（失败项写入 `errors.json`） |
| `POST /api/jobs` | 异步任务；可选 `callback_url` Webhook |
| `GET /api/jobs/{id}` | 轮询状态 |
| `GET /api/jobs/{id}/result` | 下载结果 |
| `GET /api/health` | 健康 + OCR/anydoc 就绪 |
| `GET /api/metrics` | Prometheus 指标 |

```bash
curl -X POST http://localhost:8000/api/convert \
  -F "file=@sample.docx" -F "source_format=docx" -F "target_format=md" \
  -o output.md
```

错误体统一 JSON：`code` / `message` / `status_code`（如 `corrupted_file`、`ocr_unavailable`、`unsupported_format`）。

---

## 项目结构

```
md-bridge/
├── backend/                 # FastAPI + 转换内核
│   ├── app/
│   │   ├── core/            # DocumentModel, convert(), registry, jobs, metrics
│   │   ├── importers/       # docx, pdf, md, anydoc_bridge, ocr_engine, …
│   │   ├── exporters/       # md, docx, pdf, html, xlsx, table_span, …
│   │   └── api/             # routes, auth, rate_limit
│   ├── tests/               # 366+ 单元/集成/金样测试
│   ├── Dockerfile           # 多阶段：native + 运行时
│   └── pyproject.toml
├── frontend/                # Vue3 + Ant Design Vue
├── native/anydoc/           # vendored Rust → mdbridge_native._doc
├── enterprise/              # 私有化交付、验收、OCR、Prometheus compose
├── docs/                    # document-model.md, ops/prometheus/
├── scripts/                 # build_native.sh, sync_anydoc.sh, docker_build_*.sh
├── examples/                # sdk_demo.py, sample.md
├── docker-compose*.yml
├── ENTERPRISE.md            # 企业能力边界一页纸
└── .env.example
```

---

## 开发

```bash
cd backend

# Lint
ruff check app

# 全量回归（默认跳过 @integration OCR  live 测试）
pytest -q --tb=short -m "not integration"

# 全量含 integration 标记
pytest -q

# 前端
cd ../frontend && npm run build
```

CI（`.github/workflows/ci.yml`）覆盖 backend 测试、native 编译 smoke、前端 build；nightly 跑更重矩阵。

---

## 边界与已知限制

1. **PDF 非可编辑格式** — 只能还原语义结构，无法 100% 复刻原版式。
2. **数字 PDF** — `MDBRIDGE_PDF_ENGINE=auto` 优先本地文本层；无文本层才走 OCR/docling。
3. **扫描 PDF** — 需 OCR 可选依赖与模型；见 [`enterprise/OCR.md`](enterprise/OCR.md)。
4. **复杂 Word** — 页眉页脚、动画、DRM 等部分特性为 PARTIAL 或 ACCEPTED 边界。
5. **PyPI vs Docker** — PyPI 包不含 `mdbridge_native`；`doc`/`ppt`/`xls` 等需 Docker 或 `build_native.sh`。
6. **协议** — 禁止打包 Pandoc（GPL-2）；依赖仅 MIT / BSD / Apache-2.0。anydoc 上游 hosted OCR 已在 vendoring 时裁剪。

能力评估报告（与代码/CI 对齐）：[`mdbridge_capability_eval_report.html`](mdbridge_capability_eval_report.html)

---

## 协议

| 组件 | 协议 |
|------|------|
| md-bridge 自研代码 | **MIT** |
| anydoc（`native/anydoc/`） | MIT |
| FastAPI, Typer, markdown-it-py, python-docx, WeasyPrint 等 | MIT / BSD-3 |

详见 [LICENSE](LICENSE)。

---

## 企业版 / 商业支持

开源 MIT 可自由自托管。企业交付（离线镜像、预置 OCR 模型、SLA、一机一码、验收清单）见：

- [`ENTERPRISE.md`](ENTERPRISE.md) — 能力边界
- [`enterprise/DELIVERY.md`](enterprise/DELIVERY.md) — 交付总览
- [`enterprise/OCR.md`](enterprise/OCR.md) — 扫描双引擎
- [`enterprise/ACCEPTANCE.md`](enterprise/ACCEPTANCE.md) — 验收 B1–B11

| 形态 | 说明 |
|------|------|
| 开源自托管 | MIT，功能完整，自行运维 |
| 企业离线包 | 预置模型与 CJK 字体、安装脚本、授权与 SLA 模板 |
| 多租户 SaaS 预备 | `MDBRIDGE_API_KEYS` + `daily_quota` + `/api/usage` |
