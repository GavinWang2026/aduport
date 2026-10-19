#!/usr/bin/env bash
# Build full backend image with OCR extras (INSTALL_OCR=1, ADUPORT_OCR_ENABLED=1).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export INSTALL_OCR=1
export TAG="${TAG:-aduport-backend:ocr}"
exec bash "$ROOT/scripts/docker_build_safe.sh" full
