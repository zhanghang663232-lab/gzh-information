from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote, urlencode

from .http import HttpClient
from .models import Account, ArticleSeed, Status
from .storage import Store
from .urls import article_stable_key, normalize_url, parse_biz


class ExporterListProvider:
    """mptext-compatible list provider. The API key exists only in this object."""

    def __init__(self, api_key: str, store: Store, raw_dir: Path, http: HttpClient | None = None,
                 base_url: str = "https://down.mptext.top"):
        self._key = api_key.strip()
        self.store = store
        self.raw_dir = raw_dir
        self.http = http or HttpClient()
        self.base_url = base_url.rstrip("/")

    def _json(self, endpoint: str, params: dict[str, str]) -> dict:
        url = f"{self.base_url}{endpoint}?{urlencode(params)}"
        response = self.http.request(url, headers={"X-Auth-Key": self._key})
        if response.status in (401, 403):
            raise PermissionError("导出服务 API key 无效或已过期")
        if response.status == 429:
            raise RuntimeError("导出服务限流")
        if response.status >= 400:
            raise RuntimeError(f"导出服务 HTTP {response.status}")
        payload = response.json()
        if payload.get("code") == -1:
            raise PermissionError("导出服务 API key 无效或已过期")
        return payload

    def resolve_account(self, url: str) -> Account:
        normalized = normalize_url(url)
        biz = parse_biz(normalized)
        payload = self._json("/api/public/v1/accountbyurl", {"url": url})
        data = payload.get("data", payload)
        if isinstance(data, list):
            data = data[0] if data else {}
        data = data.get("account", data) if isinstance(data, dict) else {}
        biz = str(data.get("biz") or data.get("__biz") or biz)
        fakeid = str(data.get("fakeid") or data.get("fake_id") or "")
        if not biz or not fakeid:
            raise ValueError("导出服务未返回 biz/fakeid；目标公众号可能关闭了搜索")
        normalized = str(data.get("url") or normalized)
        return Account(biz=biz, fakeid=fakeid,
                       name=str(data.get("name") or data.get("nickname") or biz),
                       source_url=normalized, source="exporter", status=Status.OK)

    def enumerate_articles(self, account: Account) -> Iterator[ArticleSeed]:
        checkpoint = self.store.get_checkpoint("exporter", account.biz)
        cursor = "0" if checkpoint.get("completed") else str(checkpoint.get("cursor") or "0")
        previous = "" if checkpoint.get("completed") else str(checkpoint.get("page_fingerprint") or "")
        page_no = 0
        while True:
            payload = self._json("/api/public/v1/article", {
                "fakeid": account.fakeid, "begin": cursor, "size": "20",
            })
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
            fingerprint = hashlib.sha256(encoded).hexdigest()
            page_no += 1
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            (self.raw_dir / f"list-{page_no:05d}-{fingerprint[:10]}.json").write_bytes(encoded)
            if fingerprint == previous:
                self.store.set_checkpoint("exporter", account.biz, cursor, fingerprint, False)
                raise RuntimeError("导出服务返回重复分页，列表完整性无法证明")
            data = payload.get("data", payload)
            items = data.get("list") or data.get("articles") or data.get("items") or []
            for raw in items:
                url = str(raw.get("url") or raw.get("link") or "")
                aid = str(raw.get("aid") or raw.get("id") or "")
                appmsgid = str(raw.get("appmsgid") or raw.get("mid") or "")
                itemidx = str(raw.get("itemidx") or raw.get("idx") or "")
                if not url and appmsgid and itemidx:
                    url = f"https://mp.weixin.qq.com/s?__biz={quote(account.biz)}&mid={appmsgid}&idx={itemidx}"
                if not url:
                    continue
                yield ArticleSeed(
                    stable_key=article_stable_key(url=url), url=normalize_url(url), biz=account.biz,
                    title=str(raw.get("title") or ""), aid=aid, appmsgid=appmsgid,
                    itemidx=itemidx, published_at=str(raw.get("publish_time") or raw.get("date") or ""),
                    source="exporter", status=Status.PENDING,
                )
            current = int(cursor or 0)
            explicit_next = data.get("next") or data.get("next_begin")
            next_cursor = str(explicit_next if explicit_next is not None else current + len(items))
            total = data.get("total") or data.get("total_count")
            completed = bool(
                data.get("is_end") or data.get("completed") or not items or len(items) < 20
                or (total is not None and current + len(items) >= int(total))
            )
            self.store.set_checkpoint("exporter", account.biz, next_cursor, fingerprint, completed)
            if completed:
                return
            previous, cursor = fingerprint, next_cursor

    def download_article(self, url: str, format: str = "html") -> bytes:
        endpoint = f"{self.base_url}/api/public/v1/download?{urlencode({'url': url, 'format': format})}"
        response = self.http.request(endpoint, headers={"X-Auth-Key": self._key})
        if response.status >= 400:
            raise RuntimeError(f"导出服务下载端点 HTTP {response.status}")
        return response.body
