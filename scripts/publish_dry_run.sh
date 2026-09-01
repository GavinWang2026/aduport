#!/usr/bin/env bash
# Local dry-run for PyPI packaging (no upload).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
python ../scripts/check_version.py
python -m pip install -q -U build twine
rm -rf dist build *.egg-info
python -m build
twine check dist/*
echo "Artifacts:"
ls -la dist/
echo "Ready. Tag a GitHub Release or run workflow_dispatch to publish."
