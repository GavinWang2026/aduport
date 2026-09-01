#!/usr/bin/env bash
# Load + start enterprise stack. Run from repo root or enterprise/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENT="$ROOT/enterprise"

if [[ ! -f "$ENT/.env" ]]; then
  echo "Missing $ENT/.env — copy from .env.example and set MDBRIDGE_API_KEY"
  cp "$ENT/.env.example" "$ENT/.env"
  echo "Created $ENT/.env (edit API key before production use)"
fi

mkdir -p "$ENT/data/logs"
cd "$ENT"

if ! docker image inspect md-bridge-backend:enterprise >/dev/null 2>&1; then
  echo "Building enterprise images…"
  docker compose build
fi

docker compose up -d
echo "Waiting for health…"
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:8000/api/health" >/dev/null; then
    echo "OK — API http://127.0.0.1:8000  UI http://127.0.0.1:8080"
    echo "Next: ./enterprise/scripts/smoke_test.sh http://127.0.0.1:8000"
    exit 0
  fi
  sleep 1
done
echo "Health check timed out" >&2
docker compose logs --tail=80 backend >&2 || true
exit 1
