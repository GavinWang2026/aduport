#!/usr/bin/env bash
# AduPort MCP 一键安装（macOS / Linux）
# 安装 aduport[mcp]（含 mcp SDK），安装后即可在 Claude/Cursor 中接入。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WHEEL="$(ls "$HERE"/dist/aduport-*.whl 2>/dev/null | head -1 || true)"

echo "==> AduPort MCP 安装"
if [ -n "$WHEEL" ]; then
    echo "    使用本地 wheel：$(basename "$WHEEL")"
    python3 -m pip install --upgrade "${WHEEL}[mcp]"
else
    echo "    未找到本地 wheel，从 PyPI 安装 aduport[mcp]"
    python3 -m pip install --upgrade "aduport[mcp]"
fi

echo ""
echo "==> 验证安装（构建 MCP server，不阻塞）"
python3 - <<'PY'
import mcp
from aduport.mcp_server import _build_server
server = _build_server()
print("    mcp SDK:", getattr(mcp, "__version__", "?"))
print("    AduPort MCP server: OK")
PY

echo ""
echo "==> 安装完成。"
echo "    1. 找到入口命令绝对路径：which aduport-mcp"
echo "    2. 按 README.md 将 configs/ 模板填入 Claude / Cursor"
echo "    3. 运行 smoke_test.py 做端到端自检：python3 smoke_test.py"
