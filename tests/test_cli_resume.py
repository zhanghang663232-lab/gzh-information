from pathlib import Path

import pytest

from gzh_reader.cli import main
from gzh_reader.models import Account, Status
from gzh_reader.storage import Store
from gzh_reader.workspace import Workspace


def test_resume_uses_matching_human_account_and_current_profile(monkeypatch, tmp_path: Path):
    ws = Workspace.create(tmp_path, "监所家属")
    store = Store(ws.database)
    store.upsert_account(Account(
        biz="unresolved-sample", name="监所家属", source_url="https://example.com/wrong",
        source="public_html_sample", status=Status.MISSING,
    ))
    sample = "https://mp.weixin.qq.com/s/re6qDh-h6rldSZ3j0q9cTw"
    store.upsert_account(Account(
        biz="human:target", name="监所家属", source_url=sample,
        source="mac_human_agent", status=Status.OK,
    ))
    calls = []

    class FakeCollector:
        def __init__(self, progress):
            self.progress = progress

        def collect(self, url, output, *, account_name, max_articles, max_new_articles):
            calls.append((url, output, account_name, max_articles, max_new_articles))

    monkeypatch.setattr("gzh_reader.human_agent.MacHumanAccountCollector", FakeCollector)
    assert main(["resume", "--workspace", str(ws.root), "--max-articles", "20"]) == 0
    assert calls == [(sample, ws.root.parent, "监所家属", 20, None)]


def test_collect_defaults_to_unlimited_new_articles(monkeypatch, tmp_path: Path):
    calls = []

    class FakeCollector:
        def __init__(self, progress, **kwargs):
            pass

        def collect(self, url, output, *, account_name, max_articles, max_new_articles):
            calls.append((max_articles, max_new_articles))
            return output

    monkeypatch.setattr("gzh_reader.human_agent.MacHumanAccountCollector", FakeCollector)
    assert main(["collect", "--url", "https://mp.weixin.qq.com/s/example",
                 "--output", str(tmp_path)]) == 0
    assert calls == [(None, None)]


def test_resume_rejects_ambiguous_human_accounts(tmp_path: Path):
    ws = Workspace.create(tmp_path, "监所家属")
    store = Store(ws.database)
    for biz in ("human:one", "human:two"):
        store.upsert_account(Account(
            biz=biz, name="监所家属", source_url="https://mp.weixin.qq.com/s/example",
            source="mac_human_agent", status=Status.OK,
        ))
    with pytest.raises(RuntimeError, match="唯一且匹配"):
        main(["resume", "--workspace", str(ws.root)])
