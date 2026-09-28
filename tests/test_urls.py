import pytest

from gzh_reader.capture_addon import CredentialCapture
from gzh_reader.urls import article_stable_key, normalize_url, parse_biz, redact


def test_url_and_stable_key():
    url = "https://mp.weixin.qq.com/s?__biz=MzA123&mid=7&idx=1&sn=x&utm_source=no"
    assert parse_biz(url) == "MzA123"
    assert "utm_source" not in normalize_url(url)
    assert article_stable_key(url=url) == article_stable_key(url=normalize_url(url))


def test_stable_key_fallbacks():
    assert article_stable_key(aid="8") == "aid:8"
    assert article_stable_key(appmsgid="9", itemidx="2") == "msg:9:2"
    with pytest.raises(ValueError):
        article_stable_key()


def test_redaction():
    value = "pass_ticket=abc wap_sid2: xyz Cookie=secret Authorization:Bearer"
    assert "abc" not in redact(value)
    assert "secret" not in redact(value)


def test_captured_article_url_drops_credentials():
    value = (
        "https://mp.weixin.qq.com/s?__biz=b&mid=1&idx=2&sn=x"
        "&key=secret123&pass_ticket=secret456&scene=9"
    )
    public = CredentialCapture._public_url(value)
    assert "key=" not in public and "pass_ticket" not in public and "scene" not in public
    assert parse_biz(public) == "b"

