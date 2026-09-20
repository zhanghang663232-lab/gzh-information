from pathlib import Path

from gzh_reader.agent import AgentState, MacWechatAgentProvider, Viewport
from gzh_reader.audit import security_scan
from gzh_reader.models import Account
from gzh_reader.proxy import MacProxyManager
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


def test_agent_does_not_claim_complete_without_bottom():
    urls, status = MacWechatAgentProvider(Observer(), max_repeat=2).enumerate_articles(Account(biz="b"))
    assert not urls and status == AgentState.INCOMPLETE


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


def test_security_scan(tmp_path: Path):
    (tmp_path / "safe.txt").write_text("hello", encoding="utf-8")
    assert security_scan(tmp_path) == []
    (tmp_path / "bad.txt").write_text("pass_ticket=secret123", encoding="utf-8")
    assert security_scan(tmp_path)[0]["kind"] == "pass_ticket"


def test_credential_file_is_scrubbed_with_private_permissions(tmp_path: Path):
    workspace = Workspace.create(tmp_path, "账号")
    workspace.write_json("runtime/credential.json", {"cookie": "secret123"}, mode=0o600)
    assert workspace.credential.stat().st_mode & 0o777 == 0o600
    workspace.scrub_credential()
    assert workspace.credential.read_text(encoding="utf-8").strip() == "{}"
