"""mitmproxy add-on; loaded only after explicit local proxy consent."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


class CredentialCapture:
    def __init__(self):
        self.biz = os.environ.get("GZH_TARGET_BIZ", "")
        self.output = Path(os.environ.get("GZH_CREDENTIAL_PATH", "credential.json"))
        log = os.environ.get("GZH_ARTICLE_URL_LOG", "")
        self.article_log = Path(log) if log else None

    @staticmethod
    def _public_url(value: str) -> str:
        value = html.unescape(value.replace("\\/", "/"))
        parts = urlsplit(value)
        query = parse_qs(parts.query)
        keep = {
            name: query[name][-1]
            for name in ("__biz", "mid", "idx", "sn", "chksm")
            if query.get(name)
        }
        return urlunsplit(("https", "mp.weixin.qq.com", "/s", urlencode(keep), ""))

    def response(self, flow) -> None:  # pragma: no cover - executed by mitmproxy
        request = flow.request
        if request.pretty_host == "mp.weixin.qq.com" and request.path.startswith("/mp/profile_ext"):
            query = parse_qs(urlsplit(request.pretty_url).query)
            biz = (query.get("__biz") or [""])[-1]
            if self.article_log and biz == self.biz:
                body = flow.response.get_text(strict=False) or ""
                urls = set()
                for match in re.findall(
                    r"https?(?::|%3A)(?:\\/|/|%2F){2}mp\.weixin\.qq\.com(?:\\/|/|%2F)s[^\"'\s,}]+",
                    body,
                ):
                    from urllib.parse import unquote
                    candidate = unquote(match)
                    public = self._public_url(candidate)
                    if (parse_qs(urlsplit(public).query).get("__biz") or [""])[-1] == self.biz:
                        urls.add(public)
                if urls:
                    self.article_log.parent.mkdir(parents=True, exist_ok=True)
                    with self.article_log.open("a", encoding="utf-8") as handle:
                        for url in sorted(urls):
                            handle.write(url + "\n")
                    os.chmod(self.article_log, 0o600)
        if request.pretty_host != "mp.weixin.qq.com" or request.path.split("?", 1)[0] != "/s":
            return
        query = parse_qs(urlsplit(request.pretty_url).query)
        biz = (query.get("__biz") or [""])[-1]
        if not self.biz or biz != self.biz:
            return
        cookie = request.headers.get("Cookie", "")
        if not cookie:
            return
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temp = self.output.with_suffix(".tmp")
        sensitive_names = ("uin", "key", "pass_ticket", "wap_sid2", "appmsg_token")
        temp.write_text(json.dumps({
            "biz": biz, "fresh_url": request.pretty_url, "cookie": cookie,
            "url_hash": hashlib.sha256(request.pretty_url.encode()).hexdigest(),
            "params": {name: query[name][-1] for name in sensitive_names if query.get(name)},
        }, ensure_ascii=False), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(self.output)


addons = [CredentialCapture()]

