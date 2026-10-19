#!/usr/bin/env python3
"""Ensure package version is consistent across pyproject / __init__ / FastAPI."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _pyproject_version() -> str:
    text = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("version not found in pyproject.toml")
    return m.group(1)


def _init_version() -> str:
    text = (BACKEND / "aduport" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("__version__ not found")
    return m.group(1)


def main() -> int:
    expected = _pyproject_version()
    init_v = _init_version()
    if init_v != expected:
        print(f"FAIL: app.__version__={init_v} != pyproject={expected}", file=sys.stderr)
        return 1

    # Prefer importing the package when run from backend/
    sys.path.insert(0, str(BACKEND))
    from aduport import __version__ as runtime_v  # noqa: WPS433

    if runtime_v != expected:
        print(f"FAIL: import aduport.__version__={runtime_v}", file=sys.stderr)
        return 1

    # Drift guards for hardcoded leftovers
    for path in (BACKEND / "main.py", BACKEND / "aduport" / "api" / "routes.py"):
        text = path.read_text(encoding="utf-8")
        # Allow importing __version__; flag raw 0.x.y string mismatches if present alone
        for m in re.finditer(r'version\s*=\s*"(\d+\.\d+\.\d+)"', text):
            if m.group(1) != expected:
                print(f"FAIL: {path.name} has version={m.group(1)}", file=sys.stderr)
                return 1
        for m in re.finditer(r'"version":\s*"(\d+\.\d+\.\d+)"', text):
            if m.group(1) != expected:
                print(f"FAIL: {path.name} has version={m.group(1)}", file=sys.stderr)
                return 1

    print(f"OK version={expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
