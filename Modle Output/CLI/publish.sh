#!/usr/bin/env bash
# 发布 dist/ 产物到 PyPI（需 TWINE_USERNAME / TWINE_PASSWORD，或配置 ~/.pypirc 的 API token）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if ! ls dist/aduport-*.whl >/dev/null 2>&1; then
  echo "未找到 dist/ 产物。请先在 backend/ 目录运行：" >&2
  echo "    python -m build --sdist --wheel" >&2
  echo "然后把 backend/dist/aduport-* 复制到本目录 dist/。" >&2
  exit 1
fi

python3 -m pip install --quiet --upgrade build twine

echo "== twine check =="
python3 -m twine check dist/*

echo "== 上传到 PyPI =="
python3 -m twine upload dist/*
