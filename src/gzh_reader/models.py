from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Status(StrEnum):
    PENDING = "pending"
    OK = "ok"
    DELETED = "deleted"
    RESTRICTED = "restricted"
    RATE_LIMITED = "rate_limited"
    CREDENTIAL_EXPIRED = "credential_expired"
    MISSING = "missing"
    FAILED = "failed"


@dataclass(slots=True)
class Account:
    biz: str
    name: str = "未知公众号"
    fakeid: str = ""
    source_url: str = ""
    source: str = "input"
    captured_at: str = field(default_factory=utc_now)
    status: Status = Status.PENDING


@dataclass(slots=True)
class ArticleSeed:
    stable_key: str
    url: str
    biz: str
    title: str = ""
    aid: str = ""
    appmsgid: str = ""
    itemidx: str = ""
    published_at: str = ""
    source: str = ""
    captured_at: str = field(default_factory=utc_now)
    status: Status = Status.PENDING


@dataclass(slots=True)
class ContentSnapshot:
    article_key: str
    title: str = ""
    author: str = ""
    published_at: str = ""
    html: str = ""
    markdown: str = ""
    cover_url: str = ""
    assets: list[str] = field(default_factory=list)
    source: str = ""
    checksum: str = ""
    captured_at: str = field(default_factory=utc_now)
    status: Status = Status.PENDING
    reason: str = ""


@dataclass(slots=True)
class MetricSnapshot:
    article_key: str
    readNum: int | None = None
    likeNum: int | None = None
    oldLikeNum: int | None = None
    shareNum: int | None = None
    commentNum: int | None = None
    source: str = "wechat_session"
    checksum: str = ""
    captured_at: str = field(default_factory=utc_now)
    status: Status = Status.PENDING
    reason: str = ""


@dataclass(slots=True)
class CommentReply:
    reply_id: str
    content: str
    nickname: str = ""
    like_num: int = 0
    created_at: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CommentRecord:
    comment_id: str
    article_key: str
    content: str
    nickname: str = ""
    like_num: int = 0
    created_at: int | None = None
    assets: list[str] = field(default_factory=list)
    replies: list[CommentReply] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CaptureSession:
    biz: str
    fresh_url: str
    cookie: str
    params: dict[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)
    expires_at: str = ""
    source: str = "mitmproxy"

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["cookie"] = "[REDACTED]"
        value["params"] = {key: "[REDACTED]" for key in self.params}
        value["fresh_url"] = "[REDACTED]"
        return value
