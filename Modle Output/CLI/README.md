# AduPort CLI

本地文档双向转换器（PDF / DOCX / Markdown / ODT / EPUB / …）的命令行形态。

- **零上传**：全部在本机完成，文件不出你的电脑。
- **MIT 开源**：`pip install aduport` 即可用。
- 本包为**纯 Python 版**（不含 Rust 加速轮子与 OCR 重型依赖），一键安装零障碍。

---

## 1. 安装

### 方式 A：pip（推荐，macOS / Linux / Windows 通用）

```bash
pip install aduport
# 或从本地 wheel 安装（离线交付场景）
pip install ./dist/aduport-0.16.0-py3-none-any.whl
```

### 方式 B：一键脚本

```bash
bash install.sh        # macOS / Linux
install.bat            # Windows（双击或命令行）
```

### 可选增强依赖

```bash
pip install 'aduport[office]'     # docling：DOCX/PDF/PPTX 高保真导入
pip install 'aduport[images]'     # Pillow：图片压缩
pip install 'aduport[ocr]'        # OCR 扫描件（mineru + paddle，体积大）
pip install 'aduport[mcp]'        # MCP server（供 AI 客户端调用）
```

---

## 2. 命令速览

```text
aduport convert    单文件转换
aduport batch      目录批量转换
aduport formats    列出支持的格式
aduport serve      启动本地 FastAPI 服务（API + 浏览器 UI）
aduport doctor     依赖自检（WeasyPrint / native / OCR / Redis / License）
aduport machine-id      打印本机指纹（企业一机一码绑定用）
aduport license-issue   签发企业 License Key
aduport license-check   校验 License
aduport --version       显示版本
```

---

## 3. 常用示例

```bash
# 单文件：Markdown → DOCX
aduport convert input.md -t docx -o out.docx

# 单文件：DOCX → Markdown（自动识别源格式，可省略 -f）
aduport convert report.docx -t md -o report.md

# PDF → Markdown
aduport convert scan.pdf -t md -o scan.md

# Markdown → PDF（自定义纸张/页眉页脚）
aduport convert doc.md -t pdf --pdf-page-size A4 --pdf-header "机密" -o doc.pdf

# 目录批量：md → html
aduport batch ./src ./out -f md -t html --recursive

# 列出全部支持格式
aduport formats

# 起本地服务（浏览器打开 http://127.0.0.1:8000/docs）
aduport serve --host 127.0.0.1 --port 8000
```

---

## 4. 支持格式

运行 `aduport formats` 查看完整清单。核心覆盖：

- **源格式**：md / docx / pdf / pptx / xlsx / odt / epub / mobi / txt / html / csv / json / ipynb / rss / atom / msg / image 等 39 种
- **目标格式**：md / docx / pdf / html / latex / txt / pptx / odt / epub / csv / json / rag 等 19 种

> 说明：纯 Python 版不含 `aduport_native`（Rust 加速），`xlsb`、`ods`、二进制 `ppt` 等 anydoc-only 格式会返回 `unsupported_format`；共享格式正常转换。企业版 / Docker 镜像会编译进 native 加速。

---

## 5. 发布说明

本目录为「可交付」的 CLI 形态产物：

| 文件 | 用途 |
|------|------|
| `dist/aduport-0.16.0-py3-none-any.whl` | 可安装的 wheel（纯 Python） |
| `dist/aduport-0.16.0.tar.gz` | sdist 源码包 |
| `install.sh` / `install.bat` | 一键安装脚本 |
| `publish.sh` | PyPI 发布脚本（twine） |

重新打包（在 backend/ 目录）：

```bash
python -m build --sdist --wheel
```

发布到 PyPI（需配置 `TWINE_USERNAME` / `TWINE_PASSWORD`）：

```bash
./publish.sh
```

---

## 6. License

MIT
