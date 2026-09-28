#!/bin/zsh
set -euo pipefail
cd "${0:A:h}"

pause_before_exit() {
  if [[ -t 0 ]]; then
    read -k 1 "?按任意键关闭..."
    print
  fi
}

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import gzh_reader, fastapi, uvicorn, ApplicationServices, Quartz, Vision' >/dev/null 2>&1; then
  print "程序还未完整安装。请先双击同一文件夹中的 安装.command，完成后再启动。"
  pause_before_exit
  exit 1
fi
print "正在打开本地图形界面：http://127.0.0.1:8765"
print "读取期间请保留本窗口；在此按 Control-C 可以停止服务。"
if ! .venv/bin/python -m gzh_reader serve; then
  print "启动失败。如果已有读取界面正在运行，请使用原界面；否则保留上面的错误信息。"
  pause_before_exit
  exit 1
fi

