# AduPort Python SDK

本地文档双向转换库的**官方 Python SDK**。一条依赖、三行代码，把 `docx / pdf / md / epub / pptx / odt / xlsx / html / …`（39 源 / 19 目标）的转换能力接入你的程序，**文件全程不出本机**。

---

## 1. 安装

```bash
# 方式一：直接安装本目录下的 wheel（离线可用）
pip install dist/aduport-0.16.0-py3-none-any.whl

# 方式二：从 PyPI 安装（发布后）
pip install aduport

# 可选能力（按需叠加）
pip install "aduport[images]"     # 图片导入
pip install "aduport[office]"     # docling 高保真 Office 解析
pip install "aduport[embed]"      # 向量 / RAG
pip install "aduport[ocr]"        # OCR 双引擎（重，建议 GPU 主机）
```

> 要求 Python ≥ 3.11。导入名是 `aduport`（产品名为 AduPort）。

---

## 2. 快速上手

```python
from aduport import convert

# 内存 → 内存：字节进，字节出
md = b"# 标题\n\n- 项目 A\n- 项目 B\n\n**加粗**"
html = convert(md, "md", "html")              # Markdown → HTML
docx = convert(md, "md", "docx")              # Markdown → Word
pdf  = convert(md, "md", "pdf")               # Markdown → PDF

# 文件 → 文件
with open("报告.docx", "rb") as f:
    source = f.read()
result = convert(source, "docx", "md", filename="报告.docx")
open("报告.md", "wb").write(result)
```

---

## 3. 公共 API 清单

| 符号 | 类型 | 稳定性 | 说明 |
|------|------|--------|------|
| `convert` | `(bytes, str, str, ...) -> bytes` | ✅ 稳定 | 单文档转换，核心入口 |
| `convert_directory` | `(Path, Path, str, str, ...) -> BatchResult` | ✅ 稳定 | 目录批量转换（线程池） |
| `SUPPORTED_SOURCE_FORMATS` | `frozenset[str]` | ✅ 稳定 | 39 种源格式 |
| `SUPPORTED_TARGET_FORMATS` | `frozenset[str]` | ✅ 稳定 | 19 种目标格式 |
| `SUPPORTED_FORMATS` | `frozenset[str]` | ✅ 稳定 | 源 ∪ 目标 |
| `AduPortError` 及 9 个子类 | `Exception` | ✅ 稳定 | 统一异常契约（见 §6） |
| `DocumentModel` | `pydantic.BaseModel` | ✅ 稳定* | 中间 AST，可手工构建后导出（节点集合只增不破） |
| `BatchResult` / `BatchItemResult` | `dataclass` | ✅ 稳定 | `convert_directory` 的返回契约 |
| `capabilities` | `() -> dict` | ✅ 稳定 | 运行时能力检测（§5），键集合已固定 |
| `tier` | `() -> str` | ✅ 稳定 | 当前授权层级提示：`enterprise/native/free`（§5） |
| `__version__` | `str` | ✅ 稳定 | 版本号（单源，CLI/API 共用） |

> **稳定性契约**：上表所有 ✅ 符号均受 SemVer 保护——同一次大版本内不做破坏性变更。带 `*` 的 `DocumentModel`：类与其现有字段稳定可用，AST 节点类型集合随格式支持持续扩展，**新增节点/字段视为加法而非破坏**。推荐一律从 `from aduport import ...` 导入，不要 reach into `aduport.core.*` 内部模块——内部路径不受 SemVer 保护。

---

## 4. 完整示例

本目录 `examples/sdk_demo.py` 是可运行示例，覆盖内存转换、文件转换、批量、AST 构建、能力检测五类场景：

```bash
cd examples
python sdk_demo.py
```

### 4.1 PDF 定制

```python
result = convert(
    source, "md", "pdf", filename="a.md",
    pdf_font_family="Georgia, serif",
    pdf_custom_css="h1 { color: #1a73e8; }",
    pdf_page_size="A4",
    pdf_orientation="portrait",
)
```

### 4.2 批量转换

```python
from aduport import convert_directory

result = convert_directory("docs_in", "docs_out", "docx", "md", recursive=True)
print(f"total={result.total} ok={result.succeeded} fail={result.failed}")
for item in result.items:
    if not item.success:
        print(f"  ✗ {item.source}: {item.error}")
```

### 4.3 手工构建 AST（进阶）

```python
from aduport import DocumentModel
from aduport.core.document_model import Heading, Paragraph, Table
from aduport.exporters.docx_exporter import export_docx

model = DocumentModel(
    title="演示",
    nodes=[Heading(level=1, text="标题"), Paragraph(text="正文"), Table(rows=[["a", "b"]])],
)
docx = export_docx(model, theme="minimal")
```

---

## 5. 能力检测与分层授权

`capabilities()` 返回当前环境的运行时能力，是「开源免费 / native 加速 / 企业版」分层的地基：

```python
from aduport import capabilities, tier

caps = capabilities()
# {'version': '0.16.0', 'native': False, 'native_version': None,
#  'ocr_mineru': False, 'ocr_paddle': False, 'embed': False,
#  'office_docling': False, 'images_pillow': True}

print(tier())   # 'free' | 'native' | 'enterprise'
```

| 层级 | 判定 | 能力 |
|------|------|------|
| **free**（MIT 免费） | 默认 | 全功能转换，纯 Python 实现（自研 OOXML / PDF 文本层） |
| **native**（商业授权） | 装了 `aduport-native` 轮子 | Rust anydoc 引擎接管 docx/xls/odt/rtf 等，速度与保真度显著提升 |
| **enterprise**（一机一码） | native + OCR/embed 重能力 | 叠加 mineru/paddle OCR、fastembed 向量、docling 高保真 |

> `tier()` 仅是**探测提示**，不是授权边界。真正的授权强制在 `license` 模块（HMAC 一机一码），与 SDK 能力分层正交。

---

## 6. 异常契约

所有转换错误都抛出 `AduPortError` 子类，捕获一个基类即可兜底：

```python
from aduport import convert, AduPortError, UnsupportedFormatError, CorruptedFileError

try:
    convert(data, "docx", "md")
except UnsupportedFormatError as e:
    print("格式不支持：", e)          # 含可用格式清单
except CorruptedFileError as e:
    print("文件损坏：", e)
except AduPortError as e:              # 兜底
    print("转换失败：", e)
```

异常层级：`AduPortError` ← `UnsupportedFormatError / EmptyFileError / FileTooLargeError / UploadTimeoutError / CorruptedFileError / InvalidDocumentError / OcrUnavailableError / ExportError / MemoryBudgetError`。

---

## 7. 注意事项

1. **惰性导入**：`import aduport` 本身极轻量（不加载 weasyprint 等重依赖），公共符号在首次访问时才加载。CLI 的 `--version` 因此保持毫秒级。
2. **文件不进本机外**：SDK 是全离线库，转换过程不发起任何网络请求（除非你显式传 URL 导入）。
3. **大文件**：超限会抛 `FileTooLargeError`，默认上限见 `app/config.py`，可通过环境变量调整。
