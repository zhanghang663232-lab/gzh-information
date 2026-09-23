from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .models import (
    Account,
    ArticleSeed,
    CommentRecord,
    ContentSnapshot,
    MetricSnapshot,
    Status,
    utc_now,
)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS accounts (
  biz TEXT PRIMARY KEY, name TEXT NOT NULL, fakeid TEXT, source_url TEXT, source TEXT,
  captured_at TEXT, status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
  stable_key TEXT PRIMARY KEY, biz TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
  title TEXT, aid TEXT, appmsgid TEXT, itemidx TEXT, published_at TEXT,
  source TEXT, captured_at TEXT, status TEXT NOT NULL,
  FOREIGN KEY(biz) REFERENCES accounts(biz)
);
CREATE TABLE IF NOT EXISTS content_snapshots (
  id INTEGER PRIMARY KEY, article_key TEXT NOT NULL, title TEXT, author TEXT,
  published_at TEXT, html_path TEXT, markdown_path TEXT, cover_url TEXT,
  assets_json TEXT, source TEXT, checksum TEXT, captured_at TEXT,
  status TEXT NOT NULL, reason TEXT,
  UNIQUE(article_key, checksum), FOREIGN KEY(article_key) REFERENCES articles(stable_key)
);
CREATE TABLE IF NOT EXISTS metric_snapshots (
  id INTEGER PRIMARY KEY, article_key TEXT NOT NULL, readNum INTEGER,
  likeNum INTEGER, oldLikeNum INTEGER, shareNum INTEGER, commentNum INTEGER,
  source TEXT, checksum TEXT, captured_at TEXT, status TEXT NOT NULL, reason TEXT,
  UNIQUE(article_key, captured_at), FOREIGN KEY(article_key) REFERENCES articles(stable_key)
);
CREATE TABLE IF NOT EXISTS comments (
  comment_id TEXT, article_key TEXT, content TEXT, nickname TEXT, like_num INTEGER,
  created_at INTEGER, assets_json TEXT, raw_json TEXT, captured_at TEXT,
  PRIMARY KEY(comment_id, article_key), FOREIGN KEY(article_key) REFERENCES articles(stable_key)
);
CREATE TABLE IF NOT EXISTS comment_replies (
  reply_id TEXT, comment_id TEXT, article_key TEXT, content TEXT, nickname TEXT,
  like_num INTEGER, created_at INTEGER, raw_json TEXT, captured_at TEXT,
  PRIMARY KEY(reply_id, comment_id, article_key)
);
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY, article_key TEXT, comment_id TEXT, url TEXT NOT NULL,
  kind TEXT NOT NULL, local_path TEXT, checksum TEXT, status TEXT NOT NULL,
  UNIQUE(article_key, comment_id, url)
);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, biz TEXT, started_at TEXT, ended_at TEXT, stage TEXT,
  status TEXT, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS capture_receipts (
  id INTEGER PRIMARY KEY, biz TEXT, captured_at TEXT, expires_at TEXT,
  source TEXT, fresh_url_hash TEXT, status TEXT, detail_json TEXT
);
CREATE TABLE IF NOT EXISTS missing_records (
  id INTEGER PRIMARY KEY, article_key TEXT, layer TEXT, status TEXT, reason TEXT,
  captured_at TEXT, UNIQUE(article_key, layer)
);
CREATE TABLE IF NOT EXISTS checkpoints (
  provider TEXT, biz TEXT, cursor TEXT, page_fingerprint TEXT, completed INTEGER,
  updated_at TEXT, PRIMARY KEY(provider, biz)
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def upsert_account(self, account: Account) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO accounts(biz,name,fakeid,source_url,source,captured_at,status)
                VALUES(:biz,:name,:fakeid,:source_url,:source,:captured_at,:status)
                ON CONFLICT(biz) DO UPDATE SET name=excluded.name, fakeid=excluded.fakeid,
                source_url=excluded.source_url,
                source=excluded.source, captured_at=excluded.captured_at, status=excluded.status""",
                {**asdict(account), "status": account.status.value},
            )

    def upsert_article(self, article: ArticleSeed) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO articles VALUES(:stable_key,:biz,:url,:title,:aid,:appmsgid,
                :itemidx,:published_at,:source,:captured_at,:status)
                ON CONFLICT(stable_key) DO UPDATE SET url=excluded.url,
                title=COALESCE(NULLIF(excluded.title,''),articles.title),
                published_at=COALESCE(NULLIF(excluded.published_at,''),articles.published_at),
                source=excluded.source, captured_at=excluded.captured_at""",
                {**asdict(article), "status": article.status.value},
            )

    def save_content(self, item: ContentSnapshot, html_path: str = "", markdown_path: str = "") -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO content_snapshots
                (article_key,title,author,published_at,html_path,markdown_path,cover_url,
                 assets_json,source,checksum,captured_at,status,reason)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item.article_key, item.title, item.author, item.published_at, html_path,
                 markdown_path, item.cover_url, json.dumps(item.assets, ensure_ascii=False),
                 item.source, item.checksum, item.captured_at, item.status.value, item.reason),
            )
            db.execute("UPDATE articles SET status=? WHERE stable_key=?", (item.status.value, item.article_key))
            for asset in item.assets:
                db.execute(
                    "INSERT OR IGNORE INTO assets(article_key,comment_id,url,kind,status) VALUES(?,?,?,?,?)",
                    (item.article_key, "", asset, "article", Status.PENDING.value),
                )
            if item.cover_url:
                db.execute(
                    "INSERT OR IGNORE INTO assets(article_key,comment_id,url,kind,status) VALUES(?,?,?,?,?)",
                    (item.article_key, "", item.cover_url, "cover", Status.PENDING.value),
                )
            self._missing(db, item.article_key, "content", item.status, item.reason)

    def save_metrics(self, item: MetricSnapshot) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO metric_snapshots
                (article_key,readNum,likeNum,oldLikeNum,shareNum,commentNum,source,
                 checksum,captured_at,status,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (item.article_key, item.readNum, item.likeNum, item.oldLikeNum,
                 item.shareNum, item.commentNum, item.source, item.checksum,
                 item.captured_at, item.status.value, item.reason),
            )
            self._missing(db, item.article_key, "metrics", item.status, item.reason)

    def save_comments(self, article_key: str, comments: list[CommentRecord], status: Status, reason: str = "") -> None:
        with self.connect() as db:
            for item in comments:
                db.execute(
                    """INSERT OR REPLACE INTO comments VALUES(?,?,?,?,?,?,?,?,?)""",
                    (item.comment_id, article_key, item.content, item.nickname, item.like_num,
                     item.created_at, json.dumps(item.assets, ensure_ascii=False),
                     json.dumps(item.raw, ensure_ascii=False), utc_now()),
                )
                for asset in item.assets:
                    db.execute(
                        "INSERT OR IGNORE INTO assets(article_key,comment_id,url,kind,status) VALUES(?,?,?,?,?)",
                        (article_key, item.comment_id, asset, "comment", Status.PENDING.value),
                    )
                for reply in item.replies:
                    db.execute(
                        """INSERT OR REPLACE INTO comment_replies VALUES(?,?,?,?,?,?,?,?,?)""",
                        (reply.reply_id, item.comment_id, article_key, reply.content, reply.nickname,
                         reply.like_num, reply.created_at, json.dumps(reply.raw, ensure_ascii=False), utc_now()),
                    )
            self._missing(db, article_key, "comments", status, reason)

    @staticmethod
    def _missing(db: sqlite3.Connection, article_key: str, layer: str, status: Status, reason: str) -> None:
        if status == Status.OK:
            db.execute("DELETE FROM missing_records WHERE article_key=? AND layer=?", (article_key, layer))
        else:
            db.execute(
                """INSERT INTO missing_records(article_key,layer,status,reason,captured_at)
                VALUES(?,?,?,?,?) ON CONFLICT(article_key,layer) DO UPDATE SET
                status=excluded.status,reason=excluded.reason,captured_at=excluded.captured_at""",
                (article_key, layer, status.value, reason, utc_now()),
            )

    def set_checkpoint(self, provider: str, biz: str, cursor: str, fingerprint: str, completed: bool) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO checkpoints VALUES(?,?,?,?,?,?)""",
                (provider, biz, cursor, fingerprint, int(completed), utc_now()),
            )

    def get_checkpoint(self, provider: str, biz: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM checkpoints WHERE provider=? AND biz=?", (provider, biz)).fetchone()
            return dict(row) if row else {}

    def rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, args).fetchall()]
