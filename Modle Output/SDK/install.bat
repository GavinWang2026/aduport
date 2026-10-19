@echo off
REM AduPort SDK 一键安装脚本（Windows）
setlocal enabledelayedexpansion

set "HERE=%~dp0"
set "WHEEL="
for %%f in ("%HERE%dist\aduport-*.whl") do set "WHEEL=%%f"

echo ==^> AduPort SDK 安装
if defined WHEEL (
    echo     使用本地 wheel：%WHEEL%
    python -m pip install --upgrade "%WHEEL%"
) else (
    echo     未找到本地 wheel，从 PyPI 安装 aduport
    python -m pip install --upgrade aduport
)

echo.
echo ==^> 验证安装
python -c "import aduport; from aduport import convert, capabilities; print('    AduPort', aduport.__version__, '| native =', capabilities()['native'])"

echo.
echo ==^> 安装完成。用法见 README.md，示例见 examples\sdk_demo.py
