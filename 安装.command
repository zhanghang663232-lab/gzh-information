#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

pause_before_exit() {
  if [[ -t 0 ]]; then
    read -k 1 "?按任意键关闭..."
    print
  fi
}

installation_failed() {
  local install_status=$?
  print "安装未完成。请保留本窗口中的错误信息，修复后重新双击 安装.command。"
  pause_before_exit
  exit "$install_status"
}
trap installation_failed ZERR

if [[ "$(/usr/bin/uname -s)" != "Darwin" || "$(/usr/bin/uname -m)" != "arm64" ]]; then
  print "此安装包面向 Apple Silicon Mac。请在 Mac 原生终端运行，勿使用 Rosetta。"
  pause_before_exit
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  print "首次安装：正在下载 Python 环境管理器 uv，需要联网。"
  curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh | sh
fi
print "正在安装固定版本依赖与 Python 3.12；首次运行可能需要几分钟。"
uv sync --locked --python 3.12 --extra mac --no-default-groups
.venv/bin/python -c 'import gzh_reader, fastapi, uvicorn, ApplicationServices, Quartz, Vision; print("程序与 Mac 依赖检查通过。")'
print "安装完成。双击 启动.command 即可使用。"
pause_before_exit
