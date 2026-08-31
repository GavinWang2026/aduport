# md-bridge

本地私有化文档双向转换工具。支持 **PDF**、**DOCX**、**Markdown** 之间的相互转换，提供 Python SDK、CLI、FastAPI 后端与 Vue3 Web 前端，所有处理完全在本地完成，不调用第三方云端 API。

## 功能特性

- 统一中间文档模型 `DocumentModel`（AST），导入器与导出器解耦
- 输入：PDF、DOCX、Markdown、TXT、HTML、PPTX、EPUB、MOBI
- 输出：Markdown、DOCX、PDF
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

**硬性约束：**

1. 底层第三方依赖仅允许 MIT / BSD-3-Clause / Apache-2.0 协议库
2. **Pandoc（GPL-2）禁止打包**进程序或 Docker 镜像；代码中仅保留可选外部调用注释
3. 禁止引入 AGPL、GPL 传染协议组件

## 架构

```
PDF/DOCX/MD  →  [Importer]  →  DocumentModel (AST)  →  [Exporter]  →  MD/DOCX/PDF
```

`DocumentModel` 节点类型：`Heading`、`Paragraph`、`ListItem`、`Table`、`Image`、`CodeBlock`、`InlineMark`。

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

### 方式二：本地 pip 运行

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

## 项目结构

```
md-bridge/
├── LICENSE
├── README.md
├── .env.example
├── docker-compose.yml
├── backend/
│   ├── app/
│   │   ├── core/          # DocumentModel + convert() + batch + exceptions
│   │   ├── importers/     # docx, pdf, md
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
