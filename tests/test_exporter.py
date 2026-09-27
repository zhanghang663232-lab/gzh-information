from pathlib import Path

import pytest

from gzh_reader.exporter import ExporterListProvider
from gzh_reader.http import Response
from gzh_reader.models import Account
from gzh_reader.storage import Store


class FakeHttp:
    def __init__(self, pages):
        self.pages = iter(pages)

    def request(self, *args, **kwargs):
        import json
        return Response(200, json.dumps(next(self.pages)).encode(), {})


class RecordingHttp(FakeHttp):
    def __init__(self, pages):
        super().__init__(pages)
        self.calls = []

    def request(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return super().request(url, **kwargs)


def test_accountbyurl_resolves_fakeid_and_official_base(tmp_path: Path):
    http = RecordingHttp([{"data": {"biz": "b", "fakeid": "f", "nickname": "账号"}}])
    provider = ExporterListProvider("secret", Store(tmp_path / "db.sqlite3"), tmp_path / "raw", http)
    account = provider.resolve_account("https://mp.weixin.qq.com/s/short-token")
    assert account.fakeid == "f" and account.name == "账号"
    assert http.calls[0][0].startswith("https://down.mptext.top/api/public/v1/accountbyurl")


def test_full_url_uses_biz_as_fakeid_without_accountbyurl(tmp_path: Path):
    http = RecordingHttp([])
    provider = ExporterListProvider("secret", Store(tmp_path / "db.sqlite3"), tmp_path / "raw", http)
    account = provider.resolve_account("https://mp.weixin.qq.com/s?__biz=b&mid=1&idx=1")
    assert account.biz == "b" and account.fakeid == "b"
    assert http.calls == []


def test_pagination_completion(tmp_path: Path):
    pages = [
        {"data": {"list": [{"url": "https://mp.weixin.qq.com/s?__biz=b&mid=1&idx=1"}], "next": "10"}},
        {"data": {"list": [], "is_end": True}},
    ]
    store = Store(tmp_path / "db.sqlite3")
    provider = ExporterListProvider("secret", store, tmp_path / "raw", FakeHttp(pages), "https://example.test")
    result = list(provider.enumerate_articles(Account(biz="b")))
    assert len(result) == 1
    assert store.get_checkpoint("exporter", "b")["completed"] == 1


def test_repeated_page_is_not_called_complete(tmp_path: Path):
    items = [
        {"url": f"https://mp.weixin.qq.com/s?__biz=b&mid={index}&idx=1"}
        for index in range(20)
    ]
    page = {"data": {"list": items, "next": "20"}}
    store = Store(tmp_path / "db.sqlite3")
    provider = ExporterListProvider("secret", store, tmp_path / "raw", FakeHttp([page, page]))
    with pytest.raises(RuntimeError, match="重复分页"):
        list(provider.enumerate_articles(Account(biz="b")))
    assert store.get_checkpoint("exporter", "b")["completed"] == 0

