#!/usr/bin/env bash
# AduPort CLI 一键安装（macOS / Linux）
# 优先 pipx（隔离安装），回退 pip --user；支持从本目录 dist/ 离线安装。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WHEEL="$(ls "$SCRIPT_DIR"/dist/aduport-*.whl 2>/dev/null | head -1 || true)"

echo "== AduPort CLI 安装 =="

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误：未找到 python3，请先安装 Python 3.11+（https://www.python.org）" >&2
  exit 1
fi
echo "Python: $(python3 --version)"

if command -v pipx >/dev/null 2>&1; then
  echo "使用 pipx（隔离环境）..."
  if [ -n "$WHEEL" ]; then
    pipx install "$WHEEL" --force
  else
    pipx install aduport
  fi
else
  echo "使用 pip --user ..."
  if [ -n "$WHEEL" ]; then
    python3 -m pip install --user "$WHEEL"
  else
    python3 -m pip install --user aduport
  fi
  echo "提示：若 aduport 命令找不到，请确认 ~/.local/bin 在 PATH 中。"
fi

echo ""
echo "安装完成。验证："
echo "  aduport --version"
echo "  aduport convert input.md -t docx -o out.docx"
