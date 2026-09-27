from __future__ import annotations

import multiprocessing
import json
import queue
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .task_worker import run_collect_task

app = FastAPI(title="gzh-information", docs_url=None, redoc_url=None)
STATE: dict = {"stage": "idle", "detail": {}, "running": False, "task_id": None}
_PROCESS: multiprocessing.Process | None = None
_STALL_SECONDS = 120
_NO_PROGRESS_SECONDS = 600


class CollectRequest(BaseModel):
    url: str
    output: str = str(Path.home() / "Documents" / "gzh-information-data")
    account_name: str | None = None
    max_articles: int | None = None
    max_new_articles: int = Field(default=5, ge=1, le=10)
    foreground_consent: bool = False
    deepseek_review: bool = False
    agent_mode: bool = False
    deepseek_api_key: str | None = None
    model_provider: Literal["deepseek", "doubao"] = "deepseek"
    model_id: str | None = None
    model_api_key: str | None = None


class ModelTestRequest(BaseModel):
    deepseek_api_key: str | None = None
    model_provider: Literal["deepseek", "doubao"] = "deepseek"
    model_id: str | None = None
    model_api_key: str | None = None


HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>公众号全量读取</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f4f7f5;color:#16251c;margin:0}
main{max-width:760px;margin:48px auto;background:white;padding:36px;border-radius:20px;box-shadow:0 10px 40px #173b2418}
h1{margin-top:0}label{display:block;margin:18px 0 7px;font-weight:650}input[type=text],input[type=password]{width:100%;box-sizing:border-box;padding:13px;border:1px solid #c7d2cb;border-radius:10px;font-size:16px}
.check{font-weight:400}.warning{background:#fff8e3;padding:14px;border-radius:10px;margin-top:18px}button{margin-top:22px;background:#137c45;color:white;border:0;border-radius:11px;padding:14px 22px;font-size:16px;cursor:pointer}button:disabled{opacity:.5}#status{margin-top:24px;white-space:pre-wrap;background:#eff7f1;padding:16px;border-radius:10px}</style></head>
<body><main><h1>公众号读取试跑</h1><p>支持任意公开公众号。先在 Mac 微信打开目标公众号主页；程序只在主页身份可核对时开始。试跑通过后可以从同一工作区续采。</p>
<label>文章链接</label><input id="url" type="text" placeholder="https://mp.weixin.qq.com/s?...">
<label>公众号名称（可留空；填写时须与微信主页一致）</label><input id="account_name" type="text" placeholder="留空则从唯一可见的公众号主页识别">
<label>工作区累计保存上限（可留空）</label><input id="max_articles" type="number" min="1" placeholder="留空；每轮仍受下面的新增上限保护">
<label>本轮最多新增篇数（1-10，建议 5；不是整个账号的上限）</label><input id="max_new_articles" type="number" min="1" max="10" value="5">
<label>输出目录</label><input id="output" type="text" value="__OUTPUT__">
<button id="visual_check" type="button">检查能否读取微信画面</button><span id="visual_status"></span>
<label class="check"><input id="deepseek_review" type="checkbox"> OCR 无法识别文章卡片时，最多调用所选模型 5 次复核公开列表文字（不发送正文或截图）</label>
<label class="check"><input id="agent_mode" type="checkbox"> Agent 模式：由所选模型排序当前页面的文章；程序验证编号后操作微信</label>
<label>复核模型</label><select id="model_provider"><option value="deepseek">DeepSeek</option><option value="doubao">豆包（火山方舟）</option></select>
<label>模型 ID（可留空使用默认值）</label><input id="model_id" type="text" placeholder="例如 doubao-seed-2-1-lite-260915">
<label>模型 API key（仅本次使用，不保存）</label><input id="model_key" type="password" autocomplete="off" placeholder="可留空并使用 DEEPSEEK_API_KEY 或 ARK_API_KEY">
<button id="test_model" type="button">测试模型连接</button><span id="model_status"></span>
<div class="warning"><label class="check"><input id="consent" type="checkbox"> 我知道读取期间 Agent 会操作桌面微信窗口；请在暂时不用电脑时开始。程序不安装证书、不切换代理，也不调用第三方导出服务。</label></div>
<button id="start">开始/继续</button> <button id="stop" type="button" disabled>停止本轮</button><div id="status">等待开始</div></main>
<script>const q=x=>document.querySelector(x),btn=q('#start'),status=q('#status');
q('#visual_check').onclick=async()=>{let target=q('#visual_status');target.textContent='正在检查画面…';try{let r=await fetch('/api/visual-check',{method:'POST'}),v=await r.json();target.textContent=v.calibration?.capture_readable?(v.wechat?.capture_readable?'微信画面可读取':(v.wechat?.reason||'当前微信画面不可读取')):'本机截图或文字识别不可用，请检查屏幕录制权限'}catch{target.textContent='检查失败，请稍后重试'}};
q('#test_model').onclick=async()=>{q('#model_status').textContent='正在测试…';let r=await fetch('/api/model/test',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({model_provider:q('#model_provider').value,model_id:q('#model_id').value.trim()||null,model_api_key:q('#model_key').value||null})});q('#model_status').textContent=r.ok?'连接成功':('连接失败：'+await r.text())};
btn.onclick=async()=>{btn.disabled=true;const body={url:q('#url').value,output:q('#output').value,account_name:q('#account_name').value.trim()||null,max_articles:q('#max_articles').value?Number(q('#max_articles').value):null,max_new_articles:Number(q('#max_new_articles').value),foreground_consent:q('#consent').checked,deepseek_review:q('#deepseek_review').checked,agent_mode:q('#agent_mode').checked,model_provider:q('#model_provider').value,model_id:q('#model_id').value.trim()||null,model_api_key:(q('#deepseek_review').checked||q('#agent_mode').checked)?(q('#model_key').value||null):null};
let r=await fetch('/api/collect',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});if(!r.ok){status.textContent=await r.text();btn.disabled=false;return}poll()};
q('#stop').onclick=async()=>{await fetch('/api/stop',{method:'POST'});poll()};
async function poll(){let s=await (await fetch('/api/status')).json();status.textContent=JSON.stringify(s,null,2);q('#stop').disabled=!s.running;if(s.running)setTimeout(poll,900);else btn.disabled=false}</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return HTML.replace("__OUTPUT__", str(Path.home() / "Documents" / "gzh-information-data"))


@app.get("/api/status")
def status() -> dict:
    return dict(STATE)


@app.post("/api/visual-check")
def visual_check() -> dict:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "gzh_reader.visual_probe"],
            capture_output=True, text=True, check=False, timeout=30,
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise HTTPException(500, "画面诊断未完成；请确认程序有屏幕录制权限") from None


