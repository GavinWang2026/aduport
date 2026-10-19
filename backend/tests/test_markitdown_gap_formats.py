"""New source formats: msg / ipynb / zip / image (MarkItDown-gap)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from aduport.core.converter import convert
from aduport.core.document_model import CodeBlock, Image
from aduport.core.format_detect import detect_source_format
from aduport.core.registry import ensure_builtin_plugins, get_importer
from aduport.importers.image_importer import import_image
from aduport.importers.ipynb_importer import import_ipynb, notebook_to_markdown
from aduport.importers.msg_importer import import_msg
from aduport.importers.zip_importer import import_zip


def test_ipynb_to_markdown_and_import():
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"title": "Demo"},
        "cells": [
            {"cell_type": "markdown", "source": ["# Hello\n", "world"]},
            {"cell_type": "code", "source": ["print(1)\n"]},
        ],
    }
    md, title = notebook_to_markdown(nb)
    assert title == "Demo"
    assert "```python" in md
    assert "print(1)" in md
    model = import_ipynb(json.dumps(nb).encode("utf-8"), "demo.ipynb")
    assert model.title == "Demo"
    assert model.metadata.get("importer") == "ipynb"
    assert any(isinstance(n, CodeBlock) for n in model.nodes) or any(
        "print" in getattr(getattr(n, "text", None), "text", str(getattr(n, "text", "")))
        for n in model.nodes
    )


def test_ipynb_via_convert():
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {},
        "cells": [{"cell_type": "markdown", "source": ["# T\n"]}],
    }
    out = convert(json.dumps(nb).encode(), "ipynb", "md", filename="t.ipynb")
    assert b"# T" in out or b"T" in out


def test_zip_import_txt_members():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "alpha line\n\nsecond")
        zf.writestr("b.md", "# Beta\n\nbody")
    data = buf.getvalue()
    model = import_zip(data, "pack.zip")
    assert model.metadata.get("importer") == "zip"
    assert model.metadata.get("zip_members_converted", 0) >= 2
    out = convert(data, "zip", "md", filename="pack.zip")
    assert b"alpha" in out.lower() or b"Beta" in out or b"beta" in out.lower()


def test_image_png_embeds_without_ocr(monkeypatch):
    from aduport.config import settings

    monkeypatch.setattr(settings, "ocr_enabled", False)
    # 1x1 PNG
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    model = import_image(png, "dot.png")
    assert any(isinstance(n, Image) for n in model.nodes)
    assert model.metadata.get("ocr_applied") is False
    assert model.metadata.get("ocr_notice") == "未启用 OCR，仅嵌入图片"
    assert any(getattr(n, "text", None) and "未启用 OCR，仅嵌入图片" in str(getattr(n.text, "text", n.text)) for n in model.nodes)
    assert detect_source_format("x.png", png) == "png"


def test_detect_ipynb_and_jpeg():
    nb = b'{"nbformat": 4, "cells": []}'
    assert detect_source_format("n.ipynb", nb) == "ipynb"
    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 20
    assert detect_source_format("a.jpg", jpeg) == "jpg"


def test_msg_rejects_non_ole():
    with pytest.raises(Exception):
        import_msg(b"not-ole", "x.msg")


def test_msg_real_cfb_import_no_monkeypatch():
    """P2-3: real CFB/MAPI streams — no monkeypatch of ole parsers."""
    from tests._ole_fixtures import build_minimal_msg

    data = build_minimal_msg(subject="Real Subj", body="Hello from real CFB", sender="a@b.c")
    model = import_msg(data, "mail.msg")
    assert model.metadata.get("importer") == "msg"
    assert model.title == "Real Subj"
    assert model.metadata.get("msg_attachments") == 0
    plain = " ".join(
        getattr(getattr(n, "text", None), "text", str(getattr(n, "text", "")))
        for n in model.nodes
    )
    assert "Real Subj" in plain or model.title == "Real Subj"
    assert "Hello from real CFB" in plain
    assert model.metadata.get("email_from") == "a@b.c"


def test_msg_via_convert_real_cfb():
    from tests._ole_fixtures import build_minimal_msg

    data = build_minimal_msg(subject="Conv", body="body-x")
    out = convert(data, "msg", "md", filename="m.msg")
    assert b"Conv" in out or b"body-x" in out


def test_registry_has_new_formats():
    ensure_builtin_plugins()
    for fmt in ("msg", "ipynb", "zip", "png", "jpg"):
        assert get_importer(fmt) is not None


def test_mcp_convert_helper(tmp_path):
    from aduport.mcp_server import _convert_bytes

    p = tmp_path / "n.ipynb"
    p.write_text(
        json.dumps(
            {
                "nbformat": 4,
                "nbformat_minor": 5,
                "metadata": {},
                "cells": [{"cell_type": "markdown", "source": ["# MCP\n"]}],
            }
        ),
        encoding="utf-8",
    )
    result = _convert_bytes(p.read_bytes(), "ipynb", "md", filename=p.name)
    assert result.get("ok") is True
    assert "MCP" in result.get("data", "")
