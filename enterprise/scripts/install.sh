#!/usr/bin/env bash
# Load + start enterprise stack. Run from repo root or enterprise/.
# Report P1-3: never ship predictable change-me credentials into a live .env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENT="$ROOT/enterprise"

_rand() {
  # 48 hex chars (~192 bits) — usable as API key / HMAC secret
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    python3 -c 'import secrets; print(secrets.token_hex(24))'
  fi
}

_set_env_kv() {
  local key="$1" val="$2" file="$3"
  if grep -qE "^${key}=" "$file" 2>/dev/null; then
    # portable in-place replace (BSD/GNU sed)
    if sed --version >/dev/null 2>&1; then
      sed -i -E "s|^${key}=.*|${key}=${val}|" "$file"
    else
      sed -i '' -E "s|^${key}=.*|${key}=${val}|" "$file"
    fi
  else
    printf '%s=%s\n' "$key" "$val" >>"$file"
  fi
}

_is_placeholder() {
  local v="${1:-}"
  case "$v" in
    ""|change-me*|changeme*|REPLACE*|replace*|TODO*|todo*) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ ! -f "$ENT/.env" ]]; then
  echo "Missing $ENT/.env — seeding from .env.example with fresh secrets"
  cp "$ENT/.env.example" "$ENT/.env"
  API_KEY="$(_rand)"
  LIC_SECRET="$(_rand)"
  SESS_SECRET="$(_rand)"
  _set_env_kv "ADUPORT_API_KEY" "$API_KEY" "$ENT/.env"
  _set_env_kv "ADUPORT_LICENSE_SECRET" "$LIC_SECRET" "$ENT/.env"
  _set_env_kv "ADUPORT_SESSION_SECRET" "$SESS_SECRET" "$ENT/.env"
  _set_env_kv "ADUPORT_LICENSE_REQUIRED" "true" "$ENT/.env"
  _set_env_kv "ADUPORT_REQUIRE_AUTH" "1" "$ENT/.env"
  echo "Created $ENT/.env"
  echo "  ADUPORT_API_KEY=<generated>  (save this — needed for UI/API)"
  echo "  ADUPORT_LICENSE_SECRET=<generated>"
  echo "  ADUPORT_SESSION_SECRET=<generated>"
  echo "  Next: issue a license with this secret, set ADUPORT_LICENSE_KEY, then re-run."
  echo "  API key (once): $API_KEY"
else
  # Converge any leftover placeholders on existing installs (no `source` — values may contain spaces)
  cur_api=$(grep -E '^ADUPORT_API_KEY=' "$ENT/.env" | head -1 | cut -d= -f2- || true)
  cur_lic=$(grep -E '^ADUPORT_LICENSE_SECRET=' "$ENT/.env" | head -1 | cut -d= -f2- || true)
  cur_sess=$(grep -E '^ADUPORT_SESSION_SECRET=' "$ENT/.env" | head -1 | cut -d= -f2- || true)
  if _is_placeholder "${cur_api}"; then
    API_KEY="$(_rand)"
    _set_env_kv "ADUPORT_API_KEY" "$API_KEY" "$ENT/.env"
    echo "Rotated placeholder ADUPORT_API_KEY → $API_KEY"
  fi
  if _is_placeholder "${cur_lic}"; then
    LIC_SECRET="$(_rand)"
    _set_env_kv "ADUPORT_LICENSE_SECRET" "$LIC_SECRET" "$ENT/.env"
    echo "Rotated placeholder ADUPORT_LICENSE_SECRET (re-issue licenses with the new secret)"
  fi
  if _is_placeholder "${cur_sess}" || [[ ${#cur_sess} -lt 16 ]]; then
    SESS_SECRET="$(_rand)"
    _set_env_kv "ADUPORT_SESSION_SECRET" "$SESS_SECRET" "$ENT/.env"
    echo "Set/rotated ADUPORT_SESSION_SECRET (HMAC sessions; no hardcode fallback)"
  fi
fi

# v13 P2-11: refuse REQUIRE_AUTH=0 for enterprise private deploy
req_auth=$(grep -E '^ADUPORT_REQUIRE_AUTH=' "$ENT/.env" | head -1 | cut -d= -f2- || true)
req_auth="${req_auth:-1}"
case "${req_auth}" in
  0|false|FALSE|no|NO|off|OFF)
    echo "ERROR: ADUPORT_REQUIRE_AUTH=${req_auth} is not allowed for enterprise install." >&2
    echo "        Set ADUPORT_REQUIRE_AUTH=1 (fail-closed)." >&2
    exit 1
    ;;
esac
_set_env_kv "ADUPORT_REQUIRE_AUTH" "1" "$ENT/.env"

mkdir -p "$ENT/data/logs"
cd "$ENT"

if ! docker image inspect aduport-backend:enterprise >/dev/null 2>&1; then
  echo "Building enterprise images…"
  docker compose build
fi

docker compose up -d
echo "Waiting for health…"
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:8000/api/health" >/dev/null; then
    echo "OK — API http://127.0.0.1:8000  UI http://127.0.0.1:8080"
    echo "Next: ./enterprise/scripts/smoke_test.sh http://127.0.0.1:8000"
    if grep -qE '^ADUPORT_LICENSE_REQUIRED=true' "$ENT/.env" \
      && ! grep -qE '^ADUPORT_LICENSE_KEY=.+' "$ENT/.env"; then
      echo "NOTE: ADUPORT_LICENSE_REQUIRED=true but ADUPORT_LICENSE_KEY is empty —"
      echo "      convert APIs will 403 until you issue and set a license key."
    fi
    exit 0
  fi
  sleep 1
done
echo "Health check timed out" >&2
docker compose logs --tail=80 backend >&2 || true
exit 1