@app.post("/api/model/test")
def model_test(request: ModelTestRequest) -> dict:
    from .model_client import create_client

    try:
        return create_client(
            request.model_provider,
            request.model_api_key or request.deepseek_api_key,
            request.model_id,
        ).test()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/collect")
def collect(request: CollectRequest) -> dict:
    if STATE["running"] or (_PROCESS is not None and _PROCESS.is_alive()):
        raise HTTPException(409, "已有任务正在运行")
    if not request.foreground_consent:
        raise HTTPException(400, "桌面模拟需要临时操作可见的微信窗口，请先勾选确认")
    task_id = uuid.uuid4().hex
    STATE.update({"task_id": task_id, "stage": "starting", "detail": {}, "running": True})
    try:
        _launch_task(request.model_dump(), task_id)
    except (OSError, RuntimeError) as exc:
        STATE.update({"stage": "failed", "detail": {"error": type(exc).__name__}, "running": False})
        raise HTTPException(500, "无法启动采集进程") from None
    return {"accepted": True, "task_id": task_id}


def _launch_task(payload: dict, task_id: str) -> None:
    global _PROCESS
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    process = context.Process(
        target=run_collect_task, args=(payload, events), daemon=True,
    )
    process.start()
    _PROCESS = process
    threading.Thread(target=_monitor_task, args=(process, events, task_id), daemon=True).start()


def _monitor_task(process, events, task_id: str) -> None:
    last_progress = time.monotonic()
    last_material_progress = last_progress
    highest_saved = 0
    seen_viewports: set[str] = set()
    while True:
        if STATE.get("task_id") == task_id and not STATE.get("running"):
            break
        try:
            event = events.get(timeout=2)
        except queue.Empty:
            if STATE.get("task_id") == task_id and not STATE.get("running"):
                break
            if not process.is_alive():
                try:
                    event = events.get(timeout=0.2)
                except queue.Empty:
                    STATE.update({
                        "stage": "failed", "detail": {"error": "采集进程意外退出；请从断点继续"},
                        "running": False,
                    })
                    break
            else:
                if time.monotonic() - last_progress >= _STALL_SECONDS:
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                    STATE.update({
                        "stage": "failed", "detail": {"error": "采集动作超过 120 秒无进展，进程已停止；请从断点继续"},
                        "running": False,
                    })
                    break
                if time.monotonic() - last_material_progress >= _NO_PROGRESS_SECONDS:
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                    STATE.update({
                        "stage": "failed", "detail": {
                            "error": "10 分钟没有新增文章或新列表位置，已停止；请从断点继续"
                        }, "running": False,
                    })
                    break
                continue
        if STATE.get("task_id") != task_id:
            break
        if not STATE.get("running"):
            break
        last_progress = time.monotonic()
        detail = event.get("detail", {})
        if event.get("stage") == "human_saved":
            saved = detail.get("new_in_run")
            if isinstance(saved, int) and saved > highest_saved:
                highest_saved = saved
                last_material_progress = last_progress
        elif event.get("stage") == "human_viewport":
            fingerprint = detail.get("fingerprint")
            if isinstance(fingerprint, str) and fingerprint not in seen_viewports:
                seen_viewports.add(fingerprint)
                last_material_progress = last_progress
        STATE.update({"stage": event["stage"], "detail": event.get("detail", {}),
                      "running": event.get("kind") != "final"})
        if event.get("kind") == "final":
            break
    process.join(timeout=2)


@app.post("/api/stop")
def stop() -> dict:
    if _PROCESS is None or not STATE.get("running"):
        raise HTTPException(409, "当前没有正在运行的采集任务")
    if _PROCESS.is_alive():
        _PROCESS.terminate()
        _PROCESS.join(timeout=5)
        if _PROCESS.is_alive():
            _PROCESS.kill()
    STATE.update({
        "stage": "interrupted", "detail": {"reason": "用户停止；可从工作区断点继续"},
        "running": False,
    })
    return {"stopped": True, "task_id": STATE.get("task_id")}


def open_browser(port: int = 8765) -> None:
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
