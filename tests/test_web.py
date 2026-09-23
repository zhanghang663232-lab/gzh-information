from fastapi.testclient import TestClient

from gzh_reader.web import STATE, app


def test_home_and_foreground_consent_gate():
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "测试 DeepSeek 连接" in home.text
    assert "本轮最多新增篇数" in home.text
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
