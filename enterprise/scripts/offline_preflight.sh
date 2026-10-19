#!/usr/bin/env bash
# Offline pack preflight — does NOT build images; fails fast if build machine is not ready.
# Usage: ./enterprise/scripts/offline_preflight.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

fail=0
warn() { echo "WARN: $*"; }
die() { echo "FAIL: $*"; fail=1; }
ok() { echo "OK:   $*"; }

echo "== AduPort offline pack preflight =="

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    ok "docker available"
  else
    die "docker installed but daemon not usable (docker info failed)"
  fi
else
  die "docker not found — full offline pack requires Docker 20+"
fi

# Disk free on repo filesystem (best-effort, GB)
free_gb="$(df -P . 2>/dev/null | awk 'NR==2{printf "%d", $4/1024/1024}')"
if [[ -n "${free_gb:-}" ]]; then
  if [[ "$free_gb" -ge 40 ]]; then
    ok "disk free ≈ ${free_gb} GB (≥40 recommended)"
  else
    warn "disk free ≈ ${free_gb} GB (<40 GB) — full bake may fail"
  fi
fi

if [[ -x ./enterprise/scripts/pack.sh ]]; then
  ok "pack.sh present"
else
  die "enterprise/scripts/pack.sh missing or not executable"
fi

if [[ -f ./enterprise/README.md ]]; then
  ok "enterprise/README.md present"
else
  die "enterprise/README.md missing"
fi

# Docs-only always works; remind operator
echo ""
echo "Next:"
echo "  # formal customer delivery (needs Docker + disk)"
echo "  ./enterprise/scripts/pack.sh"
echo "  # docs-only preview (NOT a customer install bundle)"
echo "  ./enterprise/scripts/pack.sh --docs-only"
echo ""
echo "See enterprise/README.md"

if [[ "$fail" -ne 0 ]]; then
  echo "PREFLIGHT FAILED"
  exit 1
fi
echo "PREFLIGHT PASSED (build machine looks ready; run pack.sh to produce the bundle)"
exit 0
