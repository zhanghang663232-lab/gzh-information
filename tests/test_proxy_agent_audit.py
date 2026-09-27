from pathlib import Path

import pytest

from gzh_reader.agent import AgentState, MacWechatAgentProvider, Viewport
from gzh_reader.audit import audit_workspace, security_scan
from gzh_reader.models import Account
from gzh_reader.proxy import MacProxyManager
from gzh_reader.storage import Store
from gzh_reader.workspace import Workspace


class Observer:
    def observe(self):
        return Viewport("same", [])
    def scroll(self):
        pass
    def open_next(self):
        pass
    def close_detail(self):
        pass


class BottomObserver(Observer):
    def observe(self):
        return Viewport("bottom", ["https://mp.weixin.qq.com/s?__biz=b&mid=1&idx=1"], True)


def test_agent_does_not_claim_complete_without_bottom():
    urls, status = MacWechatAgentProvider(Observer(), max_repeat=2).enumerate_articles(Account(biz="b"))
    assert not urls and status == AgentState.INCOMPLETE


def test_agent_claims_complete_only_after_verified_bottom_repeats():
    urls, status = MacWechatAgentProvider(BottomObserver()).enumerate_articles(Account(biz="b"))
    assert len(urls) == 1 and status == AgentState.COMPLETE


def test_proxy_restores_three_kinds(tmp_path: Path):
    calls = []
    def runner(args):
        calls.append(args)
        if args[1] == "-listallnetworkservices":
            return "An asterisk denotes disabled.\nWi-Fi\n"
        return "Enabled: Yes\nServer: proxy.local\nPort: 8080\n"
    manager = MacProxyManager(tmp_path / "state.json", runner)
    manager.snapshot()
    manager.enable_local(8899, consent=True)
    assert manager.restore()
    assert any("-setsocksfirewallproxy" in call for call in calls)
    assert not (tmp_path / "state.json").exists()


def test_proxy_change_requires_explicit_consent(tmp_path: Path):
    manager = MacProxyManager(tmp_path / "state.json", lambda args: "")
    with pytest.raises(PermissionError):
        manager.enable_local(8899, consent=False)


def test_security_scan(tmp_path: Path):
    (tmp_path / "safe.txt").write_text("hello", encoding="utf-8")
    assert security_scan(tmp_path) == []
    (tmp_path / "bad.txt").write_text("pass_ticket=secret123", encoding="utf-8")
    assert security_scan(tmp_path)[0]["kind"] == "pass_ticket"


def test_audit_rejects_url_only_content_even_when_database_says_ok(tmp_path: Path):
    store = Store(tmp_path / "database" / "archive.sqlite3")
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "only-link.md").write_text("https://mp.weixin.qq.com/s/one", encoding="utf-8")
    (raw / "article.md").write_text("这是文章正文。" * 30, encoding="utf-8")
    with store.connect() as db:
        db.execute("INSERT INTO accounts(biz,name,status) VALUES('human:one','监所家属','ok')")
        for key, path in (("one", "only-link.md"), ("two", "article.md")):
            db.execute(
                "INSERT INTO articles(stable_key,biz,url,title,status) VALUES(?,?,?,?, 'ok')",
                (key, "human:one", f"https://mp.weixin.qq.com/s/{key}", key),
            )
            db.execute(
                "INSERT INTO content_snapshots(article_key,markdown_path,status) VALUES(?,?,'ok')",
                (key, f"raw/{path}"),
            )
    report = audit_workspace(store, tmp_path)
    assert report["content"]["ok"] == 1
    assert report["content"]["invalid_ok_claims"] == 1
    assert any(row["article_key"] == "one" and row["layer"] == "content"
               for row in report["missing"])


def test_audit_uses_latest_metric_snapshot_and_preserves_zero(tmp_path: Path):
    store = Store(tmp_path / "database" / "archive.sqlite3")
    with store.connect() as db:
        db.execute("INSERT INTO accounts(biz,name,status) VALUES('b','账号','ok')")
        db.execute(
            "INSERT INTO articles(stable_key,biz,url,title,status) "
            "VALUES('one','b','https://mp.weixin.qq.com/s/one','文章','ok')"
        )
        db.execute(
            "INSERT INTO metric_snapshots(article_key,readNum,likeNum,status) "
            "VALUES('one',10,8,'ok')"
        )
        db.execute(
            "INSERT INTO metric_snapshots(article_key,readNum,likeNum,status) "
            "VALUES('one',NULL,0,'missing')"
        )
    report = audit_workspace(store, tmp_path)
    assert report["metrics"]["readNum"]["ok"] == 0
    assert report["metrics"]["likeNum"]["ok"] == 1
    assert report["content"]["quality_level"] == "minimum_body_length_only"


def test_credential_file_is_scrubbed_with_private_permissions(tmp_path: Path):
    workspace = Workspace.create(tmp_path, "账号")
    workspace.write_json("runtime/credential.json", {"cookie": "secret123"}, mode=0o600)
    assert workspace.credential.stat().st_mode & 0o777 == 0o600
    workspace.scrub_credential()
    assert workspace.credential.read_text(encoding="utf-8").strip() == "{}"
