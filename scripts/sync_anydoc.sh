#!/usr/bin/env bash
# =====================================================================
# sync_anydoc.sh — pull upstream Firecrawl/anydoc into the vendored tree
# ---------------------------------------------------------------------
# md-bridge vendors anydoc's Rust source at native/anydoc/ (NOT an external
# PyPI dependency). When upstream ships fixes/new formats, re-sync with:
#   ./scripts/sync_anydoc.sh                 # tracks upstream main
#   ANYDOC_REF=v0.2.5 ./scripts/sync_anydoc.sh
#
# What gets synced:
#   - upstream src/              -> native/anydoc/src/            (parser core, full overwrite)
#   - upstream python/src/*.rs   -> native/anydoc/python/src/     (binding logic, except lib.rs)
# What is KEPT as our fork (do NOT overwrite):
#   - python/Cargo.toml         (members=["python"], path dep)
#   - python/pyproject.toml     (module-name = mdbridge_native._doc)
#   - python/src/lib.rs         (pymodule fn _doc + hosted-OCR stripped)
#   - python/mdbridge_native/   (package anchor)
#   - LICENSE                   (MIT, preserved)
# After sync, rebuild: ./scripts/build_native.sh
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UPSTREAM="${ANYDOC_UPSTREAM:-https://github.com/firecrawl/anydoc.git}"
REF="${ANYDOC_REF:-main}"

echo "==> syncing anydoc from $UPSTREAM @ $REF"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

git clone --depth 1 --branch "$REF" "$UPSTREAM" "$TMP/up" 2>&1 | tail -3

# parser core (full overwrite)
rm -rf "$ROOT/native/anydoc/src"
cp -R "$TMP/up/src" "$ROOT/native/anydoc/src"

# binding logic: overwrite document.rs etc., but KEEP our forked lib.rs
for f in "$TMP/up/python/src"/*.rs; do
  base="$(basename "$f")"
  [ "$base" = "lib.rs" ] && { echo "  skip lib.rs (local fork: module _doc + hosted-OCR stripped)"; continue; }
  cp "$f" "$ROOT/native/anydoc/python/src/$base"
done

# license preserved
cp "$TMP/up/LICENSE" "$ROOT/native/anydoc/LICENSE"

# sanity: our fork entry point must still exist
if ! grep -q "fn _doc(" "$ROOT/native/anydoc/python/src/lib.rs"; then
  echo "!! WARNING: python/src/lib.rs no longer defines 'fn _doc'. Re-apply the local fork:" >&2
  echo "   - rename #[pymodule] fn _anydoc -> fn _doc" >&2
  echo "   - strip the ocr='hosted' Firecrawl round-trip from the Python wrapper" >&2
fi

echo "==> synced. Review the diff, then: ./scripts/build_native.sh"
