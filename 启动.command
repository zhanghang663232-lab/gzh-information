#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
if ! command -v uv >/dev/null 2>&1; then
  echo "请先双击 安装.command"
  read -k 1 "?按任意键关闭..."
  exit 1
fi
uv run gzh-reader serve

