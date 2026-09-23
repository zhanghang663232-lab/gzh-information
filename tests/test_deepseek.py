import json
import urllib.error

import pytest

from gzh_reader.deepseek import DeepSeekCardReviewer, DeepSeekClient, DeepSeekError
from gzh_reader.human_agent import OcrLine


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        assert size == 1_000_001
        return self.payload


def test_deepseek_test_uses_flash_non_thinking_json_and_never_returns_key(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse({
            "choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13},
        })

    monkeypatch.setattr("gzh_reader.deepseek.urllib.request.urlopen", fake_urlopen)
    result = DeepSeekClient("secret-test-key").test()
    assert result == {"model": "deepseek-flash", "connected": True,
                      "usage": {"prompt_tokens": 8, "completion_tokens": 5, "total_tokens": 13}}
    request, timeout = requests[0]
    assert timeout == 20
    assert request.get_header("Authorization") == "Bearer secret-test-key"
    assert "secret-test-key" not in json.dumps(result)
    payload = json.loads(request.data)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["model"] == "deepseek-flash"


def test_deepseek_requires_key_and_redacts_http_errors(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(DeepSeekError, match="DEEPSEEK_API_KEY"):
        DeepSeekClient()

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "secret-test-key", {}, None)

    monkeypatch.setattr("gzh_reader.deepseek.urllib.request.urlopen", fake_urlopen)
    with pytest.raises(DeepSeekError) as error:
        DeepSeekClient("secret-test-key").test()
    assert "401" in str(error.value)
    assert "secret-test-key" not in str(error.value)


def test_reviewer_only_accepts_original_ocr_lines_and_has_call_budget():
    class FakeClient:
        def complete_json(self, system, user, **kwargs):
            assert "0: 监所家属" in user
            return {"pairs": [
                {"title": 2, "metric": 3},
                {"title": 0, "metric": 3},
                {"title": 2, "metric": 99},
            ]}, {}

    lines = [
        OcrLine("监所家属", 10, 20, 100, 20),
        OcrLine("301篇原创内容", 10, 50, 100, 20),
        OcrLine("一篇文章", 10, 100, 100, 20),
        OcrLine("阅读123 赞0", 10, 140, 100, 20),
    ]
    reviewer = DeepSeekCardReviewer(FakeClient(), max_calls=1)
    cards = reviewer.review_cards(lines, 600)
    assert len(cards) == 1
    assert cards[0].title == "一篇文章"
    assert cards[0].read_num == 123 and cards[0].like_num == 0
    assert reviewer.review_cards(lines, 600) == []
    assert reviewer.calls == 1
