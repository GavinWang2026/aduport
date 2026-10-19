#!/usr/bin/env bash
# =====================================================================
# pack_native_wheels.sh — gather local / CI wheels into offline tarball
# ---------------------------------------------------------------------
# Usage:
#   # after maturin / workflow artifacts:
#   ./scripts/pack_native_wheels.sh [wheel_dir…] [outdir]
#   ADUPORT_NATIVE_WHEEL_DIR=./wheelhouse ./scripts/pack_native_wheels.sh
#
# Output:
#   <outdir>/aduport-native-wheels-<ver>.tar.gz + SHA256SUMS.txt
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VER="$(grep -E '^version[[:space:]]*=' "$ROOT/native/anydoc/python/Cargo.toml" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
OUT="$ROOT/enterprise/dist"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/aduport-pack-wheels.XXXXXX")"
cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

DIRS=()
while [[ $# -gt 0 ]]; do
  if [[ -d "$1" ]]; then
    DIRS+=("$1")
  else
    OUT="$1"
  fi
  shift
done

if [[ ${#DIRS[@]} -eq 0 ]]; then
  for cand in \
    "${ADUPORT_NATIVE_WHEEL_DIR:-}" \
    "$ROOT/native/anydoc/target/wheels" \
    "$ROOT/native/anydoc/python/dist" \
    "$ROOT/wheelhouse"
  do
    [[ -n "$cand" && -d "$cand" ]] || continue
    if compgen -G "$cand"/*.whl >/dev/null 2>&1; then
      DIRS+=("$cand")
    fi
  done
fi

if [[ ${#DIRS[@]} -eq 0 ]]; then
  echo "no wheel directories found. Build first:" >&2
  echo "  bash scripts/build_native.sh" >&2
  echo "  # or download CI artifacts into ./wheelhouse" >&2
  exit 1
fi

mkdir -p "$OUT" "$STAGING"
copied=0
for d in "${DIRS[@]}"; do
  for w in "$d"/*.whl; do
    [[ -f "$w" ]] || continue
    cp -f "$w" "$STAGING/"
    copied=$((copied + 1))
  done
done
if [[ "$copied" -eq 0 ]]; then
  echo "no .whl files copied" >&2
  exit 1
fi

TAR="$OUT/aduport-native-wheels-${VER}.tar.gz"
tar -czf "$TAR" -C "$STAGING" .
(
  cd "$OUT"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "aduport-native-wheels-${VER}.tar.gz" | tee SHA256SUMS-native.txt
  else
    sha256sum "aduport-native-wheels-${VER}.tar.gz" | tee SHA256SUMS-native.txt
  fi
)

echo "OK packed $copied wheel(s) → $TAR"
echo "Install: bash scripts/install_native_wheel.sh --tarball $TAR"
