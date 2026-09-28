import json
import urllib.error

import pytest

from gzh_reader.ark import ArkClient, ArkError
from gzh_reader.model_client import create_card_reviewer, create_client


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size):
        assert size == 1_000_001
        return json.dumps({
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }).encode()


def test_ark_uses_configured_model_and_never_returns_key(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr("gzh_reader.ark.urllib.request.urlopen", fake_urlopen)
    result = ArkClient("test-secret", model_id="ep-123").test()
    assert result == {"model": "ep-123", "connected": True,
                      "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}
    request, timeout = requests[0]
    assert timeout == 20
    assert request.full_url == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    assert request.get_header("Authorization") == "Bearer test-secret"
    assert json.loads(request.data)["model"] == "ep-123"
    assert "test-secret" not in json.dumps(result)


def test_ark_rejects_missing_key_and_redacts_http_errors(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    with pytest.raises(ArkError, match="ARK_API_KEY"):
        ArkClient()
    with pytest.raises(ArkError, match="格式无效"):
        ArkClient("请填写真实密钥")

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "test-secret", {}, None)

    monkeypatch.setattr("gzh_reader.ark.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(ArkError) as error:
        ArkClient("test-secret").test()
    assert "401" in str(error.value)
    assert "test-secret" not in str(error.value)


def test_reviewers_share_the_same_ocr_contract():
    assert isinstance(create_client("doubao", "key"), ArkClient)
    assert create_card_reviewer("doubao", "key").max_calls == 5
    with pytest.raises(ValueError, match="不支持"):
        create_client("unknown", "key")
