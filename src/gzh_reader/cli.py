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
from .workspace import DEFAULT_OUTPUT_ROOT, Workspace, safe_name


def _wechat_installed() -> bool:
    """Ask Launch Services for WeChat, rather than checking macOS's `open` command."""
    if platform.system() != "Darwin":
        return False
    try:
        import AppKit

        return bool(
            AppKit.NSWorkspace.sharedWorkspace()
            .URLForApplicationWithBundleIdentifier_("com.tencent.xinWeChat")
        )
    except (ImportError, AttributeError):
        return False


def _progress(stage: str, detail: dict) -> None:
    print(json.dumps({"stage": stage, **detail}, ensure_ascii=False), flush=True)


def _model_key(provider: str) -> str | None:
    env_name = "ARK_API_KEY" if provider == "doubao" else "DEEPSEEK_API_KEY"
    if os.environ.get(env_name):
        return None
    if sys.stdin.isatty():
        return getpass.getpass(f"请输入 {provider} API key（输入隐藏且不会保存）：")
    return None


def _model_reviewer(provider: str, model_id: str | None, api_key: str | None):
    from .model_client import create_card_reviewer

    return create_card_reviewer(provider, api_key, model_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gzh-reader", description="Mac 公众号公开数据归档工具")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--url", required=True)
    collect.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    collect.add_argument("--account-name", help="已在微信中打开的目标公众号名称")
    collect.add_argument("--max-articles", type=int, help="试跑时最多保存的文章总数；不填则持续读取")
    collect.add_argument("--max-new-articles", type=int, help="可选试跑上限；不填则持续读取目标账号")
    collect.add_argument("--deepseek-review", action="store_true", help="仅在文章卡片 OCR 无法解析时调用 DeepSeek；最多 5 次")
    collect.add_argument("--model-review", action="store_true", help="OCR 无法解析时调用所选模型复核；最多 5 次")
    collect.add_argument("--agent", action="store_true", help="由模型规划当前页面的文章读取顺序")
    collect.add_argument("--review-provider", choices=["deepseek", "doubao"], default="deepseek")
    collect.add_argument("--model-id", help="可选模型 ID；不填使用该供应商默认值")
    recover = sub.add_parser("recover-open", help="仅恢复当前已打开且可核实的一篇文章")
    recover.add_argument("--title", required=True)
    recover.add_argument("--account-name", required=True)
    recover.add_argument("--url", required=True, help="目标公众号已有的起始文章链接")
    recover.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    resume = sub.add_parser("resume")
    resume.add_argument("--workspace", required=True, type=Path)
    resume.add_argument("--max-articles", type=int, help="试跑时最多保存的文章总数；不填则持续读取")
    resume.add_argument("--max-new-articles", type=int, help="可选试跑上限；不填则持续读取目标账号")
    resume.add_argument("--deepseek-review", action="store_true", help="仅在文章卡片 OCR 无法解析时调用 DeepSeek；最多 5 次")
    resume.add_argument("--model-review", action="store_true", help="OCR 无法解析时调用所选模型复核；最多 5 次")
    resume.add_argument("--agent", action="store_true", help="由模型规划当前页面的文章读取顺序")
    resume.add_argument("--review-provider", choices=["deepseek", "doubao"], default="deepseek")
    resume.add_argument("--model-id", help="可选模型 ID；不填使用该供应商默认值")
    model = sub.add_parser("model", help="检查可选模型接口")
    model.add_argument("action", choices=["test"])
    model.add_argument("--provider", choices=["deepseek", "doubao"], default="deepseek")
    model.add_argument("--model-id")
    for name in ("audit", "export"):
        command = sub.add_parser(name)
        command.add_argument("--workspace", required=True, type=Path)
        if name == "export":
            command.add_argument("--format", default="all", choices=["all"])
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--visual", action="store_true", help="短暂显示测试窗口，验证实际截图和文字识别")
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
        api_key = _model_key(args.review_provider) if (
            args.deepseek_review or args.model_review or args.agent
        ) else None
        if args.deepseek_review or args.model_review:
            collector_options["reviewer"] = _model_reviewer(
                args.review_provider, args.model_id, api_key
            )
        if args.agent:
            from .agent_runtime import CardPlanAgent
            from .model_client import create_client

            collector_options["agent"] = CardPlanAgent(
                create_client(args.review_provider, api_key, args.model_id)
            )
        path = MacHumanAccountCollector(_progress, **collector_options).collect(
            args.url, args.output, max_articles=args.max_articles,
            account_name=args.account_name, max_new_articles=args.max_new_articles,
        )
        print(path)
    elif args.command == "recover-open":
        from .human_agent import MacHumanAccountCollector

        path, saved = MacHumanAccountCollector(_progress).recover_open_article(
            title=args.title, account_name=args.account_name,
            seed_url=args.url, output=args.output,
        )
        _progress("recovered_open_article" if saved else "body_missing", {"workspace": str(path)})
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
        api_key = _model_key(args.review_provider) if (
            args.deepseek_review or args.model_review or args.agent
        ) else None
        if args.deepseek_review or args.model_review:
            collector_options["reviewer"] = _model_reviewer(
                args.review_provider, args.model_id, api_key
            )
        if args.agent:
            from .agent_runtime import CardPlanAgent
            from .model_client import create_client

            collector_options["agent"] = CardPlanAgent(
                create_client(args.review_provider, api_key, args.model_id)
            )
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
        from .model_client import create_client

        try:
            result = create_client(
                args.provider, _model_key(args.provider), args.model_id
            ).test()
        except (RuntimeError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
            "WeChat": _wechat_installed(),
            "accessibility_permission": accessibility,
            "screen_recording_permission": screen_capture,
            "python_3_12_plus": tuple(map(int, platform.python_version_tuple()[:2])) >= (3, 12),
        }
        visual = None
        if args.visual and required["macOS"] and required["screen_recording_permission"]:
            from .visual_probe import probe_all

            visual = probe_all()
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
        print(json.dumps({"required": required, "visual": visual, "background": background}, ensure_ascii=False, indent=2))
        return 0 if all(required.values()) and (visual is None or (
            visual["calibration"]["capture_readable"]
            and (visual["wechat"] or {}).get("capture_readable")
        )) else 1
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
