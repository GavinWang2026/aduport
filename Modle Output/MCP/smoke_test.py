#!/usr/bin/env python3
"""AduPort MCP 端到端冒烟自检。

在「已安装 aduport[mcp] 的同一 Python 环境」中运行：:

    python smoke_test.py

它用 MCP 官方 client 拉起 ``aduport.mcp_server``（stdio），依次验证
initialize → list_tools → convert_text（文本 / 二进制）→ list_formats → capabilities。
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _text_of(result) -> str:
    """Extract the first text payload from a CallToolResult."""
    for item in result.content:
        text = getattr(item, "text", None)
        if text is not None:
            return text
    raise RuntimeError("tool result had no text content")


async def _run() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "aduport.mcp_server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[1/5] MCP initialize ................ OK")

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"[2/5] list_tools ..................... {names}")
            assert "convert_text" in names and "convert_document" in names

            r = await session.call_tool(
                "convert_text", {"content": "# 你好\n\n- 甲\n- 乙\n", "target_format": "html"}
            )
            payload = json.loads(_text_of(r))
            assert payload["ok"] and payload["encoding"] == "utf-8"
            assert "你好" in payload["data"]
            print("[3/5] convert_text (md -> html) ...... OK")

            r = await session.call_tool(
                "convert_text", {"content": "# 标题\n正文", "target_format": "docx"}
            )
            payload = json.loads(_text_of(r))
            assert payload["ok"] and payload["encoding"] == "base64"
            raw = base64.b64decode(payload["data"])
            assert raw[:2] == b"PK", "docx payload must be a ZIP (PK magic)"
            print(f"[4/5] convert_text (md -> docx) ...... OK ({len(raw)} bytes)")

            r = await session.call_tool("list_formats", {})
            fmt = json.loads(_text_of(r))
            print(f"[5/5] list_formats .................... {len(fmt['sources'])} 源 / {len(fmt['targets'])} 目标")

    print("\n全部通过：AduPort MCP server 可正常接入。")
    print("下一步：将 configs/ 中的模板填入 Claude Desktop / Claude Code / Cursor。")


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        print(f"\n冒烟自检失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
