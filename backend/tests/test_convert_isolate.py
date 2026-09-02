"""Subprocess isolation for convert (API survives worker crash)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.convert_isolate import convert_isolated
from app.core.exceptions import ExportError, MdBridgeError


def test_convert_isolated_md_roundtrip():
    out = convert_isolated(b"# Hello\n\nworld\n", "md", "txt", filename="t.md")
    assert b"Hello" in out
    assert b"world" in out


def test_convert_isolated_falls_back_when_worker_crash(tmp_path: Path):
    """Worker crash without status.json → in-process fallback still succeeds."""

    def fake_run(cmd, **kwargs):
        class Proc:
            returncode = -6
            stderr = b"abort: fake native crash"
            stdout = b""

        return Proc()

    with patch("app.core.convert_isolate.settings") as s:
        s.convert_isolate = True
        s.convert_timeout_seconds = 60
        with patch("app.core.convert_isolate.subprocess.run", side_effect=fake_run):
            out = convert_isolated(b"# x\n", "md", "txt", filename="x.md")
            assert b"x" in out


def test_convert_isolated_reports_worker_crash_when_fallback_fails(tmp_path: Path):
    """If worker dies and in-process fallback fails, surface worker_crash."""

    def fake_run(cmd, **kwargs):
        class Proc:
            returncode = -6
            stderr = b"abort: fake native crash"
            stdout = b""

        return Proc()

    with patch("app.core.convert_isolate.settings") as s:
        s.convert_isolate = True
        s.convert_timeout_seconds = 60
        with patch("app.core.convert_isolate.subprocess.run", side_effect=fake_run):
            with patch(
                "app.core.convert_isolate.convert",
                side_effect=RuntimeError("boom"),
            ):
                with pytest.raises(ExportError) as ei:
                    convert_isolated(b"# x\n", "md", "txt", filename="x.md")
                assert ei.value.code == "worker_crash"


def test_convert_isolated_passthrough_when_disabled():
    with patch("app.core.convert_isolate.settings") as s:
        s.convert_isolate = False
        with patch("app.core.convert_isolate.convert", return_value=b"ok") as cv:
            out = convert_isolated(b"# a\n", "md", "txt")
            assert out == b"ok"
            cv.assert_called_once()


def test_convert_worker_writes_status(tmp_path: Path):
    from app.core.convert_worker import main

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
