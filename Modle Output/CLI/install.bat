@echo off
setlocal enabledelayedexpansion

rem AduPort CLI one-click installer (Windows)
set "SCRIPT_DIR=%~dp0"
set "WHEEL="
for %%f in ("%SCRIPT_DIR%dist\aduport-*.whl") do set "WHEEL=%%f"

echo == AduPort CLI installer ==

python --version >nul 2>&1
if errorlevel 1 (
  echo ERROR: python not found. Install Python 3.11+ and check "Add to PATH".
  exit /b 1
)

if defined WHEEL (
  echo Installing from local wheel...
  python -m pip install "%WHEEL%"
) else (
  echo Installing from PyPI...
  python -m pip install aduport
)

echo.
echo Done. Verify:
echo   aduport --version
echo   aduport convert input.md -t docx -o out.docx
