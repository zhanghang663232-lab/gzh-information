from fastapi.testclient import TestClient

from gzh_reader.web import STATE, app


def test_home_and_foreground_consent_gate():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    STATE.update({"stage": "idle", "detail": {}, "running": False})
    response = client.post("/api/collect", json={
        "url": "https://mp.weixin.qq.com/s?__biz=b",
        "foreground_consent": False,
    })
    assert response.status_code == 400
