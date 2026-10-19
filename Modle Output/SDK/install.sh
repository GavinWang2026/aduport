#!/usr/bin/env bash
# AduPort SDK 一键安装脚本（macOS / Linux）
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHEEL="$(ls "$HERE"/dist/aduport-*.whl 2>/dev/null | head -1 || true)"

echo "==> AduPort SDK 安装"
if [ -n "$WHEEL" ]; then
    echo "    使用本地 wheel：$(basename "$WHEEL")"
    python3 -m pip install --upgrade "$WHEEL"
else
    echo "    未找到本地 wheel，从 PyPI 安装 aduport"
    python3 -m pip install --upgrade aduport
fi

echo ""
echo "==> 验证安装"
python3 - <<'PY'
import aduport
from aduport import convert, capabilities
print("    AduPort", aduport.__version__, "| native =", capabilities()["native"])
print("    source formats:", len(aduport.SUPPORTED_SOURCE_FORMATS), "| target formats:", len(aduport.SUPPORTED_TARGET_FORMATS))
PY
echo ""
echo "==> 安装完成。用法见 README.md，示例见 examples/sdk_demo.py"
