"""MCP server: path-scope guards and conversion-core correctness.

The MCP layer's own dependency (``mcp``) is optional and not required here —
these tests exercise the module-level functions that carry the security and
correctness contract (path allow-listing, extension allow-listing, and the
JSON-friendly conversion envelope), so they run even without ``mcp`` installed.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from aduport import mcp_server as m
from aduport.config import settings


# --------------------------------------------------------------------------- #
# Path-scope guards
# --------------------------------------------------------------------------- #
def test_allowed_roots_default_is_cwd(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcp_allowed_roots", "")
    roots = m._allowed_roots()
    assert len(roots) == 1
    assert roots[0] == Path.cwd().resolve()


def test_allowed_roots_star_is_unrestricted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcp_allowed_roots", "*")
    assert m._allowed_roots() == []


def test_allowed_roots_multiple(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(settings, "mcp_allowed_roots", f"{tmp_path},/etc")
    roots = m._allowed_roots()
    assert roots == [tmp_path.resolve(), Path("/etc").resolve()]


def test_assert_path_allowed_inside_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcp_allowed_roots", str(tmp_path))
    m._assert_path_allowed(tmp_path / "a.md")  # must not raise


def test_assert_path_allowed_outside_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcp_allowed_roots", str(tmp_path))
    outside = tmp_path.parent / "elsewhere.md"
    with pytest.raises(PermissionError):
        m._assert_path_allowed(outside)


def test_assert_path_allowed_unrestricted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcp_allowed_roots", "*")
    m._assert_path_allowed(Path("/tmp/anything.md"))  # must not raise


def test_allowed_extensions_parsing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcp_allowed_extensions", ".md, DOCX,pdf")
    assert m._allowed_extensions() == {".md", ".docx", ".pdf"}


def test_allowed_extensions_empty(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "mcp_allowed_extensions", "")
    assert m._allowed_extensions() == set()


# --------------------------------------------------------------------------- #
# Conversion envelope
# --------------------------------------------------------------------------- #
def test_convert_bytes_textual_target():
    r = m._convert_bytes(b"# Hi\n\n- a\n- b\n", "md", "html", filename="a.md")
    assert r["ok"] is True
    assert r["target_format"] == "html"
    assert r["encoding"] == "utf-8"
    assert "Hi" in r["data"]


def test_convert_bytes_binary_target_is_base64():
    r = m._convert_bytes(b"# Hi\n", "md", "docx", filename="a.md")
    assert r["ok"] is True
    assert r["target_format"] == "docx"
    assert r["encoding"] == "base64"
    assert "note" in r  # decode hint for the agent
    payload = base64.b64decode(r["data"])
    assert payload[:2] == b"PK"  # OOXML (ZIP) magic


def test_convert_bytes_unsupported_target_returns_error_dict():
    r = m._convert_bytes(b"# Hi\n", "md", "not-a-format", filename="a.md")
    assert r["ok"] is False
    assert r["error"]


def test_convert_bytes_never_raises_on_bad_input():
    # Corrupted docx bytes must surface as an error dict, not an exception.
    r = m._convert_bytes(b"\x00\x01\x02", "docx", "md", filename="a.docx")
    assert isinstance(r, dict)
    assert r["ok"] in (True, False)


def test_error_result_shape():
    r = m._error_result(ValueError("boom"), "md")
    assert r["ok"] is False
    assert r["target_format"] == "md"
    assert "ValueError" in r["error"]
    assert "boom" in r["error"]
