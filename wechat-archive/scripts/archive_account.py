#!/usr/bin/env python3
"""Archive every publicly listed article for one WeChat Official Account.

The input is one public WeChat article or album URL containing ``__biz``.
The list API needs an exporter key, read only from the macOS pasteboard, a
prompt, or an environment variable.  It is never written to the run folder.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

API_ROOT = "https://down.mptext.top/api/public/v1"
PAGE_SIZE = 20
SENSITIVE = {"pass_ticket", "exportkey", "wap_sid2"}


def public_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or parts.netloc != "mp.weixin.qq.com":
        raise ValueError("请输入 mp.weixin.qq.com 的公开文章或专辑链接。")
    query = sorted((key, value) for key, values in parse_qs(parts.query, keep_blank_values=True).items()
                   for value in values if key not in SENSITIVE)
    return urlunsplit(("https", parts.netloc, parts.path, urlencode(query), ""))


def biz_from_url(url: str) -> str:
    biz = parse_qs(urlsplit(url).query).get("__biz", [""])[0]
    if not biz:
        raise ValueError(
            "该链接不含 __biz，无法可靠识别公众号。请在微信中打开一篇文章后复制完整链接。"
        )
    return biz


def read_key(source: str) -> str:
    if source == "env":
        value = os.environ.get("WECHAT_EXPORTER_AUTH_KEY", "")
    elif source == "pasteboard":
        try:
            value = subprocess.check_output(["pbpaste"], text=True, timeout=3).strip()
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError("无法读取 macOS 剪贴板；请改用 --auth-source prompt。") from exc
    else:
        import getpass
        value = getpass.getpass("Exporter API key（输入不回显）: ").strip()
    if not value:
        raise RuntimeError(
            "需要 exporter API key 才能分页读取完整公开历史。"
            "可先在导出服务页面复制 key，再使用 --auth-source pasteboard。"
        )
    return value


def request_json(url: str, key: str) -> dict[str, Any]:
    request = Request(url, headers={"X-Auth-Key": key, "Accept": "application/json"})
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("文章列表服务返回了非 JSON 对象。")
    code = str(payload.get("code", "0"))
    if code not in {"0", "200", "None"}:
        raise RuntimeError(f"文章列表服务返回错误 code={code}。")
    return payload


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def extract_articles(payload: dict[str, Any]) -> tuple[list[str], str]:
    urls: list[str] = []
    account_name = ""
    seen: set[str] = set()
    for item in walk(payload):
        if not account_name:
            account_name = str(item.get("_accountName") or item.get("account_name") or "").strip()
        for key in ("link", "url", "content_url"):
            raw = item.get(key)
            if not isinstance(raw, str) or "mp.weixin.qq.com/" not in raw:
                continue
            try:
                normalized = public_url(raw)
            except ValueError:
                continue
            if normalized not in seen:
                seen.add(normalized)
                urls.append(normalized)
    return urls, account_name


def fetch_all(biz: str, key: str, raw_dir: Path) -> tuple[list[str], str]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    all_urls: list[str] = []
    seen: set[str] = set()
    fingerprints: set[str] = set()
    account_name = ""
    begin = 0
    while True:
        endpoint = f"{API_ROOT}/article?" + urlencode({"fakeid": biz, "begin": begin, "size": PAGE_SIZE})
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                payload = request_json(endpoint, key)
                break
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    raise RuntimeError(f"列表第 {begin} 页连续失败。") from exc
                time.sleep(attempt + 1)
        else:
            raise RuntimeError("无法获取文章列表。") from last_error
        (raw_dir / f"page-{begin:06d}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        urls, discovered_name = extract_articles(payload)
        if discovered_name and not account_name:
            account_name = discovered_name
        fingerprint = json.dumps(urls, ensure_ascii=False, separators=(",", ":"))
        if not urls:
            break
        if fingerprint in fingerprints:
            raise RuntimeError("检测到重复列表页，已停止以避免无限循环。")
        fingerprints.add(fingerprint)
        for url in urls:
            if url not in seen:
                seen.add(url)
                all_urls.append(url)
        begin += PAGE_SIZE
    return all_urls, account_name


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="从一个公开链接归档整个公众号的可访问公开文章。")
    parser.add_argument("--account-url", required=True, help="任一该账号公开文章/专辑链接")
    parser.add_argument("--auth-source", choices=("pasteboard", "prompt", "env"), default="pasteboard")
    parser.add_argument("--output", type=Path, help="归档目录；默认 run/account-<biz>")
    parser.add_argument("--account-name", help="可选：覆盖 API 返回的账号名称")
    parser.add_argument("--skip-content", action="store_true", help="仅建立全量链接清单，不下载文章正文")
    args = parser.parse_args()
    try:
        seed = public_url(args.account_url)
        biz = biz_from_url(seed)
        key = read_key(args.auth_source)
        root = args.output or Path("run") / f"account-{biz}"
        urls, api_name = fetch_all(biz, key, root / "raw" / "list-pages")
        name = args.account_name or api_name or f"biz-{biz}"
        urls_path = root / "article-urls.txt"
        urls_path.parent.mkdir(parents=True, exist_ok=True)
        urls_path.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
        manifest = {"seed_url": seed, "biz": biz, "account_name": name, "unique_public_urls": len(urls),
                    "list_complete": True, "page_size": PAGE_SIZE}
        (root / "list-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not args.skip_content:
            script_dir = Path(__file__).parent
            run([sys.executable, str(script_dir / "collect_articles.py"), "--input", str(urls_path), "--output", str(root / "articles")])
            run([sys.executable, str(script_dir / "build_obsidian.py"), "--articles", str(root / "articles"), "--output", str(root / "obsidian"), "--account-name", name])
            run([sys.executable, str(script_dir / "audit.py"), "--urls", str(urls_path), "--articles", str(root / "articles"), "--output", str(root / "audit.json")])
        print(json.dumps({"account_name": name, "unique_public_urls": len(urls), "output": str(root)}, ensure_ascii=False))
        return 0
    finally:
        key = ""  # Avoid retaining the exporter key longer than this process needs.


if __name__ == "__main__":
    raise SystemExit(main())
