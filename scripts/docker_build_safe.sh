#!/usr/bin/env bash
# =====================================================================
# docker_build_safe.sh — build backend image from an ASCII-safe context
# ---------------------------------------------------------------------
# Colima / Docker Desktop sometimes serve a stale build context when the
# project path contains non-ASCII characters (this repo's directory name
# may use U+2011 NON-BREAKING HYPHEN). This helper rsyncs to /tmp and builds.
#
# Usage:
#   ./scripts/docker_build_safe.sh                     # slim + INSTALL_NATIVE=1
#   ./scripts/docker_build_safe.sh full                # backend/Dockerfile
#   TAG=my:tag ./scripts/docker_build_safe.sh
# =====================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KIND="${1:-slim}"
TAG="${TAG:-aduport-backend:local}"
TMP="${ADUPORT_DOCKER_CTX:-/tmp/aduport-build}"

case "$KIND" in
  slim) DOCKERFILE="backend/Dockerfile.slim" ;;
  full) DOCKERFILE="backend/Dockerfile" ;;
  *) echo "usage: $0 [slim|full]" >&2; exit 1 ;;
esac

echo "==> syncing $ROOT -> $TMP (ASCII-safe Docker context)"
mkdir -p "$TMP"
rsync -a --delete \
  --exclude '.git' \
  --exclude '**/__pycache__' \
  --exclude '**/.venv' \
  --exclude 'native/anydoc/target' \
  --exclude 'frontend/node_modules' \
  "$ROOT/" "$TMP/"

echo "==> docker build -f $DOCKERFILE -t $TAG"
docker build \
  --build-arg INSTALL_NATIVE="${INSTALL_NATIVE:-1}" \
  --build-arg INSTALL_OCR="${INSTALL_OCR:-0}" \
  -f "$TMP/$DOCKERFILE" \
  -t "$TAG" \
  "$TMP"

echo "==> done: $TAG"
