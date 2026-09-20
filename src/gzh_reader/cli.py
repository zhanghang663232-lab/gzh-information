from __future__ import annotations

import argparse
import json
import platform
import shutil
from pathlib import Path

from .audit import audit_workspace
from .exports import export_all
from .orchestrator import Collector
from .proxy import MacProxyManager
from .storage import Store
from .workspace import Workspace


def _progress(stage: str, detail: dict) -> None:
    print(json.dumps({"stage": stage, **detail}, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gzh-reader", description="Mac 公众号公开数据归档工具")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--url", required=True)
    collect.add_argument("--output", type=Path, default=Path.home() / "Documents" / "gzh-information-data")
    collect.add_argument("--api-key", help="不推荐：会进入 shell 历史；省略后从隐藏输入读取")
    collect.add_argument("--engagement", action="store_true")
    collect.add_argument("--allow-system-proxy", action="store_true")
    resume = sub.add_parser("resume")
    resume.add_argument("--workspace", required=True, type=Path)
    resume.add_argument("--api-key", help="省略后从隐藏输入读取")
    resume.add_argument("--engagement", action="store_true")
    resume.add_argument("--allow-system-proxy", action="store_true")
    for name in ("audit", "export"):
        command = sub.add_parser(name)
        command.add_argument("--workspace", required=True, type=Path)
        if name == "export":
            command.add_argument("--format", default="all", choices=["all"])
    sub.add_parser("doctor")
    proxy = sub.add_parser("proxy")
    proxy.add_argument("action", choices=["restore"])
    proxy.add_argument("--state", type=Path)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect":
        import getpass
        key = args.api_key or getpass.getpass("导出服务 API key（不会保存）: ")
        path = Collector(_progress).collect(args.url, args.output, key,
            with_engagement=args.engagement, proxy_consent=args.allow_system_proxy)
        print(path)
    elif args.command == "resume":
        import getpass
        key = args.api_key or getpass.getpass("导出服务 API key（不会保存）: ")
        Collector(_progress).resume(args.workspace, key, with_engagement=args.engagement,
                                    proxy_consent=args.allow_system_proxy)
    elif args.command in {"audit", "export"}:
        ws = Workspace.open(args.workspace)
        store = Store(ws.database)
        value = audit_workspace(store, ws.root) if args.command == "audit" else export_all(store, ws.root)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif args.command == "doctor":
        checks = {
            "macOS": platform.system() == "Darwin",
            "networksetup": bool(shutil.which("networksetup")),
            "mitmdump": bool(shutil.which("mitmdump")),
            "mitm_ca_generated": (Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem").exists(),
            "python_3_12_plus": tuple(map(int, platform.python_version_tuple()[:2])) >= (3, 12),
        }
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return 0 if all(checks.values()) else 1
    elif args.command == "proxy":
        states = [args.state] if args.state else list(
            (Path.home() / "Documents" / "gzh-information-data").glob("*/runtime/proxy-state.json")
        )
        restored = sum(MacProxyManager(path).restore() for path in states if path)
        print(f"已恢复 {restored} 组代理设置" if restored else "没有发现待恢复的代理状态")
    elif args.command == "serve":
        import uvicorn

        from .web import open_browser
        open_browser(args.port)
        uvicorn.run("gzh_reader.web:app", host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
