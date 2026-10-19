#!/usr/bin/env bash
# Summarize probe_availability.sh JSONL for one calendar month (UTC dates in ts).
# Usage: ./enterprise/scripts/summarize_availability.sh /data/logs/availability.jsonl 2026-09
set -euo pipefail

LOG="${1:?usage: summarize_availability.sh <jsonl> <YYYY-MM>}"
MONTH="${2:?usage: summarize_availability.sh <jsonl> <YYYY-MM>}"

if [[ ! -f "$LOG" ]]; then
  echo "missing log: $LOG" >&2
  exit 2
fi

total=0
ok=0
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  case "$line" in
    *"\"ts\":\"${MONTH}"*) ;;
    *) continue ;;
  esac
  total=$((total + 1))
  case "$line" in
    *"\"ok\":1"*|*"\"ok\": true"*) ok=$((ok + 1)) ;;
  esac
done <"$LOG"

fail=$((total - ok))
pct="n/a"
if [[ "$total" -gt 0 ]]; then
  pct="$(awk -v o="$ok" -v t="$total" 'BEGIN{printf "%.3f", (o/t)*100}')"
fi

echo "month=$MONTH"
echo "samples=$total"
echo "ok=$ok"
echo "fail=$fail"
echo "availability_pct=$pct"
echo "target_default=99.5"
echo "note=Exclude planned maintenance and customer-side outages per SLA before final sign-off."
