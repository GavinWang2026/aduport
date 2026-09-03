#!/usr/bin/env bash
# Start md-bridge API (sets Homebrew library path for WeasyPrint on macOS)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [[ -d /opt/homebrew/lib ]]; then
  export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
  export PKG_CONFIG_PATH="/opt/homebrew/lib/pkgconfig:/opt/homebrew/opt/libffi/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
elif [[ -d /usr/local/lib ]]; then
  export DYLD_FALLBACK_LIBRARY_PATH="/usr/local/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
fi

# Hugging Face mirror for docling model downloads (override with empty to use official)
if [[ -n "${MDBRIDGE_HF_ENDPOINT:-}" ]]; then
  export HF_ENDPOINT="${MDBRIDGE_HF_ENDPOINT}"
elif [[ -z "${HF_ENDPOINT:-}" ]]; then
  # Default China-friendly mirror; set HF_ENDPOINT=https://huggingface.co to force official
  export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
# Opt-in only: --reload + PDFium SIGTRAP can leave a half-dead worker while the
# parent still answers /health ("在线" but converts crash). Use MDBRIDGE_RELOAD=1 for DIY.
EXTRA_ARGS=()
if [[ "${MDBRIDGE_RELOAD:-0}" == "1" ]]; then
  EXTRA_ARGS+=(--reload)
fi
# Trust reverse-proxy X-Forwarded-For for rate-limit / audit IP (P1 #22)
if [[ "${MDBRIDGE_PROXY_HEADERS:-1}" == "1" ]]; then
  EXTRA_ARGS+=(--proxy-headers)
  EXTRA_ARGS+=(--forwarded-allow-ips="${MDBRIDGE_TRUSTED_PROXY_CIDR:-127.0.0.1,::1}")
fi

exec uvicorn main:app --host "$HOST" --port "$PORT" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}" "$@"
