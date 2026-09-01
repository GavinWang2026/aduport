#!/usr/bin/env bash
# Build the Firecrawl anydoc Python wheel (abi3, Python 3.10+) from the vendored
# anydoc-main/ tree and install it into the backend virtualenv.
#
# anydoc is MIT and pure-Rust (no network / no ML / no LibreOffice), so it is a
# drop-in accelerator for md-bridge's import side. md-bridge treats it as an
# OPTIONAL extra: with the wheel present and MDBRIDGE_ANYDOC_ENABLED=true the
# anydoc-backed formats (doc, odt, rtf, ppt, ods, xls/xlsb/xlsm, ...) gain
# span-aware tables, real list numbering, LaTeX math, embedded assets and the
# previously-unsupported binary Office / ODF formats. Without it, md-bridge
# degrades to its native importers exactly as before.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ANYDOC_DIR="${ANYDOC_DIR:-$ROOT/anydoc-main}"
VENV="${VENV:-$ROOT/backend/.venv}"
PY="${PY:-$VENV/bin/python}"

echo ">> anydoc source : $ANYDOC_DIR"
echo ">> target venv   : $PY"

if [[ ! -d "$ANYDOC_DIR/python" ]]; then
  echo "!! anydoc python bindings not found at $ANYDOC_DIR/python" >&2
  exit 1
fi

# maturin builds the abi3 wheel; install into the backend venv.
"$PY" -m pip install --quiet --upgrade maturin
( cd "$ANYDOC_DIR" && "$PY" -m maturin build --release --out "$ROOT/.wheels" --manifest-path python/Cargo.toml -i "$PY" )

WHEEL=$(ls -t "$ROOT/.wheels"/firecrawl_anydoc-*.whl 2>/dev/null | head -n1)
if [[ -z "${WHEEL:-}" ]]; then
  echo "!! wheel build failed" >&2
  exit 1
fi

echo ">> installing $WHEEL"
"$PY" -m pip install --quiet --force-reinstall "$WHEEL"
"$PY" -c "import anydoc; print('anydoc installed:', getattr(anydoc, '__version__', 'unknown'))"

# Smoke-test the bridge wiring.
"$PY" - <<'PY'
import sys
sys.path.insert(0, "backend")
from app.importers.anydoc_bridge import supports, should_handle
print("supports(xlsb)=", supports("xlsb"), "| should_handle(xlsb)=", should_handle("xlsb"))
PY
