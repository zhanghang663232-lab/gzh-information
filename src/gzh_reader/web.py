from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .human_agent import MacHumanAccountCollector

app = FastAPI(title="gzh-information", docs_url=None, redoc_url=None)
STATE: dict = {"stage": "idle", "detail": {}, "running": False}


class CollectRequest(BaseModel):
    url: str
    output: str = str(Path.home() / "Documents" / "gzh-information-data")
    account_name: str | None = None
    max_articles: int | None = 20
    foreground_consent: bool = False


HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>公众号全量读取</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f4f7f5;color:#16251c;margin:0}
main{max-width:760px;margin:48px auto;background:white;padding:36px;border-radius:20px;box-shadow:0 10px 40px #173b2418}
h1{margin-top:0}label{display:block;margin:18px 0 7px;font-weight:650}input[type=text],input[type=password]{width:100%;box-sizing:border-box;padding:13px;border:1px solid #c7d2cb;border-radius:10px;font-size:16px}
.check{font-weight:400}.warning{background:#fff8e3;padding:14px;border-radius:10px;margin-top:18px}button{margin-top:22px;background:#137c45;color:white;border:0;border-radius:11px;padding:14px 22px;font-size:16px;cursor:pointer}button:disabled{opacity:.5}#status{margin-top:24px;white-space:pre-wrap;background:#eff7f1;padding:16px;border-radius:10px}</style></head>
<body><main><h1>公众号读取试跑</h1><p>先在 Mac 微信打开目标公众号主页，核对名称；试跑通过后可以从同一工作区续采。</p>
<label>文章链接</label><input id="url" type="text" placeholder="https://mp.weixin.qq.com/s?...">
<label>公众号名称（与微信主页一致）</label><input id="account_name" type="text" placeholder="例如：监所家属">
<label>最多保存篇数（留空为持续读取）</label><input id="max_articles" type="number" min="1" value="20">
<label>输出目录</label><input id="output" type="text" value="__OUTPUT__">
<div class="warning"><label class="check"><input id="consent" type="checkbox"> 我知道读取期间 Agent 会操作桌面微信窗口；请在暂时不用电脑时开始。程序不安装证书、不切换代理，也不调用第三方导出服务。</label></div>
<button id="start">开始/继续</button><div id="status">等待开始</div></main>
<script>const q=x=>document.querySelector(x),btn=q('#start'),status=q('#status');
btn.onclick=async()=>{btn.disabled=true;const body={url:q('#url').value,output:q('#output').value,account_name:q('#account_name').value.trim()||null,max_articles:q('#max_articles').value?Number(q('#max_articles').value):null,foreground_consent:q('#consent').checked};
let r=await fetch('/api/collect',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});if(!r.ok){status.textContent=await r.text();btn.disabled=false;return}poll()};
async function poll(){let s=await (await fetch('/api/status')).json();status.textContent=JSON.stringify(s,null,2);if(s.running)setTimeout(poll,900);else btn.disabled=false}</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return HTML.replace("__OUTPUT__", str(Path.home() / "Documents" / "gzh-information-data"))


@app.get("/api/status")
def status() -> dict:
    return STATE


@app.post("/api/collect")
def collect(request: CollectRequest) -> dict:
    if STATE["running"]:
        raise HTTPException(409, "已有任务正在运行")
    if not request.foreground_consent:
        raise HTTPException(400, "桌面模拟需要临时操作可见的微信窗口，请先勾选确认")
    STATE.update({"stage": "starting", "detail": {}, "running": True})

    def run() -> None:
        def progress(stage: str, detail: dict) -> None:
            STATE.update({"stage": stage, "detail": detail, "running": stage != "complete"})
        try:
            workspace = MacHumanAccountCollector(progress).collect(
                request.url, Path(request.output),
                account_name=request.account_name,
                max_articles=request.max_articles,
            )
            STATE.update({"stage": "complete", "detail": {"workspace": str(workspace)}, "running": False})
        except Exception as exc:  # noqa: BLE001 - background-task boundary reports failures to UI
            STATE.update({"stage": "failed", "detail": {"error": str(exc)}, "running": False})

    threading.Thread(target=run, daemon=True).start()
    return {"accepted": True}


def open_browser(port: int = 8765) -> None:
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
