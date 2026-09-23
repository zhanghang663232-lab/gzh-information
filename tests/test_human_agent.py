import json
import subprocess

import pytest
from pathlib import Path

from gzh_reader.human_agent import (
    HumanCapture,
    MacHumanAccountCollector,
    MacHumanController,
    OcrLine,
    ProfileCard,
    Window,
    card_signature,
    account_name_from_profile,
    article_matches_account,
    meaningful_body,
    parse_count,
    parse_profile_cards,
    parse_share_num,
    profile_identity_conflicts,
    profile_matches_account,
    unique_known_cards,
)
from gzh_reader.storage import Store


def line(text: str, y: float, x: float = 20) -> OcrLine:
    return OcrLine(text=text, x=x, y=y, width=180, height=20)


def test_parse_count_keeps_zero_and_converts_wan():
    assert parse_count("0") == 0
    assert parse_count("1.2万") == 12000
    assert parse_count("100001") == 100001


def test_parse_profile_cards_uses_metric_row_as_safe_click():
    lines = [
        line("监所家属", 20),
        line("300篇原创内容", 45),
        line("无期徒刑，真的要坐一辈子吗？", 110),
        line("阅读731 赞5", 155),
        line("亲人入狱后，家属最容易忽略的三件事", 280),
        line("阅读2230 赞10", 325),
    ]
    cards = parse_profile_cards(lines, 800)
    assert [item.title for item in cards] == ["无期徒刑，真的要坐一辈子吗？", "亲人入狱后，家属最容易忽略的三件事"]
    assert cards[0].read_num == 731 and cards[1].like_num == 10
    assert cards[0].click_y == 165


def test_account_name_and_share_parsing():
    lines = [line("•••", 10), line("监所家属", 30), line("300篇原创内容", 60)]
    assert account_name_from_profile(lines) == "监所家属"
    assert parse_share_num("点赞 5  转发 12") == 12


def test_account_identity_must_match_profile_and_article_author():
    profile = [line("监所家属", 30), line("300篇原创内容", 60)]
    assert profile_matches_account(profile, "监所家属")
    assert not profile_matches_account(profile, "安徽监狱")
    assert article_matches_account("阅读 10\n监所家属\n写留言", "监所家属")
    assert not article_matches_account("阅读 10\n安徽监狱\n写留言", "监所家属")


def test_scrolled_profile_without_header_is_not_an_identity_mismatch():
    assert not profile_identity_conflicts([line("阅读731 赞5", 155)], "监所家属")
    assert not profile_identity_conflicts(
        [line("监所家属", 30), line("300篇原创内容", 60)], "监所家属"
    )
    assert profile_identity_conflicts(
        [line("安徽监狱", 30), line("300篇原创内容", 60)], "监所家属"
    )


def test_url_only_clipboard_is_not_valid_article_body():
    assert not meaningful_body("https://mp.weixin.qq.com/s/example")
    assert not meaningful_body("短正文")
    assert meaningful_body("这是文章正文。" * 20)


def test_ocr_worker_result_is_parsed_and_sorted(monkeypatch):
    window = Window(123, "公众号", 0, 0, 400, 600, 0, 42)
    payload = {"lines": [
        {"text": "第二行", "x": 1, "y": 30, "width": 20, "height": 10},
        {"text": "第一行", "x": 1, "y": 10, "width": 20, "height": 10},
    ]}

    def fake_run(args, **kwargs):
        assert args[1:3] == ["-m", "gzh_reader.ocr_worker"]
        assert kwargs["timeout"] == 12
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("gzh_reader.human_agent.subprocess.run", fake_run)
    controller = MacHumanController.__new__(MacHumanController)
    assert [item.text for item in controller.ocr(window)] == ["第一行", "第二行"]


def test_ocr_worker_timeout_is_a_recoverable_error(monkeypatch):
    window = Window(123, "公众号", 0, 0, 400, 600, 0, 42)

    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr("gzh_reader.human_agent.subprocess.run", timed_out)
    controller = MacHumanController.__new__(MacHumanController)
    with pytest.raises(RuntimeError, match="OCR 超时"):
        controller.ocr(window)


def test_close_shortcut_is_not_sent_without_verified_window_focus():
    controller = MacHumanController.__new__(MacHumanController)
    events = []
    controller._raise = lambda window: False
    controller.hotkey = lambda *args: events.append(args)
    with pytest.raises(RuntimeError, match="已拒绝关闭快捷键"):
        controller._close_window(Window(1, "微信 (窗口)", 0, 0, 400, 600, 0, 42))
    assert events == []


def test_known_card_signature_requires_unique_url():
    rows = [
        {"url": "u1", "title": "标题", "readNum": 10, "likeNum": 0},
        {"url": "u2", "title": "标题", "readNum": 10, "likeNum": 0},
        {"url": "u3", "title": "另一篇", "readNum": 20, "likeNum": 1},
    ]
    known = unique_known_cards(rows)
    assert ("标题", 10, 0) not in known
    assert known[("另一篇", 20, 1)] == "u3"
    assert card_signature(ProfileCard("另一篇", 20, 1, 100, 200)) == ("另一篇", 20, 1)


def test_small_batches_resume_without_reopening_known_cards(tmp_path: Path):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)
    lines = [
        line("监所家属", 30), line("301篇原创内容", 60),
        line("第一篇文章", 110), line("阅读101 赞1", 155),
        line("第二篇文章", 250), line("阅读202 赞2", 295),
    ]

    class FakeController:
        def activate(self):
            pass

        def open_profile(self, url, account_name):
            return profile

        def scroll_to_top(self):
            return profile

        def focus_profile(self):
            return profile

        def ocr(self, window):
            return lines

        def scroll(self):
            return False

    opened = []
    collector = MacHumanAccountCollector(controller=FakeController())

    def capture(window, card, account_name):
        opened.append(card.title)
        return HumanCapture(
            url="https://mp.weixin.qq.com/s/" + ("first" if card.title == "第一篇文章" else "second"),
            title=card.title, body="这是公开文章正文。" * 30,
            read_num=card.read_num, like_num=card.like_num,
            share_num=None, comment_num=None,
        )

    collector._capture_card = capture
    url = "https://mp.weixin.qq.com/s/example"
    workspace = collector.collect(url, tmp_path, account_name="监所家属", max_new_articles=1)
    assert opened == ["第一篇文章"]
    collector.collect(url, tmp_path, account_name="监所家属", max_new_articles=1)
    assert opened == ["第一篇文章", "第二篇文章"]
    assert len(Store(workspace / "database" / "archive.sqlite3").rows("SELECT url FROM articles")) == 2
