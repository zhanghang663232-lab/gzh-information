from pathlib import Path

from gzh_reader.models import Account, ArticleSeed, MetricSnapshot, Status
from gzh_reader.storage import Store


def test_merge_and_zero_metrics(tmp_path: Path):
    store = Store(tmp_path / "archive.sqlite3")
    store.upsert_account(Account(biz="b", name="账号", status=Status.OK))
    article = ArticleSeed(stable_key="url:x", url="https://mp.weixin.qq.com/s?__biz=b", biz="b")
    store.upsert_article(article)
    store.upsert_article(article)
    store.save_metrics(MetricSnapshot(
        article_key="url:x", readNum=100001, likeNum=0, oldLikeNum=0,
        shareNum=0, commentNum=0, status=Status.OK,
    ))
    assert store.rows("SELECT COUNT(*) n FROM articles")[0]["n"] == 1
    row = store.rows("SELECT * FROM metric_snapshots")[0]
    assert row["readNum"] == 100001
    assert row["likeNum"] == 0


def test_missing_ledger_clears_on_success(tmp_path: Path):
    store = Store(tmp_path / "archive.sqlite3")
    store.upsert_account(Account(biz="b", status=Status.OK))
    store.upsert_article(ArticleSeed(stable_key="url:x", url="https://x", biz="b"))
    store.save_metrics(MetricSnapshot(article_key="url:x", status=Status.MISSING, reason="x"))
    assert store.rows("SELECT COUNT(*) n FROM missing_records")[0]["n"] == 1
    store.save_metrics(MetricSnapshot(article_key="url:x", readNum=0, likeNum=0, oldLikeNum=0,
                                      shareNum=0, commentNum=0, status=Status.OK))
    assert store.rows("SELECT COUNT(*) n FROM missing_records")[0]["n"] == 0


