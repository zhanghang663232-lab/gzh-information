from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import CaptureSession
from .proxy import MacProxyManager
from .workspace import Workspace


class CaptureTimeout(RuntimeError):
    pass


@contextmanager
def capture_session(workspace: Workspace, biz: str, *, consent: bool, port: int = 8899,
                    timeout: int = 180):
    manager = MacProxyManager(workspace.proxy_state)
    manager.snapshot()
    lease = workspace.root / "runtime" / "proxy-lease"
    lease.write_text(str(os.getpid()), encoding="utf-8")
    guardian = subprocess.Popen([
        sys.executable, "-m", "gzh_reader.proxy_guardian", "--parent", str(os.getpid()),
        "--state", str(workspace.proxy_state), "--lease", str(lease),
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    env = {
        **os.environ,
        "GZH_TARGET_BIZ": biz,
        "GZH_CREDENTIAL_PATH": str(workspace.credential),
        "GZH_ARTICLE_URL_LOG": str(workspace.root / "runtime" / "observed-article-urls.txt"),
    }
    addon = Path(__file__).with_name("capture_addon.py")
    mitm = None
    try:
        manager.enable_local(port, consent=consent)
        mitm = subprocess.Popen(
            ["mitmdump", "--listen-host", "127.0.0.1", "--listen-port", str(port), "-s", str(addon)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if workspace.credential.exists() and workspace.credential.stat().st_size > 3:
                data = json.loads(workspace.credential.read_text(encoding="utf-8"))
                if data.get("biz") == biz and data.get("cookie"):
                    now = datetime.now(UTC)
                    yield CaptureSession(
                        biz=biz, fresh_url=data["fresh_url"], cookie=data["cookie"],
                        params=data.get("params", {}),
                        created_at=now.isoformat(), expires_at=(now + timedelta(minutes=25)).isoformat(),
                    )
                    return
            if mitm.poll() is not None:
                raise RuntimeError("mitmproxy 启动失败；请运行 gzh-reader doctor")
            time.sleep(0.5)
        raise CaptureTimeout("未捕获到目标公众号凭据；请在桌面微信打开一篇尚未使用过的文章")
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
