from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

SENSITIVE = re.compile(
    r"(?i)(key|pass_ticket|wap_sid2|cookie|authorization)(\s*[:=]\s*)([^\s&;\"']+)"
)


def normalize_url(url: str) -> str:
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("需要 http/https 公众号文章链接")
    query = parse_qs(parts.query, keep_blank_values=True)
    keep = {}
    for key in ("__biz", "mid", "idx", "sn", "chksm"):
        if key in query:
            keep[key] = query[key][-1]
    return urlunsplit(("https", parts.netloc.lower(), parts.path, urlencode(keep), ""))


def parse_biz(url: str) -> str:
    values = parse_qs(urlsplit(url).query).get("__biz", [])
    return values[-1] if values else ""


def article_stable_key(
    url: str = "", aid: str = "", appmsgid: str = "", itemidx: str = ""
) -> str:
    if url:
        return "url:" + hashlib.sha256(normalize_url(url).encode()).hexdigest()[:24]
    if aid:
        return f"aid:{aid}"
    if appmsgid and itemidx:
        return f"msg:{appmsgid}:{itemidx}"
    raise ValueError("文章缺少 URL、aid 或 appmsgid+itemidx")


def redact(value: str) -> str:
    return SENSITIVE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", value)
