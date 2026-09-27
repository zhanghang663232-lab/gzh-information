from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlencode

from .http import HttpClient
from .models import ArticleSeed, CaptureSession, CommentRecord, CommentReply, Status


def extract_comment_context(html: str) -> dict[str, str]:
    names = ("comment_id", "appmsgid", "idx", "appmsg_token", "__biz")
    context: dict[str, str] = {}
    for name in names:
        patterns = [
            rf"(?:var\s+)?{re.escape(name)}\s*=\s*['\"]([^'\"]+)",
            rf"[?&]{re.escape(name)}=([^&'\"\s]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                context[name] = match.group(1)
                break
    return context


def _asset_urls(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"https?://[^\s'\"<>]+", text or "")))


def parse_comment_page(payload: dict, article_key: str) -> tuple[list[CommentRecord], str, bool]:
    data = payload.get("elected_comment") or payload.get("data") or payload
    items = data.get("comment") or data.get("comments") or data.get("list") or []
    comments: list[CommentRecord] = []
    for raw in items:
        cid = str(raw.get("content_id") or raw.get("comment_id") or raw.get("id") or "")
        if not cid:
            continue
        content = str(raw.get("content") or raw.get("text") or "")
        replies_raw = raw.get("reply") or raw.get("reply_list") or []
        replies = [
            CommentReply(
                reply_id=str(item.get("reply_id") or item.get("id") or f"{cid}:{index}"),
                content=str(item.get("content") or item.get("text") or ""),
                nickname=str(item.get("nick_name") or item.get("nickname") or ""),
                like_num=int(item.get("like_num") or 0),
                created_at=item.get("create_time"), raw=item,
            )
            for index, item in enumerate(replies_raw)
        ]
        comments.append(CommentRecord(
            comment_id=cid, article_key=article_key, content=content,
            nickname=str(raw.get("nick_name") or raw.get("nickname") or ""),
            like_num=int(raw.get("like_num") or 0), created_at=raw.get("create_time"),
            assets=_asset_urls(content + " " + json.dumps(raw.get("img_list") or [], ensure_ascii=False)),
            replies=replies, raw=raw,
        ))
    next_cursor = str(data.get("next_offset") or data.get("offset") or "")
    completed = bool(data.get("is_end") or not items or not next_cursor)
    return comments, next_cursor, completed


@dataclass(slots=True)
class CommentsResult:
    comments: list[CommentRecord]
    status: Status
    reason: str = ""
    raw_pages: list[dict] = field(default_factory=list)


class CommentsFetcher:
    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient()

    def fetch_comments(self, article: ArticleSeed, session: CaptureSession, html: str,
                       start_cursor: str = "0", max_pages: int = 100) -> CommentsResult:
        if session.biz != article.biz:
            return CommentsResult([], Status.FAILED, "拒绝跨公众号复用凭据")
        context = extract_comment_context(html)
        if not context.get("comment_id"):
            return CommentsResult([], Status.OK, "文章未开放公开评论")
        cursor = start_cursor
        output: list[CommentRecord] = []
        raw_pages: list[dict] = []
        fingerprints: set[str] = set()
        for _ in range(max_pages):
            params = {
                "action": "getcomment", "scene": "0", "offset": cursor,
                "limit": "100", "comment_id": context["comment_id"],
                "appmsgid": context.get("appmsgid", article.appmsgid),
                "idx": context.get("idx", article.itemidx),
                "__biz": context.get("__biz", article.biz),
                "appmsg_token": context.get("appmsg_token", ""),
            }
            params.update(session.params)
            url = "https://mp.weixin.qq.com/mp/appmsg_comment?" + urlencode(params)
            response = self.http.request(url, headers={
                "Cookie": session.cookie, "User-Agent": "Mozilla/5.0 MicroMessenger",
                "Referer": article.url,
            }, retries=1)
            if response.status == 429 or "操作频繁" in response.text:
                return CommentsResult(output, Status.RATE_LIMITED, "评论接口限流", raw_pages)
            if response.status in (401, 403) or "登录超时" in response.text:
                return CommentsResult(output, Status.CREDENTIAL_EXPIRED, "微信 Credential 已失效", raw_pages)
            try:
                payload = response.json()
            except json.JSONDecodeError:
                return CommentsResult(output, Status.FAILED, "评论接口返回非 JSON", raw_pages)
            raw_pages.append(payload)
            fingerprint = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            if fingerprint in fingerprints:
                return CommentsResult(output, Status.FAILED, "评论分页重复，未宣称完整", raw_pages)
            fingerprints.add(fingerprint)
            page, cursor, completed = parse_comment_page(payload, article.stable_key)
            output.extend(page)
            if completed:
                return CommentsResult(output, Status.OK, raw_pages=raw_pages)
        return CommentsResult(output, Status.FAILED, "评论分页超过安全上限", raw_pages)

