#!/bin/bash
# macOS Finder：双击停止 AduPort
cd "$(dirname "$0")" || exit 1
chmod +x ./start.sh 2>/dev/null || true
echo "正在停止 AduPort…"
./start.sh --stop
echo "已停止。"
if command -v osascript >/dev/null 2>&1; then
  osascript -e 'display dialog "AduPort 已停止。" buttons {"好"} default button 1 with title "AduPort"' >/dev/null 2>&1 || true
else
  read -r -p "按回车键关闭…" _
fi
