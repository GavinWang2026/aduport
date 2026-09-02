#!/usr/bin/env bash
# =====================================================================
# build_native.sh — compile the VENDORED anydoc Rust source into md-bridge
# ---------------------------------------------------------------------
# anydoc's Rust source lives in-tree at native/anydoc/ (it is NOT an
# external PyPI dependency). This script compiles it with maturin into the
# `mdbridge_native._doc` extension module and installs it into the
# backend virtualenv. No network / no PyPI round-trip is involved for the
# extension itself (maturin may be installed from PyPI if missing).
#
# Usage:
#   ./scripts/build_native.sh                 # build + install into backend/.venv
#   MDBRIDGE_PYTHON=/path/to/python ./scripts/build_native.sh
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NATIVE="$ROOT/native/anydoc/python"

# --- target python (backend venv by default) ---
PY="${MDBRIDGE_PYTHON:-$ROOT/backend/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  if [ ! -d "$ROOT/backend/.venv" ]; then
    echo "==> creating backend/.venv"
    python3 -m venv "$ROOT/backend/.venv"
  fi
  PY="$ROOT/backend/.venv/bin/python"
fi
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi
if [ -z "${PY:-}" ] || [ ! -x "$PY" ]; then
  echo "no python interpreter found" >&2
  exit 1
fi

# --- locate maturin (PATH, then MDBRIDGE_MATURIN, then install into venv) ---
MATURIN="${MDBRIDGE_MATURIN:-}"
if [ -n "$MATURIN" ] && [ ! -x "$MATURIN" ]; then
  echo "MDBRIDGE_MATURIN=$MATURIN is not executable" >&2
  exit 1
fi
if [ -z "$MATURIN" ]; then
  MATURIN="$(command -v maturin 2>/dev/null || true)"
fi
if [ -z "$MATURIN" ]; then
  VENV_MATURIN="$(dirname "$PY")/maturin"
  if [ -x "$VENV_MATURIN" ]; then
    MATURIN="$VENV_MATURIN"
  else
    echo "==> installing maturin into $($PY -c 'import sys; print(sys.prefix)')"
    "$PY" -m pip install -q "maturin>=1.9,<2"
    MATURIN="$(dirname "$PY")/maturin"
  fi
fi
if [ -z "$MATURIN" ] || [ ! -x "$MATURIN" ]; then
  echo "maturin not available. Install: pip install 'maturin>=1.9,<2' (or set MDBRIDGE_MATURIN)" >&2
  exit 1
fi

# Drop leftover external firecrawl-anydoc if present (bridge uses mdbridge_native)
if "$PY" -c "import importlib.util as u; raise SystemExit(0 if u.find_spec('anydoc') else 1)" 2>/dev/null; then
  echo "==> uninstalling leftover firecrawl-anydoc / anydoc package"
  "$PY" -m pip uninstall -y firecrawl-anydoc anydoc 2>/dev/null || true
fi

echo "==> building vendored anydoc (maturin) from $NATIVE"
cd "$NATIVE"
"$MATURIN" build --release

WHEEL=$(ls -t "$ROOT/native/anydoc/target/wheels/"*.whl 2>/dev/null | head -1)
if [ -z "$WHEEL" ]; then echo "wheel not produced" >&2; exit 1; fi

echo "==> installing $WHEEL (local, offline)"
"$PY" -m pip install --no-index --force-reinstall --no-deps "$WHEEL"

echo "==> verifying import + abi3 contract"
cd "$ROOT/backend"
"$PY" - <<PY
import importlib.metadata as md
import sys
from pathlib import Path

import mdbridge_native._doc as d

required = ("to_markdown_bytes", "to_document", "format_from_bytes")
missing = [n for n in required if not hasattr(d, n)]
if missing:
    print("FAIL missing exports:", missing, file=sys.stderr)
    sys.exit(1)

dist = md.distribution("mdbridge-native")
ver = dist.version
abi_ok = any("abi3" in str(f) for f in (dist.files or []))
if not abi_ok:
    wheels = list(Path("$ROOT/native/anydoc/target/wheels").glob("mdbridge_native*.whl"))
    abi_ok = any("abi3" in w.name for w in wheels)
if not abi_ok:
    print("FAIL: abi3 tag not found on installed mdbridge-native / wheels", file=sys.stderr)
    sys.exit(1)
print(f"OK abi3 contract · mdbridge-native {ver}")
print("OK exports:", ", ".join(required))
print("OK mdbridge_native._doc sample:", [x for x in dir(d) if not x.startswith("_")][:5])
PY
echo "==> done. md-bridge now ships its own Rust document parser."
