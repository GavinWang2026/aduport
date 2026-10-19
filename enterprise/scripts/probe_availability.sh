#!/usr/bin/env bash
# Probe AduPort liveness and append one JSONL record (SLA availability evidence).
# Usage:
#   ADUPORT_PROBE_URL=http://127.0.0.1:8000/api/health \
#   ADUPORT_PROBE_LOG=/data/logs/availability.jsonl \
#   ./enterprise/scripts/probe_availability.sh
set -euo pipefail

URL="${ADUPORT_PROBE_URL:-http://127.0.0.1:8000/api/health}"
LOG="${ADUPORT_PROBE_LOG:-./availability.jsonl}"
TIMEOUT="${ADUPORT_PROBE_TIMEOUT:-5}"
TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

code="000"
body=""
ok=0
if body="$(curl -fsS -m "$TIMEOUT" -w "\n%{http_code}" "$URL" 2>/dev/null)"; then
  code="$(printf '%s' "$body" | tail -n1)"
  payload="$(printf '%s' "$body" | sed '$d')"
  if [[ "$code" == "200" ]] && printf '%s' "$payload" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
    ok=1
  fi
else
  payload=""
  code="000"
fi

# Minimal JSON without jq dependency
esc_payload="$(printf '%s' "$payload" | tr '\n' ' ' | sed 's/"/\\"/g' | cut -c1-200)"
printf '{"ts":"%s","url":"%s","http_code":"%s","ok":%s,"snippet":"%s"}\n' \
  "$TS" "$URL" "$code" "$ok" "$esc_payload" >>"$LOG"

if [[ "$ok" -eq 1 ]]; then
  exit 0
fi
exit 1
