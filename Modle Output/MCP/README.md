# AduPort MCP Server

本地文档双向转换器（PDF / DOCX / Markdown / EPUB / PPTX / …）的 **MCP（Model Context Protocol）形态**——让 Claude、Cursor 等 AI 客户端把「文档转换」当成一个本地工具来调用，**文件与内容全程不出本机**。

- **零上传**：转换在本机完成，不经第三方 API。
- **4 个工具**：文件转换、内存文本转换、格式清单、能力探测。
- **默认锁死工作目录**：路径白名单 + 扩展名白名单 + 大小上限，安全默认开启。
- **MIT 开源**：`pip install 'aduport[mcp]'` 即可用。

---

## 1. 安装

要求 Python ≥ 3.11。

```bash
# 方式一：一键脚本（自动安装 aduport[mcp] 含 mcp SDK）
bash install.sh          # macOS / Linux
install.bat              # Windows

# 方式二：pip 直接安装
pip install 'aduport[mcp]'

# 方式三：本地 wheel 离线安装（交付场景）
pip install './dist/aduport-0.16.0-py3-none-any.whl[mcp]'
```

验证安装：

```bash
which aduport-mcp        # 入口命令（接入配置要用到它的绝对路径）
python smoke_test.py      # 端到端自检（见 §6）
```

> 入口有两个等价形式：`aduport-mcp`（console script）与 `python -m aduport.mcp_server`。

---

## 2. 工具一览

| 工具 | 作用 | 关键参数 |
|------|------|----------|
| `convert_document` | 本地**文件** → 目标格式 | `path`, `target_format`, PDF/DOCX 定制 |
| `convert_text` | **内存文本** → 目标格式（不落盘） | `content`, `source_format`, `target_format`, … |
| `list_formats` | 列出 39 源 / 19 目标格式 + 版本 | — |
| `capabilities` | 运行时能力（native / OCR / 向量） | — |

### 2.1 `convert_text` —— MCP 形态的核心价值

Agent 手里常握着「已经读到的内容」（用户粘贴文本、抓取的网页、生成的 Markdown/HTML），
不必先写盘再转换。`convert_text` 直接内存进、内存出：

```
content: "# 季度报告\n\n..."  +  target_format: "docx"
      ──▶  {ok, target_format: "docx", encoding: "base64", data: "<base64>"}
```

支持 `source_format`：`md` / `txt` / `html` / `latex` / `json` / `csv` 等文本源格式。

### 2.2 返回契约（统一 JSON）

所有转换工具**永不抛异常**，统一返回结构化对象：

```json
// 文本目标（md / txt / html / latex / csv / json / rag）
{ "ok": true, "target_format": "html", "mime": "text/html; charset=utf-8",
  "encoding": "utf-8", "size": 128, "data": "<正文文本>" }

// 二进制目标（docx / pdf / pptx / epub / odt / xlsx / …）
{ "ok": true, "target_format": "docx",
  "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "encoding": "base64", "size": 20480, "data": "<base64>",
  "note": "binary payload — base64-decode and write to a file (e.g. out.docx)" }

// 失败
{ "ok": false, "target_format": "docx", "error": "UnsupportedFormatError: …" }
```

Agent 拿到 `encoding: "base64"` 的结果后，解码并写入文件即可（宿主都有写文件工具）。

### 2.3 PDF / DOCX 定制参数

`convert_text` 与 `convert_document` 均支持：`pdf_page_size`（如 `A4`）、`pdf_header`、`pdf_footer`、`pdf_custom_css`、`docx_theme`。

---

## 3. 接入 AI 客户端

### 3.1 Claude Desktop

把 `configs/claude_desktop_config.json` 合并进
`~/Library/Application Support/Claude/claude_desktop_config.json`（Windows：`%APPDATA%\Claude\claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "AduPort": {
      "command": "/绝对路径/aduport-mcp",
      "args": [],
      "env": {
        "ADUPORT_MCP_ALLOWED_ROOTS": "/Users/YOURNAME/Documents"
      }
    }
  }
}
```

