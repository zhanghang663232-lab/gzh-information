#!/usr/bin/env python3
"""Extract public article links from a saved WeChat album/page HTML file."""
from __future__ import annotations
import argparse
import html
import re
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

URL_RE = re.compile(r"""https?://mp\.weixin\.qq\.com/(?:s|mp/appmsgalbum)[^"'<>\s]+""", re.I)
SENSITIVE = {"pass_ticket", "exportkey", "wap_sid2"}

def normalize_url(raw: str) -> str:
    value = html.unescape(raw).replace("\\/", "/").replace("\\u0026", "&")
    parts = urlsplit(value)
    if not parts.netloc.endswith("mp.weixin.qq.com"):
        return ""
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key not in SENSITIVE]
    return urlunsplit(("https", parts.netloc, parts.path, urlencode(query), ""))

def extract_urls(text: str) -> list[str]:
    seen, result = set(), []
    for raw in URL_RE.findall(text):
        url = normalize_url(raw)
        if url and "/s" in url and url not in seen:
            seen.add(url)
            result.append(url)
    return result

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    urls = extract_urls(args.html.read_text(encoding="utf-8", errors="ignore"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
    print(f"unique_public_urls={len(urls)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
