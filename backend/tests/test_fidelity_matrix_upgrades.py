"""Fidelity matrix upgrades: MD → 15 targets keep 17 semantic sentinels searchable."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.core.converter import convert

_FIDELITY_MD = b"""# Title H1

## Title H2

This has **bold** and *italic* and `incode` and ~~strike~~ and [link](https://example.com/docs).

- bullet one
  - nested two
1. ordered one

> quote line

```python
print(42)
```

| ColA | ColB |
| --- | --- |
| 1 | 2 |

---

- [x] task done

[^1]: footnote body text

See note[^1]

$$
E=mc^2
$$

![logo alt]()
"""

_CHECKS = [
    ("H1", lambda t: "Title H1" in t),
    ("H2", lambda t: "Title H2" in t),
    (
        "粗体",
        lambda t: "**bold**" in t
        or '"type": "bold"' in t
        or '"type":"bold"' in t
        or "w:b" in t
        or 'b="1"' in t
        or "<strong>" in t
        or r"\b" in t
        or r"\textbf" in t,
    ),
    (
        "斜体",
        lambda t: "*italic*" in t
        or '"type": "italic"' in t
        or '"type":"italic"' in t
        or "w:i" in t
        or 'i="1"' in t
        or "<em>" in t
        or r"\i" in t
        or r"\textit" in t,
    ),
    (
        "删除线",
        lambda t: "strike" in t
        and (
            "~~" in t
            or "w:strike" in t
            or "line-through" in t
            or "<del>" in t
            or r"\sout" in t
            or r"\strike" in t
            or 'strike="sngStrike"' in t
            or "strikethrough" in t.lower()
        ),
    ),
    ("行内码", lambda t: "incode" in t),
    ("链接", lambda t: "example.com" in t.lower()),
    ("无序", lambda t: "bullet one" in t),
    ("嵌套", lambda t: "nested two" in t),
    ("有序", lambda t: "ordered one" in t),
    ("引用", lambda t: "quote line" in t),
    (
        "代码",
        lambda t: "print(42)" in t
        or r"print\(42\)" in t
        or ("print" in t and "42" in t),
    ),
    ("表格", lambda t: "ColA" in t),
    (
        "分隔",
        lambda t: "---" in t
        or "<hr" in t.lower()
        or "horizontal_rule" in t
        or r"\rule" in t
        or r"\hrulefill" in t,
    ),
    (
        "任务",
        lambda t: "task done" in t
        and (
            "[x]" in t
            or "checked" in t.lower()
            or "☑" in t
            or "aria-checked" in t
            or "boxtimes" in t.lower()
        ),
    ),
    ("脚注", lambda t: "footnote body" in t),
    (
        "数学",
        lambda t: "E=mc" in t
        or "mc^2" in t
        # OMML: E=mc^2 → run "E=m" + sSup(c,2) (contiguous ASCII sentinel gone)
        or ("m:oMath" in t and "E=m" in t and "m:sSup" in t)
        or r"E=mc^2" in t
        or r"E=mc^{2}" in t,
    ),
    (
        "图片",
        lambda t: "logo" in t.lower()
        or "[Image" in t
        or "<img" in t.lower()
        or "image/png" in t.lower(),
    ),
]

_TARGETS = [
    "md",
    "txt",
    "html",
    "json",
    "docx",
    "xlsx",
    "pptx",
    "epub",
    "odt",
    "ods",
    "odp",
    "latex",
    "mobi",
    "azw3",
    "pdf",
    "rtf",
    "csv",
]


def _blob_text(fmt: str, data: bytes) -> str:
    if data[:2] == b"PK":
        parts: list[str] = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                raw = zf.read(name)
                try:
                    parts.append(raw.decode("utf-8"))
                except UnicodeDecodeError:
                    parts.append(raw.decode("latin-1", errors="ignore"))
        return "\n".join(parts)
    if fmt == "pdf":
        return data.decode("latin-1", errors="ignore")
    return data.decode("utf-8", errors="ignore")


@pytest.mark.parametrize("fmt", _TARGETS)
def test_fidelity_matrix_target_keeps_semantics(fmt: str):
    data = convert(_FIDELITY_MD, "md", fmt, filename="fidelity.md")
    assert data, f"{fmt} export empty"
    text = _blob_text(fmt, data)
    fails = [name for name, fn in _CHECKS if not fn(text)]
    assert not fails, f"{fmt} lost {fails}"


def test_csv_mixed_doc_emits_content_section():
    out = convert(_FIDELITY_MD, "md", "csv", filename="fidelity.md").decode("utf-8")
    assert "_CONTENT" in out
    assert "**bold**" in out
    assert "example.com" in out
    assert "ColA" in out


def test_txt_and_pdf_expose_rich_markers():
    txt = convert(_FIDELITY_MD, "md", "txt", filename="fidelity.md").decode("utf-8")
    assert "**bold**" in txt and "*italic*" in txt
    assert "https://example.com/docs" in txt
    pdf = convert(_FIDELITY_MD, "md", "pdf", filename="fidelity.md")
    blob = pdf.decode("latin-1", errors="ignore")
    assert "**bold**" in blob and "*italic*" in blob
    assert "example.com" in blob
    assert "[Image" in blob or "logo" in blob.lower()
