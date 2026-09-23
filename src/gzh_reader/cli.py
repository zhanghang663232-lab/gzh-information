from __future__ import annotations

import argparse
import getpass
import json
import os
import platform
import shutil
import sys
from pathlib import Path

from .audit import audit_workspace
from .exports import export_all
from .proxy import MacProxyManager
from .storage import Store
from .workspace import Workspace, safe_name


def _progress(stage: str, detail: dict) -> None:
    print(json.dumps({"stage": stage, **detail}, ensure_ascii=False))


def _deepseek_key() -> str | None:
    if os.environ.get("DEEPSEEK_API_KEY"):
        return None
    if sys.stdin.isatty():
        return getpass.getpass("请输入 DeepSeek API key（输入隐藏且不会保存）：")
    return None


def _deepseek_reviewer():
    from .deepseek import DeepSeekCardReviewer, DeepSeekClient

    return DeepSeekCardReviewer(DeepSeekClient(_deepseek_key()))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gzh-reader", description="Mac 公众号公开数据归档工具")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--url", required=True)
    collect.add_argument("--output", type=Path, default=Path.home() / "Documents" / "gzh-information-data")
    collect.add_argument("--account-name", help="已在微信中打开的目标公众号名称")
    collect.add_argument("--max-articles", type=int, help="试跑时最多保存的文章总数；不填则持续读取")
    collect.add_argument("--max-new-articles", type=int, default=5, help="每轮最多新增 1-10 篇，默认 5 篇")
    collect.add_argument("--deepseek-review", action="store_true", help="仅在文章卡片 OCR 无法解析时调用 DeepSeek；最多 5 次")
    resume = sub.add_parser("resume")
    resume.add_argument("--workspace", required=True, type=Path)
    resume.add_argument("--max-articles", type=int, help="试跑时最多保存的文章总数；不填则持续读取")
    resume.add_argument("--max-new-articles", type=int, default=5, help="每轮最多新增 1-10 篇，默认 5 篇")
    resume.add_argument("--deepseek-review", action="store_true", help="仅在文章卡片 OCR 无法解析时调用 DeepSeek；最多 5 次")
    model = sub.add_parser("model", help="检查可选模型接口")
    model.add_argument("action", choices=["test"])
    model.add_argument("--provider", choices=["deepseek"], default="deepseek")
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
        from .human_agent import MacHumanAccountCollector

        collector_options = {}
        if args.deepseek_review:
            collector_options["reviewer"] = _deepseek_reviewer()
        path = MacHumanAccountCollector(_progress, **collector_options).collect(
            args.url, args.output, max_articles=args.max_articles,
            account_name=args.account_name, max_new_articles=args.max_new_articles,
        )
        print(path)
    elif args.command == "resume":
        from .human_agent import MacHumanAccountCollector

        ws = Workspace.open(args.workspace)
        accounts = Store(ws.database).rows(
            """SELECT name, source_url FROM accounts
               WHERE source='mac_human_agent' AND status='ok' AND biz LIKE 'human:%'"""
        )
        if (
            len(accounts) != 1
            or safe_name(accounts[0]["name"]) != ws.root.name
            or not accounts[0]["source_url"]
        ):
            raise RuntimeError("工作区没有唯一且匹配的目标公众号及起始文章链接")
        collector_options = {}
        if args.deepseek_review:
            collector_options["reviewer"] = _deepseek_reviewer()
        MacHumanAccountCollector(_progress, **collector_options).collect(
            accounts[0]["source_url"], ws.root.parent,
            account_name=accounts[0]["name"],
            max_articles=args.max_articles,
            max_new_articles=args.max_new_articles,
        )
    elif args.command in {"audit", "export"}:
        ws = Workspace.open(args.workspace)
        store = Store(ws.database)
        value = audit_workspace(store, ws.root) if args.command == "audit" else export_all(store, ws.root)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif args.command == "model":
        from .deepseek import DeepSeekClient

        print(json.dumps(DeepSeekClient(_deepseek_key()).test(), ensure_ascii=False, indent=2))
    elif args.command == "doctor":
        try:
            import ApplicationServices
            import Quartz

            accessibility = bool(ApplicationServices.AXIsProcessTrusted())
            screen_capture = bool(Quartz.CGPreflightScreenCaptureAccess())
        except ImportError:
            accessibility = False
            screen_capture = False
        required = {
            "macOS": platform.system() == "Darwin",
            "WeChat": bool(shutil.which("open")),
            "accessibility_permission": accessibility,
            "screen_recording_permission": screen_capture,
            "python_3_12_plus": tuple(map(int, platform.python_version_tuple()[:2])) >= (3, 12),
        }
        free_gib = round(shutil.disk_usage(Path.home()).free / (1024 ** 3), 1)
        background = {
            "cua_driver": shutil.which("cua-driver"),
            "lume": shutil.which("lume"),
            "tart": shutil.which("tart"),
            "free_disk_gib": free_gib,
            "vm_disk_ready": free_gib >= 35,
            "recommended_mode": (
                "isolated_macos_vm" if free_gib >= 35 else "foreground_resumable"
            ),
        }
        print(json.dumps({"required": required, "background": background}, ensure_ascii=False, indent=2))
        return 0 if all(required.values()) else 1
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
