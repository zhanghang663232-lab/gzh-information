from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from .comments import CommentsResult
from .models import (
    Account,
    ArticleSeed,
    CaptureSession,
    ContentSnapshot,
    MetricSnapshot,
)


class Provider(Protocol):
    """Unified public interface for deterministic collection adapters."""

    def resolve_account(self, url: str) -> Account: ...
    def enumerate_articles(self, account: Account) -> Iterator[ArticleSeed]: ...
    def fetch_content(self, article: ArticleSeed) -> ContentSnapshot: ...
    def capture_session(self, account: Account, fresh_url: str) -> CaptureSession: ...
    def fetch_metrics(self, article: ArticleSeed, session: CaptureSession) -> MetricSnapshot: ...
    def fetch_comments(self, article: ArticleSeed, session: CaptureSession,
                       checkpoint: str = "0") -> CommentsResult: ...

