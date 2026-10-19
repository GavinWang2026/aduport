"""Delist iWork, move rag to an export mode, and keep the OLE Markdown fallback."""

from __future__ import annotations

import json
import re
import zipfile
from io import BytesIO

import pytest

from aduport.core.converter import (
    EXPORT_MODES,
    SUPPORTED_SOURCE_FORMATS,
    SUPPORTED_TARGET_FORMATS,
    _try_anydoc_fast_markdown,
    convert,
)
from aduport.core.exceptions import CorruptedFileError, InvalidDocumentError, UnsupportedFormatError
from aduport.core.format_detect import detect_source_format
from aduport.importers.iwork_importer import import_keynote, import_numbers, import_pages


def test_iwork_sources_are_delisted():
    assert {"pages", "numbers", "keynote"}.isdisjoint(SUPPORTED_SOURCE_FORMATS)
    for fn, name in (
        (import_pages, "Pages"),
        (import_numbers, "Numbers"),
        (import_keynote, "Keynote"),
    ):
        with pytest.raises(UnsupportedFormatError, match="已摘牌"):
            fn(b"PK\x03\x04", f"in.{name.lower()}")
    assert detect_source_format("deck.pages", b"PK\x03\x04fake") is None
    assert detect_source_format("sheet.key") is None


def test_rag_is_export_mode_not_target_format():
    assert "rag" not in SUPPORTED_TARGET_FORMATS
    assert EXPORT_MODES == frozenset({"rag"})
    out = convert(b"# Title\n\nHello RAG.\n", "md", "rag", filename="t.md")
    payload = json.loads(out.decode())
    assert payload.get("schema") == "aduport.rag_elements.v1"
    alias = convert(b"# T\n", "md", "elements", filename="t.md")
    assert b"aduport.rag_elements" in alias


def test_fast_markdown_corrupted_file_falls_back(monkeypatch):
    def boom(*_args, **_kwargs):
        raise CorruptedFileError("文档结构不可用，无法提取内容（部件 PowerPoint Document）")

    monkeypatch.setattr(
        "aduport.importers.anydoc_bridge.markdown_via_anydoc", boom
    )
    monkeypatch.setattr(
        "aduport.importers.anydoc_bridge.should_handle_fast_md", lambda _fmt: True
    )
    monkeypatch.setattr("aduport.importers.anydoc_bridge.supports", lambda _fmt: True)
    monkeypatch.setattr(
        "aduport.importers.anydoc_bridge.prefer_native_importer", lambda *_a, **_k: False
    )
    assert _try_anydoc_fast_markdown(b"ole", "ppt", "md", "in.ppt") is None


def test_fast_markdown_still_raises_other_bridge_errors(monkeypatch):
    def boom(*_args, **_kwargs):
        raise InvalidDocumentError("文档已加密或受密码保护: in.ppt")

    monkeypatch.setattr(
        "aduport.importers.anydoc_bridge.markdown_via_anydoc", boom
    )
    monkeypatch.setattr(
        "aduport.importers.anydoc_bridge.should_handle_fast_md", lambda _fmt: True
    )
    monkeypatch.setattr("aduport.importers.anydoc_bridge.supports", lambda _fmt: True)
    monkeypatch.setattr(
        "aduport.importers.anydoc_bridge.prefer_native_importer", lambda *_a, **_k: False
    )
    with pytest.raises(InvalidDocumentError):
        _try_anydoc_fast_markdown(b"ole", "ppt", "md", "in.ppt")


def test_doctor_does_not_call_disabled_ocr_ready(monkeypatch):
    from typer.testing import CliRunner

    from aduport.cli import app

    monkeypatch.setattr("aduport.config.settings.ocr_enabled", False)
    monkeypatch.setattr("aduport.config.settings.license_required", False)
    monkeypatch.setattr("aduport.config.settings.redis_url", "")
    result = CliRunner().invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    assert payload["ocr_ready"] is False
    ocr = next(item for item in payload["checks"] if item["name"] == "ocr")
    assert ocr["ready"] is False
    assert ocr["ok"] is False
    assert ocr["advisory"] is True
    assert "未启用 OCR，仅嵌入图片" in ocr["detail"]


def test_odp_seed_tokens_are_visible_text():
    seed = """# ALPHA 2026 报告

BRAVO 这是一个用于校验的段落，包含 31415 这个数字。

## CHARLIE 数据

| 区域 | Q1 |
|---|---|
| DELTA | 31415 |

- ECHO 一
- ECHO 二

> FOXTROT 引用行

```text
GOLF 代码块
HOTEL
```

中文校验 结束段。
"""
    tokens = [
        "ALPHA",
        "BRAVO",
        "CHARLIE",
        "DELTA",
        "ECHO",
        "FOXTROT",
        "GOLF",
        "HOTEL",
        "31415",
        "2026",
        "中文校验",
    ]
    out = convert(seed.encode(), "md", "odp", filename="in.md")
    xml = zipfile.ZipFile(BytesIO(out)).read("content.xml").decode()
    visible = re.sub(r"<[^>]+>", " ", xml)
    missing = [token for token in tokens if token not in visible]
    assert not missing, missing
    assert "<table" in xml or "| DELTA |" in xml or "DELTA" in visible
