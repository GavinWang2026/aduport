#!/usr/bin/env bash
# =====================================================================
# install_native_wheel.sh — install prebuilt aduport-native (no Rust)
# ---------------------------------------------------------------------
# Prefer offline / Release wheels over compiling. Main AduPort PyPI
# stays Python-only; native ships as GitHub Release assets.
#
# Usage:
#   ./scripts/install_native_wheel.sh --dir /path/to/wheels
#   ./scripts/install_native_wheel.sh --tarball aduport-native-wheels-0.2.4.tar.gz
#   ./scripts/install_native_wheel.sh --url https://…/aduport-native-wheels-0.2.4.tar.gz
#   ./scripts/install_native_wheel.sh --release [tag]   # GitHub Releases
#   ADUPORT_NATIVE_WHEEL_DIR=./wheels ./scripts/install_native_wheel.sh
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${ADUPORT_GITHUB_REPO:-GavinWang2026/aduport}"

# Parse flags before any version grep so --help works even if Cargo.toml is missing.
MODE=""
ARG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) MODE=dir; ARG="${2:-}"; shift 2 ;;
    --tarball) MODE=tarball; ARG="${2:-}"; shift 2 ;;
    --url) MODE=url; ARG="${2:-}"; shift 2 ;;
    --release) MODE=release; ARG="${2:-}"; shift; [[ $# -gt 0 && "${1:0:1}" != "-" ]] && { ARG="$1"; shift; } || true ;;
    -h|--help)
      sed -n '2,16p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $1 (see --help)" >&2
      exit 2
      ;;
  esac
done

_cargo_version() {
  # POSIX character class — macOS BSD grep has no \s
  local f="$1"
  grep -E '^version[[:space:]]*=' "$f" 2>/dev/null | head -1 | sed -E 's/.*"([^"]+)".*/\1/' || true
}

NATIVE_VER="$(_cargo_version "$ROOT/native/anydoc/python/Cargo.toml")"
if [[ -z "$NATIVE_VER" ]]; then
  echo "cannot read version from native/anydoc/python/Cargo.toml" >&2
  exit 1
fi
ASSET="aduport-native-wheels-${NATIVE_VER}.tar.gz"

PY="${ADUPORT_PYTHON:-$ROOT/backend/.venv/bin/python}"
if [ ! -x "$PY" ]; then
  if [ ! -d "$ROOT/backend/.venv" ]; then
    echo "==> creating backend/.venv"
    python3 -m venv "$ROOT/backend/.venv"
  fi
  PY="$ROOT/backend/.venv/bin/python"
fi
if [ ! -x "$PY" ]; then PY="$(command -v python3)"; fi

if [[ -z "$MODE" ]]; then
  if [[ -n "${ADUPORT_NATIVE_WHEEL_DIR:-}" ]]; then
    MODE=dir
    ARG="$ADUPORT_NATIVE_WHEEL_DIR"
  elif [[ -n "${ADUPORT_NATIVE_WHEEL_URL:-}" ]]; then
    MODE=url
    ARG="$ADUPORT_NATIVE_WHEEL_URL"
  elif [[ -n "${ADUPORT_NATIVE_WHEEL_TARBALL:-}" ]]; then
    MODE=tarball
    ARG="$ADUPORT_NATIVE_WHEEL_TARBALL"
  else
    MODE=release
    ARG=""
  fi
fi

TMP=""
cleanup() {
  [[ -n "$TMP" && -d "$TMP" ]] && rm -rf "$TMP"
}
trap cleanup EXIT

_resolve_wheel_dir() {
  local src="$1"
  if [[ -d "$src" ]]; then
    echo "$src"
    return
  fi
  if [[ -f "$src" && "$src" == *.tar.gz ]]; then
    TMP="$(mktemp -d "${TMPDIR:-/tmp}/aduport-native.XXXXXX")"
    tar -xzf "$src" -C "$TMP"
    # tarball may contain flat wheels or a single subdir
    if compgen -G "$TMP"/*.whl >/dev/null 2>&1; then
      echo "$TMP"
    else
      local sub
      sub="$(find "$TMP" -maxdepth 2 -name '*.whl' -print -quit | xargs -I{} dirname {} | head -1)"
      echo "${sub:-$TMP}"
    fi
    return
  fi
  echo "not a wheel dir or tarball: $src" >&2
  return 1
}

_download() {
  local url="$1" dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$dest" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$dest" "$url"
  else
    echo "need curl or wget to download $url" >&2
    return 1
  fi
}

WHEEL_DIR=""
case "$MODE" in
  dir)
    [[ -n "$ARG" ]] || { echo "--dir requires a path" >&2; exit 2; }
    WHEEL_DIR="$(_resolve_wheel_dir "$ARG")"
    ;;
  tarball)
    [[ -n "$ARG" ]] || { echo "--tarball requires a path" >&2; exit 2; }
    WHEEL_DIR="$(_resolve_wheel_dir "$ARG")"
    ;;
  url)
    [[ -n "$ARG" ]] || { echo "--url requires a URL" >&2; exit 2; }
    TMP="$(mktemp -d "${TMPDIR:-/tmp}/aduport-native.XXXXXX")"
    echo "==> downloading $ARG"
    _download "$ARG" "$TMP/$ASSET"
    WHEEL_DIR="$(_resolve_wheel_dir "$TMP/$ASSET")"
    ;;
  release)
    TAG="${ARG:-${ADUPORT_NATIVE_RELEASE_TAG:-}}"
    if [[ -z "$TAG" ]]; then
      # Prefer release whose assets include our wheel bundle; fall back to latest.
      if command -v gh >/dev/null 2>&1; then
        TAG="$(gh release list -R "$REPO" --limit 20 2>/dev/null | awk '{print $1}' | head -1 || true)"
      fi
      TAG="${TAG:-latest}"
    fi
    if [[ "$TAG" == "latest" ]]; then
      URL="https://github.com/${REPO}/releases/latest/download/${ASSET}"
    else
      URL="https://github.com/${REPO}/releases/download/${TAG}/${ASSET}"
    fi
    TMP="$(mktemp -d "${TMPDIR:-/tmp}/aduport-native.XXXXXX")"
    echo "==> fetching prebuilt ${ASSET} (${TAG}) from ${REPO}"
    if ! _download "$URL" "$TMP/$ASSET"; then
      echo "prebuilt wheel bundle not found at $URL" >&2
      echo "hint: bash scripts/build_native.sh  # compile from source" >&2
      echo "      or set ADUPORT_NATIVE_WHEEL_DIR / --dir" >&2
      exit 1
    fi
    WHEEL_DIR="$(_resolve_wheel_dir "$TMP/$ASSET")"
    ;;
esac

if ! compgen -G "$WHEEL_DIR"/*.whl >/dev/null 2>&1; then
  echo "no .whl files in $WHEEL_DIR" >&2
  exit 1
fi

echo "==> installing aduport-native from $WHEEL_DIR (pip find-links)"
"$PY" -m pip install --upgrade --force-reinstall --no-deps \
  --no-index --find-links "$WHEEL_DIR" "aduport-native==${NATIVE_VER}" \
  || "$PY" -m pip install --upgrade --force-reinstall --no-deps \
    --no-index --find-links "$WHEEL_DIR" aduport-native

echo "==> verifying"
cd "$ROOT/backend"
"$PY" - <<'PY'
import importlib.metadata as md
import aduport_native._doc as d

required = ("to_markdown_bytes", "to_document", "format_from_bytes")
missing = [n for n in required if not hasattr(d, n)]
if missing:
    raise SystemExit(f"FAIL missing exports: {missing}")
ver = md.version("aduport-native")
print(f"OK aduport-native {ver} · exports {', '.join(required)}")
PY
echo "==> done (prebuilt; no Rust compile)"
