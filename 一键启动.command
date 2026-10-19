#!/bin/bash
# macOS Finder：双击本文件即可启动 AduPort（无需在终端输入命令）
# 首次若被拦截：右键 → 打开 → 仍要打开
cd "$(dirname "$0")" || exit 1

clear 2>/dev/null || true
echo "╔══════════════════════════════════════╗"
echo "║         AduPort 一键启动           ║"
echo "╚══════════════════════════════════════╝"
echo
echo "正在启动…（首次构建可能较久，请稍候）"
echo

chmod +x ./start.sh 2>/dev/null || true

if ./start.sh; then
  echo
  echo "已在浏览器打开 Web UI（若未弹出请访问 http://127.0.0.1:8081 ）"
  echo "软件在后台运行；关闭本窗口不会停止服务。"
  echo "停止服务：双击「一键停止.command」"
else
  echo
  echo "启动失败。常见原因："
  echo "  1) Docker Desktop 未打开或未完全启动"
  echo "  2) 首次构建网络较慢/中断（可再双击重试，已构建层会缓存）"
  echo "  3) 查看详情：docker compose logs --tail=80"
fi

echo
# 保持窗口，方便查看报错；有 GUI 时用对话框，否则读键盘
if command -v osascript >/dev/null 2>&1; then
  osascript -e 'display dialog "AduPort 启动流程已结束。\n\n成功时浏览器应已打开 http://127.0.0.1:8081\n关闭本对话框即可。" buttons {"好"} default button 1 with title "AduPort"' >/dev/null 2>&1 || true
else
  read -r -p "按回车键关闭此窗口…" _
fi