> `command` 必须填**绝对路径**（`which aduport-mcp` 查看），否则 Claude Desktop 找不到命令。改完重启 Claude Desktop。

### 3.2 Claude Code

项目根放 `.mcp.json`（内容同 `configs/claude_code_mcp.json`），或全局写入 `~/.claude.json` 的 `mcpServers` 段：

```bash
# 项目级（推荐，随仓库走）
cp configs/claude_code_mcp.json /你的项目/.mcp.json

# 全局注册
claude mcp add AduPort -- aduport-mcp
```

### 3.3 Cursor

把 `configs/cursor_mcp.json` 放入 `.cursor/mcp.json`，或在 Cursor 设置 → MCP → Add new MCP server 里逐项填写。

---

## 4. 安全模型

MCP 把「本机文件访问」暴露给 AI，默认采取**最小权限**：

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| 允许根目录 | `ADUPORT_MCP_ALLOWED_ROOTS` | 当前工作目录 | 逗号分隔的绝对路径；`*` 显式放开（不推荐） |
| 允许扩展名 | `ADUPORT_MCP_ALLOWED_EXTENSIONS` | 常见文档/图片 30 余种 | 白名单外一律拒绝 |
| 大小上限 | （沿用 `ADUPORT_MAX_UPLOAD_BYTES`） | 服务端默认 | 超限返回 `file too large` |

要点：
- 越出 `ALLOWED_ROOTS` 的路径 → 拒绝，不读文件。
- 白名单外的扩展名 → 拒绝。
- `convert_text` **不触碰文件系统**，天然无路径越权风险。

---

## 5. Transport（可选）

默认 `stdio`（本地单机）。团队共享场景可切到 HTTP：

```bash
ADUPORT_MCP_TRANSPORT=streamable-http aduport-mcp
# 或 sse
ADUPORT_MCP_TRANSPORT=sse aduport-mcp
```

> 远程 HTTP 部署必须自行加一层鉴权反代；本包不内置认证，公网裸奔有越权风险。

---

## 6. 端到端自检

```bash
python smoke_test.py
```

预期输出：

```text
[1/5] MCP initialize ................ OK
[2/5] list_tools ..................... ['capabilities', 'convert_document', 'convert_text', 'list_formats']
[3/5] convert_text (md -> html) ...... OK
[4/5] convert_text (md -> docx) ...... OK (NNNN bytes)
[5/5] list_formats .................... 39 源 / 19 目标
```

---

## 7. 目录结构

```text
MCP/
├── README.md                        # 本文档
├── configs/
│   ├── claude_desktop_config.json   # Claude Desktop 接入模板
│   ├── claude_code_mcp.json         # Claude Code 接入模板
│   └── cursor_mcp.json              # Cursor 接入模板
├── install.sh / install.bat         # 一键安装
└── smoke_test.py                    # 端到端自检
```

源码入口：`backend/aduport/mcp_server.py`（`aduport-mcp` 命令即指向它，随 `pip install aduport` 打包进 wheel，无需单独部署文件）。

---

## 8. 常见问题

- **Claude Desktop 报「找不到 command」** → `command` 要填 `which aduport-mcp` 的绝对路径，或 `python -m aduport.mcp_server` 的完整 python 路径。
- **报「path outside ADUPORT_MCP_ALLOWED_ROOTS」** → 目标文件不在白名单根目录下，扩大 `ADUPORT_MCP_ALLOWED_ROOTS` 或把文件放进白名单目录。
- **`xlsb` / 二进制 `ppt` 等转不了** → 这些是 anydoc-native 格式，纯 Python 版返回 `unsupported_format`；企业版 / Docker 镜像编译进 native 加速后可用（见 `capabilities` 的 `native` 字段）。
- **OCR 扫描件无文字** → 装 `aduport[ocr]`（mineru / paddle，体积大，建议 GPU 主机）。

---

## 9. License

MIT
