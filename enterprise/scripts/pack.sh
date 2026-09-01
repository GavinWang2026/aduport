#!/usr/bin/env bash
# Build + export offline enterprise package (run on a networked build host).
#
# Usage:
#   ./enterprise/scripts/pack.sh [outdir]
#   PACK_DOCS_ONLY=1 ./enterprise/scripts/pack.sh   # docs/scripts bundle without docker images
#   ./enterprise/scripts/pack.sh --docs-only
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENT="$ROOT/enterprise"
OUT="${1:-$ENT/dist}"
TAG="${MDBRIDGE_IMAGE_TAG:-0.16.0}"
DOCS_ONLY="${PACK_DOCS_ONLY:-0}"

if [[ "${1:-}" == "--docs-only" ]]; then
  DOCS_ONLY=1
  OUT="${2:-$ENT/dist}"
elif [[ "${2:-}" == "--docs-only" ]]; then
  DOCS_ONLY=1
fi

mkdir -p "$OUT"
cd "$ENT"

have_docker() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

BUNDLE="$OUT/md-bridge-enterprise-${TAG}-bundle"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/scripts" "$BUNDLE/docs" "$BUNDLE/acceptance"

echo "== docs + scripts =="
cp "$ENT/docker-compose.yml" "$BUNDLE/"
cp "$ENT/.env.example" "$BUNDLE/"
for f in ACCEPTANCE.md OCR.md README.md LICENSE.md SCORE_93.md DELIVERY.md; do
  [[ -f "$ENT/$f" ]] && cp "$ENT/$f" "$BUNDLE/docs/"
done
if [[ -d "$ENT/delivery" ]]; then
  mkdir -p "$BUNDLE/docs/delivery"
  cp "$ENT/delivery/"*.md "$BUNDLE/docs/delivery/" 2>/dev/null || true
fi
cp "$ENT/scripts/install.sh" "$ENT/scripts/smoke_test.sh" "$ENT/scripts/pack.sh" "$BUNDLE/scripts/"
chmod +x "$BUNDLE/scripts/"*.sh

# Acceptance helpers for customer sign-off
cat > "$BUNDLE/acceptance/CHECKLIST.md" <<EOF
# 离线包验收速查（${TAG}）

1. \`gunzip -c md-bridge-enterprise-${TAG}.tar.gz | docker load\`（若本包含镜像）
2. \`cp .env.example .env\` 并填写 \`MDBRIDGE_API_KEY\` / License
3. \`./scripts/install.sh\`
4. \`./scripts/smoke_test.sh http://127.0.0.1:8000\`
5. 对照 \`docs/ACCEPTANCE.md\` 勾选 B1–B8
6. 扫描 OCR（可选）：见 \`docs/OCR.md\`，样张回归 \`pytest -m integration tests/test_scan_ocr_regression.py\`
EOF

cat > "$BUNDLE/MANIFEST.txt" <<EOF
product=md-bridge-enterprise
version=${TAG}
built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docs_only=${DOCS_ONLY}
host=$(hostname 2>/dev/null || echo unknown)
EOF

IMAGES_OK=0
if [[ "$DOCS_ONLY" == "1" ]]; then
  echo "== skip docker images (docs-only) =="
  echo "images=skipped_docs_only" >> "$BUNDLE/MANIFEST.txt"
else
  if ! have_docker; then
    echo "WARN: Docker daemon unavailable — packing docs/scripts only." >&2
    echo "      Start Docker/Colima and re-run without --docs-only for full offline images." >&2
    echo "images=missing_docker" >> "$BUNDLE/MANIFEST.txt"
  else
echo "== build =="
# Bake is soft-fail inside Dockerfile; prefer BAKE=1, allow override.
docker compose build

    echo "== save =="
    docker save \
      "md-bridge-backend:enterprise" \
      "md-bridge-frontend:enterprise" \
      | gzip > "$OUT/md-bridge-enterprise-${TAG}.tar.gz"
    cp "$OUT/md-bridge-enterprise-${TAG}.tar.gz" "$BUNDLE/"
    SIZE=$(du -h "$OUT/md-bridge-enterprise-${TAG}.tar.gz" | awk '{print $1}')
    echo "images=included size=${SIZE}" >> "$BUNDLE/MANIFEST.txt"
    IMAGES_OK=1
    echo "OK images: $OUT/md-bridge-enterprise-${TAG}.tar.gz ($SIZE)"
  fi
fi

(
  cd "$OUT"
  tar -czf "md-bridge-enterprise-${TAG}-bundle.tar.gz" "md-bridge-enterprise-${TAG}-bundle"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "md-bridge-enterprise-${TAG}-bundle.tar.gz" > "SHA256SUMS.txt"
    if [[ -f "md-bridge-enterprise-${TAG}.tar.gz" ]]; then
      shasum -a 256 "md-bridge-enterprise-${TAG}.tar.gz" >> "SHA256SUMS.txt"
    fi
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "md-bridge-enterprise-${TAG}-bundle.tar.gz" > "SHA256SUMS.txt"
    if [[ -f "md-bridge-enterprise-${TAG}.tar.gz" ]]; then
      sha256sum "md-bridge-enterprise-${TAG}.tar.gz" >> "SHA256SUMS.txt"
    fi
  fi
)

echo "OK bundle: $OUT/md-bridge-enterprise-${TAG}-bundle.tar.gz"
[[ -f "$OUT/SHA256SUMS.txt" ]] && echo "OK checksums: $OUT/SHA256SUMS.txt"

if [[ "$IMAGES_OK" -eq 1 ]]; then
  echo "Next on customer host: gunzip -c …tar.gz | docker load && ./scripts/install.sh"
else
  echo "Next: start Docker, re-run ./enterprise/scripts/pack.sh for full image tarball."
  exit 0
fi
