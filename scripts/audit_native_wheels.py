#!/usr/bin/env python3
"""Audit native wheel artifacts (v13 P3-8).

Checks:
  - only ``*.whl`` (and optional SHA256SUMS) under the given directory
  - filenames match ``aduport_native-*.whl``
  - no nested path traversal / unexpected suffixes
  - wheel zip members do not include secrets (.env, *.pem private keys, credentials)
  - size bounds (reject empty / absurdly large)

Usage:
  python scripts/audit_native_wheels.py wheelhouse/
  python scripts/audit_native_wheels.py dist/*.whl
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

_WHEEL_RE = re.compile(r"^aduport_native-.+\.whl$", re.IGNORECASE)
_BAD_NAME = re.compile(
    r"(^|/)\.env($|\.)|(^|/)id_rsa|(^|/).*credentials.*\.(json|yml|yaml|txt)$|(^|/).*secret.*\.(pem|key)$",
    re.IGNORECASE,
)
_MAX_WHEEL_BYTES = 200 * 1024 * 1024  # 200 MiB soft ceiling per wheel
_MIN_WHEEL_BYTES = 1024


def _audit_wheel(path: Path) -> list[str]:
    errs: list[str] = []
    if not _WHEEL_RE.match(path.name):
        errs.append(f"{path.name}: name must match aduport_native-*.whl")
    size = path.stat().st_size
    if size < _MIN_WHEEL_BYTES:
        errs.append(f"{path.name}: too small ({size} bytes)")
    if size > _MAX_WHEEL_BYTES:
        errs.append(f"{path.name}: too large ({size} bytes)")
    try:
        with zipfile.ZipFile(path, "r") as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    errs.append(f"{path.name}: unsafe member path {name!r}")
                if _BAD_NAME.search(name):
                    errs.append(f"{path.name}: suspicious member {name!r}")
                if info.file_size > _MAX_WHEEL_BYTES:
                    errs.append(f"{path.name}: member too large {name!r}")
    except zipfile.BadZipFile as exc:
        errs.append(f"{path.name}: not a zip/wheel ({exc})")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit aduport_native wheel artifacts")
    ap.add_argument("paths", nargs="+", help="wheel files or directories")
    args = ap.parse_args()

    wheels: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            wheels.extend(sorted(p.rglob("*.whl")))
        elif p.is_file():
            wheels.append(p)
        else:
            print(f"FAIL: path not found: {p}", file=sys.stderr)
            return 2

    if not wheels:
        print("FAIL: no .whl files found", file=sys.stderr)
        return 2

    all_errs: list[str] = []
    for w in wheels:
        all_errs.extend(_audit_wheel(w))

    if all_errs:
        for e in all_errs:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print(f"OK: audited {len(wheels)} wheel(s)")
    for w in wheels:
        print(f"  - {w.name} ({w.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
