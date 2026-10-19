@echo off
REM AduPort MCP 一键安装（Windows）
REM 安装 aduport[mcp]（含 mcp SDK），安装后即可在 Claude/Cursor 中接入。
setlocal enabledelayedexpansion

echo ==^> AduPort MCP 安装

where python >nul 2>nul
if errorlevel 1 (
    echo 错误：未找到 python，请先安装 Python 3.11+（https://www.python.org）
    exit /b 1
)

set "WHEEL="
for %%f in ("%~dp0dist\aduport-*.whl") do if not defined WHEEL set "WHEEL=%%f"

if defined WHEEL (
    echo     使用本地 wheel：%WHEEL%
    python -m pip install --upgrade "%WHEEL%[mcp]"
) else (
    echo     未找到本地 wheel，从 PyPI 安装 aduport[mcp]
    python -m pip install --upgrade "aduport[mcp]"
)

echo.
echo ==^> 验证安装
python -c "import mcp; from aduport.mcp_server import _build_server; _build_server(); print('    AduPort MCP server: OK')"

echo.
echo ==^> 安装完成。
echo     1. 找到入口命令：where aduport-mcp
echo     2. 按 README.md 将 configs/ 模板填入 Claude / Cursor
echo     3. 运行 python smoke_test.py 做端到端自检
endlocal
