# AduPort

[![CI](https://github.com/GavinWang2026/aduport/actions/workflows/ci.yml/badge.svg)](https://github.com/GavinWang2026/aduport/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/aduport.svg)](https://pypi.org/project/aduport/)

**本地私有化文档交换枢纽** — 把 Office、PDF、电子书、表格、标记语言等文件，经统一 AST 双向转换为 MD / DOCX / PDF / HTML / EPUB / XLSX / ODT / ODS / ODP 等 **18 种目标格式**，另有 RAG 导出模式（不是文件格式）。提供 Python SDK、CLI、FastAPI 与 Vue3 Web UI；默认纯本地处理，不依赖第三方云端 API。

| 指标 | 数值 |
|------|------|
| 包版本 | **0.16.0** |
| 源格式 | **36** |
| 目标格式 | **18** |
| 导出模式 | **`rag`**（`aduport.rag_elements`，不计入目标格式） |
| 后端测试 | **1193** collected（`pytest --collect-only -q -m "not integration"`；另有 6 条 `@integration`，合计 1199） |

---

## 程序是做什么的

AduPort 不是「又一个格式转换脚本」，而是一套**可部署的文档交换平台**：

1. **解析（Import）** — 把异构二进制/文本文件读入统一的 `DocumentModel`（AST）
   - DOCX：native 优先抽取 `w:drawing`/`a:blip` 图片与 List Bullet；`anydoc_formats` 默认不含 `docx`（可选手动加回快通道）
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
| **产品壳** | `frontend/`、`backend/aduport/api/`、`backend/aduport/cli.py` | Web 上传/预览、REST、Typer CLI、鉴权限流 |
| **转换内核** | `backend/aduport/core/`、`importers/`、`exporters/` | AST、`convert()`、注册表、Job、缓存、指标 |
| **原生加速** | `native/anydoc/` → `aduport_native._doc` | Rust 解析：独占 Office/ODF、高保真表格/列表/公式 |

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
| **docling**（可选） | 富结构 Office / PDF | `pip install aduport[office]`；离线模型 |
| **OCR**（可选） | 扫描 PDF / 图片页 | MinerU、PaddleOCR-VL；见 `enterprise/OCR.md` |

**anydoc 路由规则**（`ADUPORT_ANYDOC_*`）：

- **独占格式** — 无原生 importer 时，启用 anydoc 即接管
- **共享格式** — anydoc 优先，原生 fallback；**`xlsx` 默认留原生**（表头行更准）
- **快通道** — 目标 = `md` 时直出 GFM，跳过 Python AST 构建
- **PDF→MD** — `ADUPORT_ANYDOC_PDF_MARKDOWN`（默认开）：文本 PDF 走 pdf-inspector；扫描页回落 OCR/docling；PDF **永不**走 anydoc 模型导入

### 导出保真要点

- **表格 span**：HTML/PDF/DOCX/PPTX/ODT/xlsx/JSON/MD/TXT/LaTeX/RTF 共 11 路；MD/LaTeX/RTF import roundtrip 已接
- **语义节点**：Comment、Footnote、Revision、ContentControl、TextBox、PivotTable 可进 HTML/EPUB/DOCX/PDF/PPTX/XLSX/JSON
- **数学**：MathBlock → HTML（KaTeX CDN）、LaTeX、PDF
- **PDF**：数字版优先 `pypdfium2` 文本层；双栏启发式；WeasyPrint 排版导出

### 性能与可靠性

| 机制 | 配置 / 位置 |
|------|-------------|
| anydoc 快通道 | `ADUPORT_ANYDOC_FAST_MARKDOWN` |
| PDF→MD 快通道 | `ADUPORT_ANYDOC_PDF_MARKDOWN` |
| 结果缓存 | `ADUPORT_CACHE_*`（LRU + gzip） |
| 大文件流式 | `ADUPORT_STREAM_CONVERT` |
| native 崩溃隔离 | `ADUPORT_CONVERT_ISOLATE`（worker 失败 in-process 回退） |
| 批量并发 | `ADUPORT_BATCH_WORKERS` |
| 启动预热 | `ADUPORT_WARMUP_ON_STARTUP` |

---

## 格式一览

| 方向 | 格式 |
|------|------|
| **源（36）** | `md` `txt` `html` `docx` `doc` `docm` `pdf` `pptx` `ppt` `pps` `pot` `pptm` `ppsx` `ppsm` `xlsx` `xls` `xlsb` `xlsm` `ods` `odp` `csv` `odt` `rtf` `epub` `mobi` `azw3` `kf8` `latex` `json` `msg` `ipynb` `zip` `png` `jpg` `rss` `atom` |
| **目标（18）** | `md` `txt` `html` `docx` `pdf` `pptx` `epub` `mobi` `azw3` `kf8` `latex` `csv` `odt` `ods` `odp` `json` `xlsx` `rtf` |
| **导出模式** | `rag` |

`pages` / `numbers` / `keynote` 已摘牌：导入直接报 `UnsupportedFormatError`。真实 iWork '13+ 正文在 Snappy 压缩的 IWA 里，明文样本上的正则不能当成支持。解析代码仍在 `backend/aduport/importers/iwork_importer.py`。

`rag`：导出模式，不是文件格式。写出 `aduport.rag_elements` JSON（切块 / element_id / 可选 embedding），见 [`enterprise/RAG.md`](enterprise/RAG.md)。`convert()` 与 API 仍接受 `rag` / `elements` / `rag_elements` / `rag-json`。

`pdf` / `rtf` 的自有文件再导入会保留标题、表格和图片。`csv` / `txt` 只承诺文字与结构标记（非排版保真）：默认把标题/列表/正文写在 `_CONTENT` 段（Excel 打开是 Markdown 源码列）；需要纯表格时设 `csv_table_only=true`（HTTP Form / CLI `--csv-table-only` / Python `convert(..., csv_table_only=True)`）。图片只写成 `[Image: 说明]`，不内嵌字节。`structure_limits` 现在是空的。别人做的 PDF、没有 `_CONTENT` 的 CSV、以及不成 Markdown 的纯文本，仍可能只剩段落。

PDF 导出默认 `ADUPORT_PDF_FONT_COMPACT=true`，优先 Songti/STSong 等可子集化中文字体；短中文文档体积可从 ~150KB 落到 ~20KB 量级（无 Songti 时仍回退 Noto/系统字体）。

图片源（`png` / `jpg`）默认只嵌入原图。未启用 OCR 时正文和导出警告都会写明「未启用 OCR，仅嵌入图片」。`aduport doctor` 只有在某个 OCR 引擎真正可用时才把 OCR 标为 ready。

运行时列表：`GET /api/formats` · `aduport formats` · 源码 `backend/aduport/core/converter.py`

---

## 快速开始

### 一键启动（推荐 · 双击即可）

在 Finder 中打开本仓库文件夹，**双击**：

| 文件 | 作用 |
|------|------|
| **`一键启动.command`** | 启动服务并自动打开浏览器（http://127.0.0.1:8081） |
| **`一键停止.command`** | 停止服务 |

首次若提示「无法打开」：右键 → **打开** → **仍要打开**。

前提：已安装并启动 [Docker Desktop](https://www.docker.com/products/docker-desktop/)。首次启动会构建镜像，可能需数分钟。

Windows 可双击 `一键启动.bat`（需 Docker + Git Bash）。

也可在终端运行：

```bash
./start.sh                 # 等同于双击一键启动
./start.sh --stop          # 停止
./start.sh --local         # 无 Docker 时本机开发模式
```

### Docker Compose（手动）

```bash
cp .env.example .env
docker compose up --build
```

```bash
# 瘦镜像（无 docling/torch）
docker compose -f docker-compose.yml -f docker-compose.slim.yml up --build

# OCR 叠加
docker compose -f docker-compose.yml -f docker-compose.ocr.yml up --build

# 非 ASCII 路径安全构建
bash scripts/docker_build_safe.sh
```

默认 `INSTALL_NATIVE=1`：Dockerfile 多阶段编译 `aduport_native`。

### pip / 源码

```bash
pip install aduport                 # PyPI：纯 Python，不含 Rust 扩展
cd backend && pip install -e ".[dev]"

bash scripts/install_native_wheel.sh --release   # 预编译 native（无需 Rust）
# bash scripts/build_native.sh                   # 或源码编译
# bash scripts/build_native.sh --prebuilt        # 先试预编译，失败再编译
```

### 本地开发

Python **3.11+** · macOS 建议 `brew install pango gdk-pixbuf libffi tesseract`（WeasyPrint PDF 导出依赖）。

启动后看日志是否出现 `PDF export: WeasyPrint ok`；或运行：

```bash
aduport doctor          # WeasyPrint / native / OCR / Redis / License 自检
```

`GET /api/health/detail` 的 `pdf_export.install_hint` / `capabilities.*.install_hint` 亦给出安装指引。

```bash
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && ./start.sh
```

`pip install -e ".[dev]"` 会安装控制台入口 `aduport` 和 `aduport-mcp`。没做这一步时，这两个命令不在开发虚拟环境里，请在 `backend` 目录用 `python -m aduport.cli`。

```bash
cd frontend && npm install && npm run dev   # :7173，/api → :8000
```

多 worker 限流：设置 `ADUPORT_REDIS_URL`，否则内存限流仅单进程有效（health `rate_limit.backend`）。

---

## 配置

复制 [.env.example](.env.example) → `.env`。常用项：

| 变量 | 说明 |
|------|------|
| `ADUPORT_API_KEY` / `ADUPORT_API_KEYS` | 鉴权（空=关闭） |
| `ADUPORT_ANYDOC_ENABLED` | 内置 anydoc 总开关 |
| `ADUPORT_ANYDOC_FAST_MARKDOWN` | MD 目标快通道 |
| `ADUPORT_ANYDOC_PDF_MARKDOWN` | 文本 PDF→MD（扫描回落 OCR） |
| `ADUPORT_OCR_ENABLED` / `ADUPORT_OCR_ENGINE` | 扫描 OCR |
| `ADUPORT_PDF_ENGINE` | PDF 导入：`auto` / `text` / `docling` / … |
| `ADUPORT_JOB_RESULT_DIR` | 异步任务结果落盘 |
| `ADUPORT_OBJECT_STORAGE_URI` | 归档 `file://` / `s3://` |
| `ADUPORT_HTML_KATEX` | HTML 导出 KaTeX |
| `ADUPORT_CONVERT_ISOLATE` | 子进程隔离 |

完整项见 `.env.example`、`enterprise/.env.example`。

---

## CLI

```bash
aduport convert input.docx output.md
aduport convert input.md output.pdf --pdf-font "Noto Sans CJK SC" --pdf-toc
aduport batch ./inbox ./outbox --from docx --to md -r
aduport formats
aduport machine-id
```

---

## Python SDK

```python
from aduport.core.converter import convert

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
AduPort/
├── backend/                 # 转换内核 + API + CLI
│   ├── app/core/            # AST, convert, jobs, metrics, security
│   ├── app/importers/       # anydoc_bridge, docx, pdf, ocr, …
│   ├── app/exporters/       # md, docx, pdf, xlsx, table_span, …
│   ├── tests/               # 1193 项非 integration 回归 + 金样 + OCR fixtures
│   └── Dockerfile           # 多阶段 native + 运行时
├── frontend/                # Vue3 Web UI
├── native/anydoc/           # vendored Rust → aduport_native._doc
├── enterprise/              # 私有化交付、验收、OCR、Prometheus compose
├── docs/                    # document-model.md, ops/prometheus/
├── scripts/                 # build_native, install_native_wheel, pack_native_wheels, sync_anydoc
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

企业能力入口：[`ENTERPRISE.md`](ENTERPRISE.md) · [`enterprise/README.md`](enterprise/README.md)

---

## 边界与限制

1. **PDF** — 语义还原，非像素级复刻排版。
2. **数字 PDF** — `auto` 优先本地文本层；无文本层才 OCR/docling。
3. **扫描 PDF** — 需 OCR 可选依赖；见 [`enterprise/OCR.md`](enterprise/OCR.md)。
4. **PyPI vs native** — `aduport` PyPI 不含 `aduport_native`；`doc`/`ppt`/`xls` 等用 Docker、**预编译** `scripts/install_native_wheel.sh --release`，或 `build_native.sh`。
5. **协议** — 禁止打包 Pandoc（GPL-2）；依赖 MIT/BSD/Apache-2.0 only。

---

## 协议

| 组件 | 协议 |
|------|------|
| AduPort | **Apache-2.0** |
| anydoc（`native/anydoc/`） | MIT（上游 firecrawl/anydoc，版权归 Sideguide Technologies Inc.，随目录保留） |

详见 [LICENSE](LICENSE) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。Apache-2.0 第 6 条不含商标授权，“AduPort / 阿渡港”名称与标识不随代码授权。

---

## 企业版

- [`ENTERPRISE.md`](ENTERPRISE.md) — 能力边界
- [`enterprise/README.md`](enterprise/README.md) — OCR / RAG / 运维脚本
- [`enterprise/OCR.md`](enterprise/OCR.md) — 扫描双引擎

| 形态 | 说明 |
|------|------|
| 开源自托管 | Apache-2.0，功能完整 |
| 企业私有化 | License、OCR、可观测 profile |
| 多租户预备 | `ADUPORT_API_KEYS` + `daily_quota` + `/api/usage` |
