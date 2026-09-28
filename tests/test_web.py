import json
import queue

from fastapi.testclient import TestClient

from gzh_reader.web import STATE, CollectRequest, app
from gzh_reader.workspace import DEFAULT_OUTPUT_ROOT
from gzh_reader.task_worker import run_collect_task


def test_home_and_foreground_consent_gate():
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "测试模型连接" in home.text
    assert "豆包（火山方舟）" in home.text
    assert "本次新增篇数上限（试跑才填写；整账号读取留空）" in home.text
    assert "检查能否读取微信画面" in home.text
    assert "支持任意公开公众号" in home.text
    assert "例如：监所家属" not in home.text
    assert str(DEFAULT_OUTPUT_ROOT) in home.text
    assert CollectRequest(url="https://mp.weixin.qq.com/s/example").output == str(DEFAULT_OUTPUT_ROOT)
    STATE.update({"stage": "idle", "detail": {}, "running": False})
    response = client.post("/api/collect", json={
        "url": "https://mp.weixin.qq.com/s?__biz=b",
        "foreground_consent": False,
    })
    assert response.status_code == 400


def test_model_test_passes_key_only_to_client_and_not_to_response(monkeypatch):
    class FakeDeepSeekClient:
        def __init__(self, key):
            assert key == "secret-test-key"

        def test(self):
            return {"connected": True, "model": "deepseek-flash"}

    monkeypatch.setattr("gzh_reader.deepseek.DeepSeekClient", FakeDeepSeekClient)
    response = TestClient(app).post(
        "/api/model/test", json={"deepseek_api_key": "secret-test-key"}
    )
    assert response.status_code == 200
    assert response.json() == {"connected": True, "model": "deepseek-flash"}
    assert "secret-test-key" not in response.text


def test_model_test_can_select_doubao_without_exposing_key(monkeypatch):
    class FakeArkClient:
        def __init__(self, key, *, model_id):
            assert key == "ark-test-secret"
            assert model_id == "ep-test"

        def test(self):
            return {"connected": True, "model": "ep-test"}

    monkeypatch.setattr("gzh_reader.ark.ArkClient", FakeArkClient)
    response = TestClient(app).post("/api/model/test", json={
        "model_provider": "doubao", "model_api_key": "ark-test-secret",
        "model_id": "ep-test",
    })
    assert response.status_code == 200
    assert response.json() == {"connected": True, "model": "ep-test"}
    assert "ark-test-secret" not in response.text


def test_unknown_model_provider_is_rejected_before_starting_collection():
    STATE.update({"stage": "idle", "detail": {}, "running": False})
    response = TestClient(app).post("/api/collect", json={
        "url": "https://mp.weixin.qq.com/s/test", "foreground_consent": True,
        "model_provider": "unknown", "deepseek_review": True,
    })
    assert response.status_code == 422
    assert STATE["running"] is False


def test_bounded_run_is_reported_incomplete_not_complete(monkeypatch, tmp_path):
    workspace = tmp_path / "监所家属"
    (workspace / "audit").mkdir(parents=True)
    (workspace / "audit" / "human-agent-progress.json").write_text(
        json.dumps({
            "complete": False, "captured_article_count": 154,
            "declared_article_count": 301, "stop_reason": "batch_new_limit",
        }), encoding="utf-8",
    )

    class FakeCollector:
        def __init__(self, progress, **kwargs):
            pass

        def collect(self, *args, **kwargs):
            return workspace

    class Events:
        def __init__(self):
            self.rows = []

        def put(self, item):
            self.rows.append(item)

    def fake_launch(payload, task_id):
        events = Events()
        run_collect_task(payload, events)
        final = events.rows[-1]
        STATE.update({"stage": final["stage"], "detail": final["detail"], "running": False})

    monkeypatch.setattr("gzh_reader.human_agent.MacHumanAccountCollector", FakeCollector)
    monkeypatch.setattr("gzh_reader.web._launch_task", fake_launch)
    STATE.update({"stage": "idle", "detail": {}, "running": False})
    response = TestClient(app).post("/api/collect", json={
        "url": "https://mp.weixin.qq.com/s/test",
        "account_name": "监所家属", "foreground_consent": True,
    })
    assert response.status_code == 200
    assert STATE["stage"] == "incomplete"
    assert STATE["detail"]["reason"] == "batch_new_limit"


def test_web_request_defaults_to_full_account_not_ten_articles():
    from gzh_reader.web import CollectRequest

    request = CollectRequest(url="https://mp.weixin.qq.com/s/example")
    assert request.max_new_articles is None
    assert request.max_articles is None
    assert CollectRequest(url=request.url, max_new_articles=50).max_new_articles == 50


def test_stalled_worker_is_terminated_without_touching_wechat(monkeypatch):
    from gzh_reader import web

    class Process:
        alive = True
        terminated = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def join(self, timeout):
            pass

    class Events:
        def get(self, timeout):
            raise queue.Empty

    process = Process()
    STATE.update({"task_id": "task-test", "running": True, "stage": "human_action"})
    monkeypatch.setattr(web, "_STALL_SECONDS", 0)
    web._monitor_task(process, Events(), "task-test")
    assert process.terminated
    assert STATE["stage"] == "failed"
    assert "120 秒" in STATE["detail"]["error"]


def test_busy_worker_without_new_article_or_viewport_is_stopped(monkeypatch):
    from gzh_reader import web

    class Process:
        alive = True
        terminated = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def join(self, timeout):
            pass

    class Events:
        calls = 0

        def get(self, timeout):
            self.calls += 1
            if self.calls == 1:
                return {"kind": "progress", "stage": "human_action",
                        "detail": {"action": "before_scroll"}}
            raise queue.Empty

    process = Process()
    STATE.update({"task_id": "task-no-data", "running": True, "stage": "starting"})
    monkeypatch.setattr(web, "_NO_PROGRESS_SECONDS", 0)
    web._monitor_task(process, Events(), "task-no-data")
    assert process.terminated
    assert "没有新增文章或新列表位置" in STATE["detail"]["error"]


def test_stop_only_terminates_collection_process(monkeypatch):
    from gzh_reader import web

    class Process:
        alive = True
        terminated = False

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.terminated = True
            self.alive = False

        def join(self, timeout):
            pass

    process = Process()
    monkeypatch.setattr(web, "_PROCESS", process)
    STATE.update({"task_id": "task-stop", "running": True, "stage": "human_article"})
    response = TestClient(app).post("/api/stop")
    assert response.status_code == 200
    assert process.terminated
    assert STATE["stage"] == "interrupted" and STATE["running"] is False
