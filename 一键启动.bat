@echo off
REM Windows：双击本文件启动 AduPort（需已安装 Docker Desktop）
cd /d "%~dp0"
title AduPort 一键启动
echo ========================================
echo   AduPort 一键启动
echo ========================================
echo.
where docker >nul 2>&1
if errorlevel 1 (
  echo 未检测到 Docker。请先安装并启动 Docker Desktop。
  pause
  exit /b 1
)
if not exist start.sh (
  echo 缺少 start.sh
  pause
  exit /b 1
)
REM Prefer Git Bash / WSL if available for start.sh
where bash >nul 2>&1
if not errorlevel 1 (
  bash ./start.sh
) else (
  echo 请安装 Git for Windows（提供 bash）后重试，或在 WSL 中运行 ./start.sh
  pause
  exit /b 1
)
echo.
echo 浏览器应已打开 http://127.0.0.1:8081
pause
