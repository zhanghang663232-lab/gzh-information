from __future__ import annotations

import hashlib
import html as html_lib
import re
from html.parser import HTMLParser

from .http import HttpClient
from .models import ArticleSeed, ContentSnapshot, Status


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.assets: list[str] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if attrs_dict.get("id") == "js_content":
            self.depth = 1
        elif self.depth:
            self.depth += 1
        if self.depth and tag == "img":
            src = attrs_dict.get("data-src") or attrs_dict.get("src")
            if src:
                self.assets.append(src)
                self.parts.append(f"\n![]({src})\n")
        elif self.depth and tag in {"p", "br", "section", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth and data.strip():
            self.parts.append(data.strip())


def _value(html: str, patterns: list[str]) -> str:
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL)
        if match:
            return html_lib.unescape(match.group(1).strip())
    return ""


def classify_page(html: str, status_code: int = 200) -> tuple[Status, str]:
    if status_code == 429 or "操作频繁" in html:
        return Status.RATE_LIMITED, "平台限流"
    if "内容已被发布者删除" in html or "该内容已被发布者删除" in html:
        return Status.DELETED, "文章已删除"
    if any(text in html for text in ("违规无法查看", "内容违规", "已停止访问该网页")):
        return Status.RESTRICTED, "文章受限或违规"
    if "js_content" not in html:
        return Status.FAILED, "页面中没有正文容器"
    return Status.OK, ""


def extract_account_name(html: str) -> str:
    return _value(html, [r'id=["\']js_name["\'][^>]*>\s*(.*?)\s*</a>'])


class ContentFetcher:
    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient()

    def fetch_content(self, article: ArticleSeed) -> ContentSnapshot:
        response = self.http.request(article.url, headers={"User-Agent": "Mozilla/5.0"})
        return self.parse_content(article, response.text, response.status, "public_page", response.body)

    def parse_content(self, article: ArticleSeed, html: str, status_code: int = 200,
                      source: str = "exporter_download", raw: bytes | None = None) -> ContentSnapshot:
        status, reason = classify_page(html, status_code)
        if status != Status.OK:
            return ContentSnapshot(article_key=article.stable_key, source=source,
                                   html=html, checksum=hashlib.sha256(raw or html.encode()).hexdigest(),
                                   status=status, reason=reason)
        parser = _TextParser()
        parser.feed(html)
        title = _value(html, [
            r"var\s+msg_title\s*=\s*['\"](.*?)['\"]",
            r'class=["\']js_title_inner["\'][^>]*>(.*?)</span>',
            r"<title>(.*?)</title>",
        ])
        author = _value(html, [
            r'id=["\']js_author_name["\'][^>]*>(.*?)</span>',
            r"var\s+nickname\s*=\s*['\"](.*?)['\"]",
        ])
        published = _value(html, [r"var\s+ct\s*=\s*['\"]?(\d+)", r'id="publish_time"[^>]*>(.*?)<'])
        cover = _value(html, [r"var\s+msg_cdn_url\s*=\s*['\"](.*?)['\"]"])
        markdown = re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
        return ContentSnapshot(
            article_key=article.stable_key, title=title or article.title, author=author,
            published_at=published or article.published_at, html=html, markdown=markdown,
            cover_url=cover, assets=list(dict.fromkeys(parser.assets)), source=source,
            checksum=hashlib.sha256(raw or html.encode()).hexdigest(), status=Status.OK,
        )
