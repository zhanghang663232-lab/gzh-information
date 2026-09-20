#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
if ! command -v uv >/dev/null 2>&1; then
  echo "正在安装 uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv sync --extra capture --extra mac
echo "安装完成。双击 启动.command 即可使用。"
read -k 1 "?按任意键关闭..."

