"""Subprocess isolation for convert (API survives worker crash)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from aduport.core.convert_isolate import convert_isolated
from aduport.core.exceptions import ExportError, AduPortError


def test_convert_isolated_md_roundtrip():
    out = convert_isolated(b"# Hello\n\nworld\n", "md", "txt", filename="t.md")
    assert b"Hello" in out
    assert b"world" in out


def _fake_popen(*, returncode: int = -6, stderr: bytes = b"abort: fake native crash"):
    def factory(cmd, **kwargs):
        proc = MagicMock()
        proc.pid = 4242
        proc.returncode = returncode
        proc.communicate.return_value = (b"", stderr)
        return proc

    return factory


def test_convert_isolated_falls_back_when_worker_nonzero_exit():
    """Non-signal worker exit without status.json → in-process fallback."""
    with patch("aduport.core.convert_isolate.settings") as s:
        s.convert_isolate = True
        s.convert_isolate_fork = True
        s.convert_timeout_seconds = 60
        with patch(
            "aduport.core.convert_isolate.subprocess.Popen",
            side_effect=_fake_popen(returncode=1, stderr=b"worker oops"),
        ):
            out = convert_isolated(b"# x\n", "md", "txt", filename="x.md")
            assert b"x" in out


def test_convert_isolated_no_fallback_on_signal_kill():
    """Signal/OOM (rc < 0) must not retry in-process (P1 #24)."""
    with patch("aduport.core.convert_isolate.settings") as s:
        s.convert_isolate = True
        s.convert_isolate_fork = True
        s.convert_timeout_seconds = 60
        with patch(
            "aduport.core.convert_isolate.subprocess.Popen",
            side_effect=_fake_popen(returncode=-6),
        ):
            with pytest.raises(ExportError) as ei:
                convert_isolated(b"# x\n", "md", "txt", filename="x.md")
            assert ei.value.code == "worker_crash"


def test_convert_isolated_reports_worker_crash_when_fallback_fails():
    """If worker dies with non-signal rc and fallback fails, surface worker_crash."""
    with patch("aduport.core.convert_isolate.settings") as s:
        s.convert_isolate = True
        s.convert_isolate_fork = True
        s.convert_timeout_seconds = 60
        with patch(
            "aduport.core.convert_isolate.subprocess.Popen",
            side_effect=_fake_popen(returncode=1, stderr=b"abort"),
        ):
            with patch(
                "aduport.core.convert_isolate.convert",
                side_effect=RuntimeError("boom"),
            ):
                with pytest.raises(ExportError) as ei:
                    convert_isolated(b"# x\n", "md", "txt", filename="x.md")
                assert ei.value.code == "worker_crash"


def test_convert_isolated_passthrough_when_disabled():
    with patch("aduport.core.convert_isolate.settings") as s:
        s.convert_isolate = False
        with patch("aduport.core.convert_isolate.convert", return_value=b"ok") as cv:
            out = convert_isolated(b"# a\n", "md", "txt")
            assert out == b"ok"
            cv.assert_called_once()


def test_convert_worker_writes_status(tmp_path: Path):
    from aduport.core.convert_worker import main

    inp = tmp_path / "in.bin"
    out = tmp_path / "out.bin"
    req = tmp_path / "req.json"
    status = tmp_path / "status.json"
    inp.write_bytes(b"# Title\n")
    req.write_text(
        json.dumps(
            {
                "input_path": str(inp),
                "source_format": "md",
                "target_format": "txt",
                "filename": "t.md",
            }
        ),
        encoding="utf-8",
    )
    assert main([str(req), str(out), str(status)]) == 0
    st = json.loads(status.read_text(encoding="utf-8"))
    assert st["ok"] is True
    assert out.read_bytes()


def test_convert_worker_forwards_rag_and_theme(tmp_path: Path, monkeypatch):
    from aduport.core import convert_worker as cw

    seen: dict = {}

    def fake_convert(content, source_format, target_format, **kwargs):
        seen.update(kwargs)
        seen["src"] = source_format
        seen["tgt"] = target_format
        return b'{"schema":"aduport.rag_elements.v1","elements":[]}'

    monkeypatch.setattr("aduport.core.converter.convert", fake_convert)

    inp = tmp_path / "in.bin"
    out = tmp_path / "out.bin"
    req = tmp_path / "req.json"
    status = tmp_path / "status.json"
    inp.write_bytes(b"# T\n")
    req.write_text(
        json.dumps(
            {
                "input_path": str(inp),
                "source_format": "md",
                "target_format": "rag",
                "filename": "t.md",
                "docx_theme": "academic",
                "rag_max_chars": 400,
                "rag_chunk_overlap": 40,
            }
        ),
        encoding="utf-8",
    )
    assert cw.main([str(req), str(out), str(status)]) == 0
    assert seen.get("docx_theme") == "academic"
    assert seen.get("rag_max_chars") == 400
    assert seen.get("rag_chunk_overlap") == 40
    assert seen.get("tgt") == "rag"
