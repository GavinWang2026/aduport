# md-bridge

[![CI](https://github.com/crayfish-team/md-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/crayfish-team/md-bridge/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/md-bridge.svg)](https://pypi.org/project/md-bridge/)

**本地私有化文档交换枢纽** — 把 Office、PDF、电子书、表格、标记语言等文件，经统一 AST 双向转换为 MD / DOCX / PDF / HTML / EPUB / XLSX 等 **16 种目标格式**。提供 Python SDK、CLI、FastAPI 与 Vue3 Web UI；默认纯本地处理，不依赖第三方云端 API。

| 指标 | 数值 |
|------|------|
| 包版本 | **0.16.0** |
| 源格式 | **29** |
| 目标格式 | **16** |
| 后端测试 | **374** collected（CI 默认跳过 live OCR `@integration`） |

---

## 程序是做什么的

md-bridge 不是「又一个格式转换脚本」，而是一套**可部署的文档交换平台**：

1. **解析（Import）** — 把异构二进制/文本文件读入统一的 `DocumentModel`（AST）
2. **变换（Transform）** — 在 AST 上可做 enrich（DOCX 批注/页眉）、路由（anydoc 快/慢通道）、OCR（扫描 PDF）
3. **生成（Export）** — 从同一 AST 写出多种目标格式，保证语义一致

```
  ┌─────────────── 使用面 ───────────────┐
  │  Web UI · CLI · HTTP API · Python SDK │
  └──────────────────┬───────────────────┘
                     │ convert() / batch / jobs
  ┌──────────────────▼───────────────────┐
  │  core/converter + registry（插件注册表） │
  └──────────────────┬───────────────────┘
                     │
     ┌───────────────┼───────────────┐
     ▼               ▼               ▼
 anydoc_bridge   native importers   ocr_engine
 (Rust 内置)     docx/pdf/md/…     MinerU/Paddle/docling
     └───────────────┼───────────────┘
                     ▼
            DocumentModel（AST）
     段落/表格(span)/批注/脚注/公式/透视…
                     │
     ┌───────────────┼───────────────┐
     ▼               ▼               ▼
 md/docx/pdf/html  epub/mobi/azw3   xlsx/json/rtf/latex
```

**典型场景**

| 场景 | 路径 |
|------|------|
| 合同 DOCX → 知识库 Markdown | `docx → md`（anydoc 快通道或 AST） |
| 技术文档 → 可打印 PDF | `md → pdf`（WeasyPrint + CJK 字体/CSS） |
| 批量 Office 目录归档 | CLI `batch` 或 API `/api/convert/batch` |
| 扫描 PDF 数字化 | OCR 双引擎（MinerU / PaddleOCR-VL，企业可选） |
| 私有化 API 网关 | Docker + API Key / 多租户 / Prometheus |

---

## 程序剖析

### 三层结构

| 层 | 目录 | 职责 |
|----|------|------|
| **产品壳** | `frontend/`、`backend/app/api/`、`backend/app/cli.py` | Web 上传/预览、REST、Typer CLI、鉴权限流 |
| **转换内核** | `backend/app/core/`、`importers/`、`exporters/` | AST、`convert()`、注册表、Job、缓存、指标 |
| **原生加速** | `native/anydoc/` → `mdbridge_native._doc` | Rust 解析：独占 Office/ODF、高保真表格/列表/公式 |

### 核心模块

| 模块 | 作用 |
|------|------|
| `core/converter.py` | 转换主入口：校验 → import → export；格式矩阵 `SUPPORTED_*` |
| `core/registry.py` | 懒加载 importer/exporter；`_wire_anydoc` 注入 Rust 桥 |
| `core/document_model.py` | AST 定义：20+ 节点类型（含 `TableCell` span、`PivotTable`、`Revision`…） |
| `importers/anydoc_bridge.py` | anydoc 快通道（GFM）与模型通道（→ AST） |
| `importers/ocr_engine.py` | 扫描 PDF：引擎链、fallback、就绪探测 |
| `exporters/table_span.py` | 11 路目标格式的表格合并单元格共享逻辑 |
| `core/jobs.py` + `job_results.py` | 异步任务、结果落盘、Webhook 回调 |
| `core/metrics.py` | Prometheus：转换量、anydoc 命中率、OCR 状态、错误码 |

### 导入引擎矩阵

| 引擎 | 覆盖 | 特点 |
|------|------|------|
| **anydoc**（Rust） | `doc` `ppt` `xls` `xlsb` `ods` `odp` 及宏变体；共享格式的优先路径 | 毫秒级；合并单元格、列表编号、OMML 公式；无网络 |
| **native Python** | `md` `html` `docx` `xlsx` `pdf`（文本层）等 | AST 深度 enrich；xlsx 表头推断；PDF charbox |
| **docling**（可选） | 富结构 Office / PDF | `pip install md-bridge[office]`；离线模型 |
| **OCR**（可选） | 扫描 PDF / 图片页 | MinerU、PaddleOCR-VL；见 `enterprise/OCR.md` |

**anydoc 路由规则**（`MDBRIDGE_ANYDOC_*`）：

- **独占格式** — 无原生 importer 时，启用 anydoc 即接管
- **共享格式** — anydoc 优先，原生 fallback；**`xlsx` 默认留原生**（表头行更准）
- **快通道** — 目标 = `md` 时直出 GFM，跳过 Python AST 构建

### 导出保真要点

- **表格 span**：HTML/PDF/DOCX/PPTX/ODT/xlsx/JSON/MD/TXT/LaTeX/RTF 共 11 路；MD/LaTeX/RTF import roundtrip 已接
- **语义节点**：Comment、Footnote、Revision、ContentControl、TextBox、PivotTable 可进 HTML/EPUB/DOCX/PDF/PPTX/XLSX/JSON
- **数学**：MathBlock → HTML（KaTeX CDN）、LaTeX、PDF
- **PDF**：数字版优先 `pypdfium2` 文本层；双栏启发式；WeasyPrint 排版导出

### 性能与可靠性

| 机制 | 配置 / 位置 |
|------|-------------|
| anydoc 快通道 | `MDBRIDGE_ANYDOC_FAST_MARKDOWN` |
| 结果缓存 | `MDBRIDGE_CACHE_*`（LRU + gzip） |
| 大文件流式 | `MDBRIDGE_STREAM_CONVERT` |
| native 崩溃隔离 | `MDBRIDGE_CONVERT_ISOLATE`（worker 失败 in-process 回退） |
| 批量并发 | `MDBRIDGE_BATCH_WORKERS` |
| 启动预热 | `MDBRIDGE_WARMUP_ON_STARTUP` |

---

## 格式一览

| 方向 | 格式 |
|------|------|
| **源（29）** | `md` `txt` `html` `docx` `doc` `docm` `pdf` `pptx` `ppt` `pps` `pot` `pptm` `ppsx` `ppsm` `xlsx` `xls` `xlsb` `xlsm` `ods` `odp` `csv` `odt` `rtf` `epub` `mobi` `azw3` `kf8` `latex` `json` |
| **目标（16）** | `md` `txt` `html` `docx` `pdf` `pptx` `epub` `mobi` `azw3` `kf8` `latex` `csv` `odt` `json` `xlsx` `rtf` |

运行时列表：`GET /api/formats` · `mdbridge formats` · 源码 `backend/app/core/converter.py`

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
| 后端 | http://localhost:8000 |

```bash
# 瘦镜像（无 docling/torch）
docker compose -f docker-compose.yml -f docker-compose.slim.yml up --build

# OCR 叠加
docker compose -f docker-compose.yml -f docker-compose.ocr.yml up --build

# 非 ASCII 路径安全构建
bash scripts/docker_build_safe.sh
```

默认 `INSTALL_NATIVE=1`：Dockerfile 多阶段编译 `mdbridge_native`。

### pip / 源码

```bash
pip install md-bridge                 # PyPI：纯 Python，不含 Rust 扩展
cd backend && pip install -e ".[dev]"

bash scripts/build_native.sh            # 高保真 Office 导入（源码/企业）
```

### 本地开发

Python **3.11+** · macOS 建议 `brew install pango gdk-pixbuf libffi tesseract`

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && ./start.sh

cd frontend && npm install && npm run dev   # :7173，/api → :8000
```

---

## 配置

复制 [.env.example](.env.example) → `.env`。常用项：

| 变量 | 说明 |
|------|------|
| `MDBRIDGE_API_KEY` / `MDBRIDGE_API_KEYS` | 鉴权（空=关闭） |
| `MDBRIDGE_ANYDOC_ENABLED` | 内置 anydoc 总开关 |
| `MDBRIDGE_ANYDOC_FAST_MARKDOWN` | MD 目标快通道 |
| `MDBRIDGE_OCR_ENABLED` / `MDBRIDGE_OCR_ENGINE` | 扫描 OCR |
| `MDBRIDGE_PDF_ENGINE` | PDF 导入：`auto` / `text` / `docling` / … |
| `MDBRIDGE_JOB_RESULT_DIR` | 异步任务结果落盘 |
| `MDBRIDGE_OBJECT_STORAGE_URI` | 归档 `file://` / `s3://` |
| `MDBRIDGE_HTML_KATEX` | HTML 导出 KaTeX |
| `MDBRIDGE_CONVERT_ISOLATE` | 子进程隔离 |

完整项见 `.env.example`、`enterprise/.env.example`。

---

## CLI

```bash
mdbridge convert input.docx output.md
mdbridge convert input.md output.pdf --pdf-font "Noto Sans CJK SC" --pdf-toc
mdbridge batch ./inbox ./outbox --from docx --to md -r
mdbridge formats
mdbridge machine-id
```

---

## Python SDK

```python
from app.core.converter import convert

with open("report.docx", "rb") as f:
    md_bytes = convert(f.read(), "docx", "md", filename="report.docx")
```

AST 详解：[`docs/document-model.md`](docs/document-model.md) · 示例：[`examples/sdk_demo.py`](examples/sdk_demo.py)

---

## HTTP API（摘要）

| 端点 | 用途 |
|------|------|
| `POST /api/convert` | 单文件转换 |
| `POST /api/convert/batch` | 批量 → ZIP |
| `POST /api/jobs` | 异步 + 可选 `callback_url` Webhook |
| `GET /api/health` | 健康 / OCR / anydoc 就绪 |
| `GET /api/metrics` | Prometheus 指标 |

```bash
curl -X POST http://localhost:8000/api/convert \
  -F "file=@sample.docx" -F "source_format=docx" -F "target_format=md" \
  -o output.md
```

错误 JSON 字段：`code` · `message` · `status_code`（如 `corrupted_file`、`ocr_unavailable`）。

可观测：[`docs/ops/prometheus/`](docs/ops/prometheus/)（Grafana 看板 + 告警规则）。

---

## 项目结构

```
md-bridge/
├── backend/                 # 转换内核 + API + CLI
│   ├── app/core/            # AST, convert, jobs, metrics, security
│   ├── app/importers/       # anydoc_bridge, docx, pdf, ocr, …
│   ├── app/exporters/       # md, docx, pdf, xlsx, table_span, …
│   ├── tests/               # 374 项回归 + 金样 + OCR fixtures
│   └── Dockerfile           # 多阶段 native + 运行时
├── frontend/                # Vue3 Web UI
├── native/anydoc/           # vendored Rust → mdbridge_native._doc
├── enterprise/              # 私有化交付、验收、OCR、Prometheus compose
├── docs/                    # document-model.md, ops/prometheus/
├── scripts/                 # build_native, sync_anydoc, docker_build_*
├── examples/                # sdk_demo.py, sample.md
└── docker-compose*.yml
```

---

## 开发与质量

```bash
cd backend
ruff check app
pytest -q -m "not integration"          # 日常回归
pytest -q                               # 含 OCR integration（需引擎）
cd ../frontend && npm run build
```

CI：`.github/workflows/ci.yml`（ruff、cov≥70%、native-build、前端 build）· nightly 金样矩阵。

企业验收清单：[`enterprise/ACCEPTANCE.md`](enterprise/ACCEPTANCE.md)

---

## 边界与限制

1. **PDF** — 语义还原，非像素级复刻排版。
2. **数字 PDF** — `auto` 优先本地文本层；无文本层才 OCR/docling。
3. **扫描 PDF** — 需 OCR 可选依赖；见 [`enterprise/OCR.md`](enterprise/OCR.md)。
4. **PyPI vs Docker** — PyPI 不含 `mdbridge_native`；`doc`/`ppt`/`xls` 等需 Docker 或 `build_native.sh`。
5. **协议** — 禁止打包 Pandoc（GPL-2）；依赖 MIT/BSD/Apache-2.0 only。

---

## 协议

| 组件 | 协议 |
|------|------|
| md-bridge | **MIT** |
| anydoc（`native/anydoc/`） | MIT |

详见 [LICENSE](LICENSE)。

---

## 企业版

- [`ENTERPRISE.md`](ENTERPRISE.md) — 能力边界
- [`enterprise/DELIVERY.md`](enterprise/DELIVERY.md) — 离线交付
- [`enterprise/OCR.md`](enterprise/OCR.md) — 扫描双引擎
- [`enterprise/ACCEPTANCE.md`](enterprise/ACCEPTANCE.md) — 验收 B1–B11

| 形态 | 说明 |
|------|------|
| 开源自托管 | MIT，功能完整 |
| 企业离线包 | 预置模型、SLA、一机一码 |
| 多租户预备 | `MDBRIDGE_API_KEYS` + `daily_quota` + `/api/usage` |
