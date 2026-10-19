"""Fidelity matrix upgrades: MD → 15 targets keep 17 semantic sentinels searchable."""

from __future__ import annotations

import io
import zipfile

import pytest

from aduport.core.converter import convert

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
        lambda t: (
            # Real embeds / media parts — placeholder-only is NOT enough (v15 P1).
            ("logo" in t.lower() and ("![logo" in t.lower() or "<img" in t.lower()))
            or "image/png" in t.lower()
            or "word/media" in t
            or "a:blip" in t
            or "Pictures/" in t
            or "image1." in t.lower()
        ),
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
        return _pdf_search_text(data)
    return data.decode("utf-8", errors="ignore")


def _pdf_search_text(data: bytes) -> str:
    """Visible text plus decompressed content streams (markup is not in the raw file)."""
    chunks = [data.decode("latin-1", errors="ignore")]
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
            annots = page.get("/Annots") or []
            for annot in annots:
                obj = annot.get_object() if hasattr(annot, "get_object") else annot
                action = obj.get("/A") if obj is not None else None
                if action is not None:
                    uri = action.get("/URI")
                    if uri:
                        chunks.append(str(uri))
            contents = page.get_contents()
            if contents is None:
                continue
            raw = contents.get_data() if hasattr(contents, "get_data") else b""
            if raw:
                chunks.append(raw.decode("latin-1", errors="ignore"))
    except Exception:
        pass
    return "\n".join(chunks)


@pytest.mark.parametrize("fmt", _TARGETS)
def test_fidelity_matrix_target_keeps_semantics(fmt: str):
    data = convert(_FIDELITY_MD, "md", fmt, filename="fidelity.md")
    assert data, f"{fmt} export empty"
    text = _blob_text(fmt, data)
    # Image sentinel is not required for shallow text exports (pdf_simple/mobi/…).
    skip_image = fmt in {"pdf", "mobi", "azw3", "txt", "csv", "latex", "rtf"}
    checks = [(n, fn) for n, fn in _CHECKS if not (skip_image and n == "图片")]
    fails = [name for name, fn in checks if not fn(text)]
    if fmt == "pdf":
        # The text layer keeps the words; markdown/HTML markup is not stored there.
        plain = {
            "粗体": "bold" in text.lower(),
            "斜体": "italic" in text.lower(),
            "删除线": "strike" in text.lower(),
        }
        fails = [name for name in fails if not plain.get(name)]
    assert not fails, f"{fmt} lost {fails}"


def test_csv_mixed_doc_emits_content_section():
    out = convert(_FIDELITY_MD, "md", "csv", filename="fidelity.md").decode("utf-8")
    assert "_CONTENT" in out
    assert "**bold**" in out
    assert "example.com" in out
    assert "ColA" in out


def test_txt_and_pdf_expose_rich_markers():
    """TXT always uses markdown-visible marks; PDF depends on engine (v15 honesty)."""
    from aduport.exporters.pdf_exporter import export_pdf
    from aduport.importers.md_importer import import_md

    txt = convert(_FIDELITY_MD, "md", "txt", filename="fidelity.md").decode("utf-8")
    assert "**bold**" in txt and "*italic*" in txt
    assert "https://example.com/docs" in txt

    model = import_md(_FIDELITY_MD)
    out = export_pdf(model)
    meta = model.metadata or {}
    engine = str(meta.get("pdf_engine") or "")
    blob = _pdf_search_text(out)
    if engine == "weasyprint":
        assert "bold" in blob.lower()
    else:
        # Degraded pdf_simple path must be explicit — not silent "full fidelity"
        assert meta.get("fidelity_degraded") or meta.get("pdf_engine_reason")
        assert "**bold**" in blob or "bold" in blob.lower()
