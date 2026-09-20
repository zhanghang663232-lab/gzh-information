from __future__ import annotations

import hashlib
import json
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .audit import audit_workspace
from .capture import capture_session
from .comments import CommentsFetcher
from .content import ContentFetcher
from .exporter import ExporterListProvider
from .exports import export_all
from .metrics import MetricsFetcher
from .models import ContentSnapshot, MetricSnapshot, Status, utc_now
from .storage import Store
from .workspace import Workspace

Progress = Callable[[str, dict], None]


def _silent(stage: str, detail: dict) -> None:
    return None


class Collector:
    def __init__(self, progress: Progress = _silent):
        self.progress = progress

    def collect(self, url: str, output: Path, api_key: str, *, with_engagement: bool = False,
                proxy_consent: bool = False) -> Path:
        if not api_key:
            raise ValueError("首次枚举完整历史文章需要导出服务 API key")
        with tempfile.TemporaryDirectory(prefix="gzh-resolve-") as temp:
            temp_root = Path(temp)
            provider = ExporterListProvider(
                api_key, Store(temp_root / "resolve.sqlite3"), temp_root / "raw"
            )
            account = provider.resolve_account(url)
        bootstrap = Workspace.create(output, account.name)
        store = Store(bootstrap.database)
        provider.store = store
        provider.raw_dir = bootstrap.root / "raw" / "lists"
        store.upsert_account(account)
        run_id = str(uuid.uuid4())
        with store.connect() as db:
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                       (run_id, account.biz, utc_now(), "", "list", Status.PENDING.value, "{}"))
        try:
            self.progress("list", {"message": "正在枚举历史文章"})
            for article in provider.enumerate_articles(account):
                store.upsert_article(article)
            self._content(store, bootstrap, provider)
            if with_engagement:
                self._engagement(store, bootstrap, account.biz, proxy_consent)
            self.progress("export", {"message": "正在生成数据包"})
            export_all(store, bootstrap.root)
            report = audit_workspace(store, bootstrap.root)
            status = Status.OK if not report["security_findings"] else Status.FAILED
            with store.connect() as db:
                db.execute("UPDATE runs SET ended_at=?,stage=?,status=?,detail_json=? WHERE run_id=?",
                           (utc_now(), "complete", status.value, json.dumps(report, ensure_ascii=False), run_id))
            self.progress("complete", {"workspace": str(bootstrap.root), "report": report})
            return bootstrap.root
        except Exception as exc:
            with store.connect() as db:
                db.execute("UPDATE runs SET ended_at=?,stage=?,status=?,detail_json=? WHERE run_id=?",
                           (utc_now(), "failed", Status.FAILED.value,
                            json.dumps({"error": type(exc).__name__}, ensure_ascii=False), run_id))
            raise
        finally:
            bootstrap.scrub_credential()

    def resume(self, workspace: Path, api_key: str = "", *, with_engagement: bool = False,
               proxy_consent: bool = False) -> Path:
        ws = Workspace.open(workspace)
        store = Store(ws.database)
        accounts = store.rows("SELECT * FROM accounts LIMIT 1")
        provider = None
        if api_key and accounts:
            from .models import Account
            account = Account(**{key: accounts[0][key] for key in Account.__dataclass_fields__})
            provider = ExporterListProvider(api_key, store, ws.root / "raw" / "lists")
            for article in provider.enumerate_articles(account):
                store.upsert_article(article)
        self._content(store, ws, provider)
        if with_engagement and accounts:
            self._engagement(store, ws, accounts[0]["biz"], proxy_consent)
        export_all(store, ws.root)
        audit_workspace(store, ws.root)
        return ws.root

    def _content(self, store: Store, ws: Workspace,
                 provider: ExporterListProvider | None = None) -> None:
        rows = store.rows("""SELECT a.* FROM articles a LEFT JOIN content_snapshots c
                            ON c.article_key=a.stable_key AND c.status='ok'
                            WHERE c.id IS NULL ORDER BY a.published_at DESC""")
        fetcher = ContentFetcher()
        for index, row in enumerate(rows, 1):
            self.progress("content", {"current": index, "total": len(rows), "title": row["title"]})
            from .models import ArticleSeed
            article = ArticleSeed(**{key: row[key] for key in ArticleSeed.__dataclass_fields__})
            snapshot = None
            if provider is not None:
                try:
                    raw = provider.download_article(article.url, "html")
                    snapshot = fetcher.parse_content(
                        article, raw.decode("utf-8", errors="replace"),
                        source="exporter_download", raw=raw,
                    )
                except (RuntimeError, OSError, UnicodeError, ValueError):
                    snapshot = None
            if snapshot is None or snapshot.status == Status.FAILED:
                try:
                    snapshot = fetcher.fetch_content(article)
                except (RuntimeError, OSError, UnicodeError, ValueError) as exc:
                    snapshot = ContentSnapshot(
                        article_key=article.stable_key, source="public_page",
                        status=Status.FAILED, reason=f"{type(exc).__name__}: 正文请求失败",
                    )
            raw_name = article.stable_key.replace(":", "_")
            html_path = f"raw/articles/{raw_name}.html"
            md_path = f"raw/markdown/{raw_name}.md"
            (ws.root / html_path).parent.mkdir(parents=True, exist_ok=True)
            (ws.root / md_path).parent.mkdir(parents=True, exist_ok=True)
            (ws.root / html_path).write_text(snapshot.html, encoding="utf-8")
            (ws.root / md_path).write_text(snapshot.markdown, encoding="utf-8")
            store.save_content(snapshot, html_path, md_path)
            time.sleep(0.25)

    def _engagement(self, store: Store, ws: Workspace, biz: str, consent: bool) -> None:
        self.progress("capture", {"message": "请在 Mac 微信打开一篇尚未使用过的目标文章"})
        rows = store.rows("SELECT * FROM articles ORDER BY published_at DESC")
        if not rows:
            return
        from .models import ArticleSeed
        metrics = MetricsFetcher()
        comments = CommentsFetcher()
        with capture_session(ws, biz, consent=consent) as session:
            with store.connect() as db:
                db.execute(
                    """INSERT INTO capture_receipts
                    (biz,captured_at,expires_at,source,fresh_url_hash,status,detail_json)
                    VALUES(?,?,?,?,?,?,?)""",
                    (biz, session.created_at, session.expires_at, session.source,
                     hashlib.sha256(session.fresh_url.encode()).hexdigest(), Status.OK.value,
                     json.dumps({"credential_fields": sorted(session.params)}, ensure_ascii=False)),
                )
            deadline = time.monotonic() + 19 * 60
            consecutive_failures = 0
            for index, row in enumerate(rows[:300], 1):
                if time.monotonic() > deadline:
                    break
                article = ArticleSeed(**{key: row[key] for key in ArticleSeed.__dataclass_fields__})
                self.progress("metrics", {"current": index, "total": len(rows), "title": row["title"]})
                try:
                    metric = metrics.fetch_metrics(article, session)
                except (RuntimeError, OSError, UnicodeError, ValueError) as exc:
                    metric = MetricSnapshot(
                        article_key=article.stable_key, status=Status.FAILED,
                        reason=f"{type(exc).__name__}: 指标请求失败",
                    )
                store.save_metrics(metric)
                if metric.status in {Status.RATE_LIMITED, Status.CREDENTIAL_EXPIRED}:
                    break
                consecutive_failures = consecutive_failures + 1 if metric.status == Status.FAILED else 0
                if consecutive_failures >= 3:
                    break
                content_rows = store.rows(
                    "SELECT html_path FROM content_snapshots WHERE article_key=? ORDER BY id DESC LIMIT 1",
                    (article.stable_key,),
                )
                html = ""
                if content_rows:
                    path = ws.root / content_rows[0]["html_path"]
                    if path.exists():
                        html = path.read_text(encoding="utf-8")
                if not html:
                    store.save_comments(article.stable_key, [], Status.MISSING, "正文不可用，无法提取评论参数")
                    continue
                try:
                    result = comments.fetch_comments(article, session, html)
                    store.save_comments(article.stable_key, result.comments, result.status, result.reason)
                    ws.write_json(
                        f"raw/comments/{article.stable_key.replace(':', '_')}.json",
                        result.raw_pages,
                    )
                except (RuntimeError, OSError, UnicodeError, ValueError) as exc:
                    store.save_comments(
                        article.stable_key, [], Status.FAILED,
                        f"{type(exc).__name__}: 评论请求失败",
                    )
                    continue
                if result.status in {Status.RATE_LIMITED, Status.CREDENTIAL_EXPIRED}:
                    break
                time.sleep(0.6)
