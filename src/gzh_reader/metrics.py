from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .http import HttpClient
from .models import ArticleSeed, CaptureSession, MetricSnapshot, Status

FIELDS = ("readNum", "likeNum", "oldLikeNum", "shareNum", "commentNum")


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def parse_metrics(text: str) -> dict[str, int | None]:
    """Parse exact WeChat field names, preserving zero and the 100001 cap."""
    result: dict[str, int | None] = {field: None for field in FIELDS}
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(text))
    except json.JSONDecodeError:
        for pattern in (r"cgiDataNew\s*=\s*(\{.*?\});", r"window\.cgiDataNew\s*=\s*(\{.*?\});"):
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    candidates.append(json.loads(match.group(1)))
                except json.JSONDecodeError:
                    pass
    for candidate in candidates:
        for obj in _walk(candidate):
            for field in FIELDS:
                if result[field] is None and field in obj:
                    value = obj[field]
                    if isinstance(value, bool):
                        continue
                    try:
                        result[field] = int(value)
                    except (TypeError, ValueError):
                        pass
    if all(value is None for value in result.values()):
        for field in FIELDS:
            match = re.search(rf'["\']{field}["\']\s*:\s*["\']?(\d+)', text)
            if match:
                result[field] = int(match.group(1))
    return result


class MetricsFetcher:
    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient()

    def fetch_metrics(self, article: ArticleSeed, session: CaptureSession) -> MetricSnapshot:
        if session.biz != article.biz:
            return MetricSnapshot(article_key=article.stable_key, status=Status.FAILED,
                                  reason="拒绝跨公众号复用凭据")
        parts = urlsplit(article.url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query.update(session.params)
        credentialed_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        response = self.http.request(credentialed_url, headers={
            "Cookie": session.cookie,
            "User-Agent": "Mozilla/5.0 MicroMessenger",
        }, retries=1)
        if response.status == 429 or "操作频繁" in response.text:
            return MetricSnapshot(article_key=article.stable_key, status=Status.RATE_LIMITED,
                                  reason="指标接口限流")
        if response.status in (401, 403) or "登录超时" in response.text:
            return MetricSnapshot(article_key=article.stable_key, status=Status.CREDENTIAL_EXPIRED,
                                  reason="微信 Credential 已失效")
        values = parse_metrics(response.text)
        if all(value is None for value in values.values()):
            return MetricSnapshot(article_key=article.stable_key, status=Status.MISSING,
                                  reason="页面未返回五项指标")
        missing = [field for field, value in values.items() if value is None]
        return MetricSnapshot(
            article_key=article.stable_key, **values,
            checksum=hashlib.sha256(response.body).hexdigest(),
            status=Status.OK if not missing else Status.MISSING,
            reason=("缺少字段: " + ", ".join(missing)) if missing else "",
        )

