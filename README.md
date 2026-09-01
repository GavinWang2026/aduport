# md-bridge

[![CI](https://github.com/crayfish-team/md-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/crayfish-team/md-bridge/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/md-bridge.svg)](https://pypi.org/project/md-bridge/)

本地私有化文档双向转换工具。支持 **PDF**、**DOCX**、**Markdown**、**ODT**、**RTF** 等格式相互转换，提供 Python SDK、CLI、FastAPI 后端与 Vue3 Web 前端，所有处理完全在本地完成，不调用第三方云端 API。

## 功能特性

- 统一中间文档模型 `DocumentModel`（AST），导入器与导出器解耦
- 输入：PDF、DOCX、Markdown、TXT、HTML、PPTX、EPUB、MOBI、ODT、RTF、**DOC**、**PPT**、**ODS**、**XLS/XLSB/XLSM**、CSV
- 输出：Markdown、DOCX、PDF、HTML、TXT、EPUB、PPTX、CSV、ODT、JSON、XLSX
- **anydoc 高保真导入引擎（内置，Rust/MIT，源码 vendored 于 `native/anydoc/`）**：`doc`/`odt`/`rtf`/`ppt`/`ods`/`xls`/`xlsb`/`xlsm`/`docm`/`pptm` 等经内置 anydoc 解析，获得**合并单元格表格**、**有序列表原始编号**、**OMML/MathML 公式（LaTeX）**、**嵌入图片字节**、**脚注/尾注**，以及此前原生不支持的二进制 Office / ODF 格式（`doc`/`ppt`/`xls`/`ods`/`odp`）
- anydoc-first + native-fallback 双通道：目标为 Markdown 时走 anydoc 直出快通道（零保真损失），其余走中间模型喂给既有导出器（docx/pdf/epub…）
- 单文件与**批量目录**转换（CLI + API）；异步任务队列（进度轮询）
- 可选 API-Key / Basic Auth、限流、审计日志、结果缓存与 Prometheus 指标
- 三种使用形态：Python SDK、命令行 CLI、Web UI + HTTP API
- Docker Compose 一键本地部署
- 扫描版 PDF 支持 OCR（依赖 Tesseract / docling 模型，可通过 `.env` 开关）
- 数字版 PDF：默认 `auto` 引擎走本地文本层（无需 Hugging Face），Hub 超时自动回退
- PDF 导出支持自定义字体 / CSS
- 标准化错误 JSON（损坏文件、格式错误、超限等）

## 协议说明

| 类别 | 协议 |
|------|------|
| 本项目自研代码 | **MIT** |
| FastAPI、Typer、docling、markdown-it-py、python-docx | MIT |
| WeasyPrint | BSD-3-Clause |
| pydantic-settings、python-multipart | MIT |
| anydoc（源码 vendored 于 `native/anydoc/`，非外部依赖） | MIT |

**硬性约束：**

1. 底层第三方依赖仅允许 MIT / BSD-3-Clause / Apache-2.0 协议库
2. **Pandoc（GPL-2）禁止打包**进程序或 Docker 镜像；代码中仅保留可选外部调用注释
3. 禁止引入 AGPL、GPL 传染协议组件

## 架构

```
PDF/DOCX/MD  →  [Importer]  →  DocumentModel (AST)  →  [Exporter]  →  MD/DOCX/PDF/...
                     │
        anydoc 桥接层（内置，Rust/MIT，源码 vendored，无网络/无 ML/无 LibreOffice）
        ├─ 快通道：目标=Markdown 时直接返回 anydoc 的 GFM（零保真损失）
        └─ 模型通道：映射到 DocumentModel 喂给既有导出器（含合并单元格/列表编号/公式）
```

`DocumentModel` 节点类型：`Heading`、`Paragraph`、`ListItem`、`Table`、`Image`、`CodeBlock`、`InlineMark`。

### anydoc 集成说明

anydoc 的 Rust 源码**已 vendored 进本仓库**（`native/anydoc/`，约 24k 行、MIT），通过 `app/importers/anydoc_bridge.py` 接入注册表（`app/core/registry.py` 的 `_wire_anydoc`）。它不是外部 PyPI 依赖，而是 md-bridge 自带的 native 组件（编译为 `mdbridge_native._doc` 扩展）。规则：

- **anydoc 独占格式**（无原生 importer）：`doc`、`ppt`、`pps`、`pot`、`xls`、`xlsb`、`xlsm`、`ods`、`odp`、`docm`、`pptm`、`ppsx`、`ppsm` —— 只要 `MDBRIDGE_ANYDOC_ENABLED=true` 即由 anydoc 接管；关闭时若无兼容原生兄弟（如 `docm`→`docx`）则降级。
- **共享格式**（有原生 importer）：`docx`、`odt`、`rtf`、`pptx`、`epub`、`csv` —— anydoc 优先，原生兜底；`xlsx` **默认留在原生**（原生推断表头行更准，anydoc 报 `header_rows=0`）。
- 通过 `MDBRIDGE_ANYDOC_FORMATS` 可缩小 anydoc 接管的共享格式范围（设为 `*` 全接管）。

## 快速开始

### 方式一：Docker Compose（推荐）

```bash
git clone <repo-url> md-bridge
cd md-bridge
docker compose up --build
# 瘦镜像（无 docling/torch，仅 MD↔DOCX/PDF 导出路径）：
# docker compose -f docker-compose.yml -f docker-compose.slim.yml up --build
```

- Web UI：http://localhost:8080
- API 文档：http://localhost:8080/docs
- 后端直连：http://localhost:8000

### 方式二：pip（PyPI / 本地包）

```bash
pip install md-bridge                 # 发布后
# 或从源码：
cd backend && pip install -e ".[dev]"
# 可选：pip install 'md-bridge[office,images]'
# 高保真 Office/EPUB/表格导入（anydoc 源码已 vendored，Rust/MIT，无外部依赖）：
#   编译内置 Rust 扩展（首次或更新源码后执行一次）：
bash scripts/build_native.sh
```

发布脚手架：`scripts/publish_dry_run.sh` 本地打 wheel；GitHub Release 触发 `.github/workflows/publish.yml`（Trusted Publishing）。

### 方式三：本地开发运行

**环境要求：** Python 3.11+

**系统依赖（WeasyPrint + OCR）：**

```bash
# macOS（PDF 导出依赖 Pango/GObject）
brew install pango gdk-pixbuf libffi tesseract
# 启动后端时需能找到 Homebrew 动态库，推荐：
#   cd backend && ./start.sh

# Debian/Ubuntu
sudo apt-get install tesseract-ocr tesseract-ocr-chi-sim \
  libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 fonts-noto-cjk
```

**安装后端：**

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"            # MD / DOCX-out / PDF-out
pip install -e ".[office,dev]"     # 另含 PDF/DOCX 导入（docling，体积较大）
```

**启动 API 服务：**

```bash
cd backend
./start.sh
# 或: uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

**启动前端（开发模式）：**

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:7173（Vite 已配置 `/api` 代理到 8000 端口）。

## 配置（.env）

复制 [.env.example](.env.example) 为 `.env`：

```bash
cp .env.example .env
```

| 变量 | 说明 | 默认 |
|------|------|------|
| `MDBRIDGE_MAX_UPLOAD_MB` | 上传/转换文件大小上限（MB） | `100` |
| `MDBRIDGE_OCR_ENABLED` | 扫描 PDF OCR 开关 | `true` |
| `MDBRIDGE_PDF_ENGINE` | PDF 导入：`auto` / `text` / `docling` | `auto` |
| `MDBRIDGE_HF_ENDPOINT` | Hugging Face 镜像（docling 模型） | `https://hf-mirror.com` |
| `MDBRIDGE_DEBUG` | 暴露内部错误详情 | `false` |
| `MDBRIDGE_CORS_ORIGINS` | CORS：`local` 白名单 / `*` / 逗号列表 | `local` |
| `MDBRIDGE_CORS_ALLOW_CREDENTIALS` | 是否允许 credentials（勿与 `*` 同开） | `false` |
| `MDBRIDGE_API_KEY` | 可选 API Key（设值后需 `X-API-Key`） | 空=关闭 |
| `MDBRIDGE_MAX_BATCH_FILES` | 批量最大文件数 | `20` |
| `MDBRIDGE_MAX_BATCH_TOTAL_MB` | 批量总大小上限（MB） | `200` |
| `MDBRIDGE_BATCH_WORKERS` | 批量并发 worker 数 | `3` |
| `MDBRIDGE_PDF_FONT_FAMILY` | PDF 默认字体族 | Noto Sans CJK SC ... |
| `MDBRIDGE_PDF_CUSTOM_CSS` | PDF 追加 CSS（自动过滤 url/@import） | 空 |
| `MDBRIDGE_ANYDOC_ENABLED` | anydoc 高保真导入总开关（关闭则完全退回原生，零行为变化） | `true` |
| `MDBRIDGE_ANYDOC_FORMATS` | anydoc 接管的**共享**格式（逗号分隔，`*`=全接管；独占格式不受此限） | `doc,docx,odt,rtf,ppt,pptx,epub,ods,odp,csv` |
| `MDBRIDGE_ANYDOC_FAST_MARKDOWN` | 目标=Markdown 时走 anydoc 直出快通道（零保真损失） | `true` |
| `MDBRIDGE_ANYDOC_FALLBACK` | anydoc 可恢复拒绝时回退原生 importer（关=直接报错） | `true` |
| `MDBRIDGE_ANYDOC_SNIFF` | 用 anydoc 内容嗅探修正无扩展名/错扩展名上传的格式识别 | `true` |

Docker Compose 会通过 `env_file: .env` 注入这些变量。

## CLI 使用

```bash
# 单文件转换
mdbridge convert input.docx output.md
mdbridge convert input.pdf output.md
mdbridge convert input.md output.docx
mdbridge convert input.md output.pdf --pdf-font "Georgia, serif"

# 显式指定格式
mdbridge convert input.bin output.md --from docx --to md

# 批量目录转换（输出到目标目录，可 -r 递归）
mdbridge batch ./inbox ./outbox --from docx --to md
mdbridge batch ./inbox ./outbox --from pdf --to md -r

# 查看支持格式
mdbridge formats
```

## Python SDK

```python
from app.core.converter import convert

with open("report.docx", "rb") as f:
    docx_bytes = f.read()

md_bytes = convert(docx_bytes, "docx", "md", filename="report.docx")

with open("report.md", "wb") as f:
    f.write(md_bytes)
```

完整示例见 [`examples/sdk_demo.py`](examples/sdk_demo.py)：

```bash
cd backend && pip install -e .
python ../examples/sdk_demo.py
```

## HTTP API

### POST `/api/convert`（单文件）

- Content-Type: `multipart/form-data`
- 字段：`file`、`source_format`、`target_format`；可选 `pdf_font_family`、`pdf_custom_css`
- 成功：文件流下载
- 失败：标准化 JSON

```bash
curl -X POST http://localhost:8000/api/convert \
  -F "file=@sample.docx" \
  -F "source_format=docx" \
  -F "target_format=md" \
  -o output.md
```

### POST `/api/convert/batch`（批量）

- 字段：`files`（多文件）、`source_format`、`target_format`；可选 PDF CSS 参数
- 成功（至少一个）：返回 ZIP；部分失败时 ZIP 内含 `errors.json`
- 全部失败：标准化 JSON（含每项错误）

```bash
curl -X POST http://localhost:8000/api/convert/batch \
  -F "files=@a.docx" \
  -F "files=@b.docx" \
  -F "source_format=docx" \
  -F "target_format=md" \
  -o converted_batch.zip
```

### 错误响应格式

```json
{
  "error": true,
  "code": "corrupted_file",
  "message": "DOCX 文件已损坏或不是有效的 Office 文档: bad.docx",
  "status_code": 422
}
```

常见 `code`：`unsupported_format`、`empty_file`、`file_too_large`、`corrupted_file`、`invalid_document`、`export_error`、`validation_error`、`batch_failed`。

OpenAPI 文档：http://localhost:8000/docs

## Web UI

- 拖拽/点击上传文件，支持批量
- 选择源格式与目标格式
- 转换完成后下载输出文件
- 目标格式为 Markdown 时右侧预览文本

## 边界与限制

1. **PDF 不是可编辑文档格式**：PDF 转其他格式只能还原语义结构，无法 100% 还原原始排版。
2. **数字版 PDF**：默认 `MDBRIDGE_PDF_ENGINE=auto`，有文本层时走本地 pypdfium2（不访问外网）。
3. **扫描版 PDF**：需 docling 模型 + Tesseract；可先运行 `./scripts/download_docling_models.sh`（默认经 hf-mirror 下载）。
4. **复杂 Word 特性**：页眉页脚、批注、内容控件等高级特性还原有限。
5. **Pandoc**：可作为可选外部工具自行安装，本项目不打包、不分发、不默认依赖。
6. **anydoc 为内置原生组件**：`doc`/`ppt`/`xls`/`ods`/`odp`/`xlsb` 等格式由 vendored 的 Rust 源码（编译为 `mdbridge_native._doc`）提供；未运行 `scripts/build_native.sh` 编译时，这些格式降级为 `unsupported_format`，其余格式行为与之前完全一致。anydoc 是纯 Rust（MIT），无网络调用、无 ML 模型、无 LibreOffice 依赖，满足私有化硬性约束；其上游 `ocr="hosted"` 联网分支已在 vendoring 时裁剪。

## 项目结构

```
md-bridge/
├── LICENSE
├── README.md
├── .env.example
├── docker-compose.yml
├── native/anydoc/         # vendored Rust 解析内核 → mdbridge_native._doc
├── scripts/
│   ├── build_native.sh    # maturin 编译内置扩展
│   └── sync_anydoc.sh     # 上游半自动同步
├── backend/
│   ├── Dockerfile         # 多阶段：编装 mdbridge_native + 运行时
│   ├── app/
│   │   ├── core/          # DocumentModel + convert() + batch + exceptions + registry
│   │   ├── importers/     # docx, pdf, md, **anydoc_bridge**
│   │   ├── exporters/     # md, docx, pdf (CSS/font)
│   │   ├── api/           # FastAPI routes
│   │   ├── config.py      # pydantic-settings / .env
│   │   └── cli.py
│   ├── main.py
│   └── pyproject.toml
├── frontend/              # Vue3 + Ant Design Vue
└── examples/
    └── sdk_demo.py
```

## 开发

```bash
# 后端 lint
cd backend && ruff check app

# 前端构建
cd frontend && npm run build
```

## License

MIT — 详见 [LICENSE](LICENSE)

## 商业支持 / 企业版

开源 MIT 版本可自由自托管。若需要**企业私有化交付**（离线镜像含 docling 模型与 CJK 字体、SLA、安装支持、商业授权书），请通过仓库 Issues / Discussions 留下联系方式，或邮件咨询维护团队。

交付包与验收清单见 [`enterprise/`](./enterprise/)（总览 [`enterprise/DELIVERY.md`](./enterprise/DELIVERY.md)，含 SLA / 授权 / 运维 / 报价边界）。
扫描件双引擎（MinerU / PaddleOCR-VL）见 [`enterprise/OCR.md`](./enterprise/OCR.md)。

| 形态 | 说明 |
|------|------|
| 开源自托管 | MIT，功能完整，自行运维 |
| 企业交付包 | 预置模型与中文排版、安装脚本、SLA 模板、授权文件 |
| 托管 SaaS | 路线图中（多租户 / 计量 / 计费），可用 `MDBRIDGE_API_KEYS` 先行隔离客户 |

相关环境变量：`MDBRIDGE_API_KEYS`、`MDBRIDGE_JOB_DB_PATH`、`MDBRIDGE_REDIS_URL`、`MDBRIDGE_VIRUS_SCAN`、`MDBRIDGE_DOCX_MAX_NODES`。

**DOCX 超大文档**：默认节点上限 `MDBRIDGE_DOCX_MAX_NODES=50000`，超出返回 `docx_too_large`；企业批量请分片，而非无限调高。
