from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .agent import AgentState, MacWechatAgentProvider, NativeMacObserver
from .models import Account, ArticleSeed, Status
from .proxy import MacProxyManager
from .urls import article_stable_key, normalize_url
from .workspace import Workspace


def discover_articles_with_mac_agent(
    workspace: Workspace,
    account: Account,
    *,
    consent: bool,
    port: int = 8899,
) -> tuple[list[ArticleSeed], AgentState]:
    """Observe WeChat profile pagination and return only MITM-confirmed public URLs."""
    manager = MacProxyManager(workspace.proxy_state)
    manager.snapshot()
    runtime = workspace.root / "runtime"
    lease = runtime / "proxy-lease"
    url_log = runtime / "observed-article-urls.txt"
    url_log.write_text("", encoding="utf-8")
    os.chmod(url_log, 0o600)
    lease.write_text(str(os.getpid()), encoding="utf-8")
    guardian = subprocess.Popen(
        [
            sys.executable, "-m", "gzh_reader.proxy_guardian", "--parent", str(os.getpid()),
            "--state", str(workspace.proxy_state), "--lease", str(lease),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = {
        **os.environ,
        "GZH_TARGET_BIZ": account.biz,
        "GZH_CREDENTIAL_PATH": str(workspace.credential),
        "GZH_ARTICLE_URL_LOG": str(url_log),
    }
    addon = Path(__file__).with_name("capture_addon.py")
    mitm = None
    try:
        manager.enable_local(port, consent=consent)
        mitm = subprocess.Popen(
            ["mitmdump", "--listen-host", "127.0.0.1", "--listen-port", str(port), "-s", str(addon)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1)
        if mitm.poll() is not None:
            raise RuntimeError("mitmproxy 启动失败；请运行 gzh-reader doctor")
        observer = NativeMacObserver(url_log)
        urls, state = MacWechatAgentProvider(observer).enumerate_articles(account)
        seeds = [
            ArticleSeed(
                stable_key=article_stable_key(url=url),
                url=normalize_url(url),
                biz=account.biz,
                source="mac_agent",
                status=Status.PENDING,
            )
            for url in urls
        ]
        return seeds, state
    finally:
        if mitm and mitm.poll() is None:
            mitm.terminate()
            try:
                mitm.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mitm.kill()
        manager.restore()
        workspace.scrub_credential()
        lease.unlink(missing_ok=True)
        guardian.terminate()

