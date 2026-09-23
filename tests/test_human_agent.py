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
    completed_human_rows,
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


def test_scrolled_tab_still_matches_only_its_own_account():
    scrolled = [
        line("监所家属", 45), line("全部", 80), line("文章", 80, 80),
        line("阅读2579 赞10", 155),
    ]
    assert profile_matches_account(scrolled, "监所家属")
    assert not profile_matches_account(scrolled, "安徽监狱")
    article_footer = [
        line("全部", 80), line("文章", 80, 80),
        line("阅读2579 赞10", 155), line("监所家属", 700),
    ]
    assert not profile_matches_account(article_footer, "监所家属")
    observed_ocr = [
        line("v 监所家属", 20), line("六 监所家属—搜一搜", 22, 220),
        line("• 监所家属", 65), line("全部 文草", 90),
        line("亲人入狱后，家属最容易忽略的三件事", 130),
        line("阅读2579 赞10", 155),
    ]
    assert profile_matches_account(observed_ocr, "监所家属")


def test_navigation_ocr_is_not_prepended_to_card_title():
    cards = parse_profile_cards([
        line("• 监所家属", 60), line("全部 文草", 90),
        line("亲人入狱后，家属最容易忽略的三件事", 130),
        line("阅读2579 赞10", 155),
    ], 800)
    assert [card.title for card in cards] == ["亲人入狱后，家属最容易忽略的三件事"]


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


def test_raise_uses_wechat_main_process_for_detached_child_window(monkeypatch):
    target = Window(7, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    actions = []

    class FakeRunning:
        def processIdentifier(self):
            return 456

        def activateWithOptions_(self, options):
            actions.append(("activate", options))

    class FakeAppKit:
        NSApplicationActivateAllWindows = 1
        NSApplicationActivateIgnoringOtherApps = 2

        class NSRunningApplication:
            @staticmethod
            def runningApplicationsWithBundleIdentifier_(bundle_id):
                assert bundle_id == "com.tencent.xinWeChat"
                return [FakeRunning()]

    class FakeAX:
        kAXWindowsAttribute = "windows"
        kAXTitleAttribute = "title"
        kAXRaiseAction = "raise"

        @staticmethod
        def AXUIElementCreateApplication(pid):
            return pid

        @staticmethod
        def AXUIElementCopyAttributeValue(item, attr, unused):
            if attr == "windows":
                return (0, [] if item == 123 else [{"title": "微信 (窗口)"}])
            return (0, item["title"])

        @staticmethod
        def AXUIElementPerformAction(item, action):
            actions.append(("raise", item["title"]))
            return 0

    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    controller = MacHumanController.__new__(MacHumanController)
    controller.AX = FakeAX
    controller.AppKit = FakeAppKit
    assert controller._raise(target)
    assert actions == [("activate", 3), ("raise", "微信 (窗口)")]


def test_activate_running_wechat_by_bundle_id_without_english_app_name(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        pytest.fail(f"已运行微信不应再启动外部命令：{args}")

    class FakeApp:
        def activateWithOptions_(self, options):
            calls.append(options)

    class FakeAppKit:
        NSApplicationActivateAllWindows = 1
        NSApplicationActivateIgnoringOtherApps = 2

        class NSRunningApplication:
            @staticmethod
            def runningApplicationsWithBundleIdentifier_(bundle_id):
                assert bundle_id == "com.tencent.xinWeChat"
                return [FakeApp()]

    monkeypatch.setattr("gzh_reader.human_agent.subprocess.run", fake_run)
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    controller = MacHumanController.__new__(MacHumanController)
    controller.AppKit = FakeAppKit
    controller.activate()
    assert calls == [3]


def test_detached_wechat_child_window_is_discoverable():
    class FakeQuartz:
        kCGWindowListOptionAll = 0
        kCGNullWindowID = 0
        kCGWindowOwnerName = "kCGWindowOwnerName"
        kCGWindowName = "kCGWindowName"
        kCGWindowNumber = "kCGWindowNumber"
        kCGWindowOwnerPID = "kCGWindowOwnerPID"
        kCGWindowLayer = "kCGWindowLayer"
        kCGWindowBounds = "kCGWindowBounds"
        kCGWindowIsOnscreen = "kCGWindowIsOnscreen"

        @staticmethod
        def CGWindowListCopyWindowInfo(options, window_id):
            return [
                {"kCGWindowOwnerName": "WeChatAppEx", "kCGWindowName": "公众号",
                 "kCGWindowNumber": 42, "kCGWindowOwnerPID": 123,
                 "kCGWindowLayer": 0,
                 "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 600, "Height": 800}},
                {"kCGWindowOwnerName": "微信 2", "kCGWindowName": "微信 (窗口)",
                 "kCGWindowNumber": 43, "kCGWindowOwnerPID": 456,
                 "kCGWindowLayer": 0,
                 "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 900, "Height": 800}},
            ]

    controller = MacHumanController.__new__(MacHumanController)
    controller.Quartz = FakeQuartz
    assert controller.window("公众号") == Window(42, "公众号", 0, 0, 600, 800, 0, 123)
    assert controller.window("微信 (窗口)") == Window(43, "微信 (窗口)", 0, 0, 900, 800, 0, 456)


def test_window_prefers_visible_copy_over_larger_hidden_copy():
    controller = MacHumanController.__new__(MacHumanController)
    visible = Window(1, "微信 (窗口)", 0, 0, 900, 800, 0, 42, True)
    hidden = Window(2, "微信 (窗口)", 0, 0, 1200, 900, 0, 42, False)
    controller.windows = lambda: [hidden, visible]
    assert controller.window("微信 (窗口)") == visible


def test_tabbed_profile_is_not_closed_as_article_window():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.activate = lambda: None
    controller._raise = lambda window: True
    controller.ocr = lambda window: [line("监所家属", 90), line("301篇原创内容", 190)]
    controller._close_window = lambda window: pytest.fail("不应关闭公众号标签页")
    assert controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属") == browser
    assert controller.tabbed_profile_account == "监所家属"


def test_tabbed_article_must_replace_profile_before_capture(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.ocr = lambda window: [line("监所家属", 90), line("301篇原创内容", 190)]
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", iter([0, 9]).__next__)
    with pytest.raises(RuntimeError, match="未能确认文章标签"):
        controller.wait_article("第一篇文章")


def test_tabbed_profile_fallback_never_accepts_author_only(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.ocr = lambda window: [line("监所家属", 720), line("正文内容" * 20, 400)]
    controller._raise = lambda window: True
    clicks = []
    controller.click = lambda x, y: clicks.append((x, y))
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", iter([0, 9]).__next__)
    assert controller._tabbed_profile("监所家属") is None
    assert clicks == [(900 * 0.42, 800 * 0.03)]


def test_tabbed_close_refuses_when_profile_is_current():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.ocr = lambda window: [line("监所家属", 90), line("301篇原创内容", 190)]
    controller._close_window = lambda window: pytest.fail("不应关闭公众号标签页")
    with pytest.raises(RuntimeError, match="拒绝再次关闭"):
        controller.close_article()


def test_miniprogram_prompt_uses_cancel_only(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda title: browser
    dismissed = False
    clicks = []

    def current_ocr(window):
        if dismissed:
            return [line("正文内容", 200)]
        return [line("即将打开小程序", 300), line("取消", 410, 400), line("允许", 410, 520)]

    def click(x, y):
        nonlocal dismissed
        clicks.append((x, y))
        dismissed = True

    controller.ocr = current_ocr
    controller.click = click
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.dismiss_miniprogram_prompt()
    assert clicks == [(490, 420)]


def test_copy_body_focuses_blank_margin_not_embedded_card(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.focus_browser = lambda: browser
    controller.dismiss_miniprogram_prompt = lambda: False
    clicks = []
    controller.click = lambda x, y: clicks.append((x, y))
    controller.hotkey = lambda key, flags: None
    controller.clipboard = lambda: "正文" * 100
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.copy_page_text() == "正文" * 100
    assert clicks == [(24, 180)]


def test_capture_records_action_sequence_without_article_text(monkeypatch):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)

    class FakeController:
        def click(self, x, y):
            pass

        def wait_window(self, title, timeout):
            return profile

        def copy_link(self):
            return "https://mp.weixin.qq.com/s/test"

        def copy_page_text(self):
            return "文章正文" * 30

        def page_bottom(self):
            return "阅读 20\n监所家属"

        def close_article(self):
            pass

        def focus_profile(self):
            return profile

    collector = MacHumanAccountCollector(controller=FakeController())
    events = []
    collector.action = events.append
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    collector._capture_card(profile, ProfileCard("标题", 20, 1, 100, 200), "监所家属")
    assert events == [
        "before_click_card", "article_window_open", "before_copy_link",
        "before_copy_body", "before_page_bottom", "before_close_article",
        "article_window_closed",
    ]


def test_article_title_mismatch_closes_tab_before_next_card(monkeypatch):
    profile = Window(1, "微信 (窗口)", 0, 0, 900, 800, 0, 42)
    actions = []

    class FakeController:
        def click(self, x, y):
            actions.append("click")

        def wait_article(self, title, timeout):
            actions.append("title_mismatch")
            raise RuntimeError("文章标题不匹配")

        def close_article(self):
            actions.append("close_article")

        def focus_profile(self):
            actions.append("focus_profile")
            return profile

    collector = MacHumanAccountCollector(controller=FakeController())
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="文章标题不匹配"):
        collector._capture_card(
            profile, ProfileCard("卡片标题", 20, 1, 100, 200), "监所家属"
        )
    assert actions == ["click", "title_mismatch", "close_article", "focus_profile"]


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


def test_resume_includes_both_human_account_id_versions(tmp_path):
    store = Store(tmp_path / "archive.sqlite3")
    with store.connect() as db:
        for biz, source in [
            ("human-old", "mac_human_agent"),
            ("human:new", "mac_human_agent"),
            ("sample", "public_html_sample"),
        ]:
            db.execute(
                "INSERT INTO accounts(biz,name,source,status) VALUES(?,?,?,'ok')",
                (biz, "监所家属", source),
            )
            db.execute(
                "INSERT INTO articles(stable_key,biz,url,title,status) VALUES(?,?,?,?, 'ok')",
                (biz, biz, f"https://mp.weixin.qq.com/s/{biz}", biz),
            )
            db.execute(
                "INSERT INTO content_snapshots(article_key,checksum,status) VALUES(?,?,'ok')",
                (biz, biz),
            )
            db.execute(
                "INSERT INTO metric_snapshots(article_key,readNum,likeNum,status) VALUES(?,10,0,'ok')",
                (biz,),
            )
    rows = completed_human_rows(store, "监所家属")
    assert {row["title"] for row in rows} == {"human-old", "human:new"}


def test_small_batches_resume_without_reopening_known_cards(tmp_path: Path):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)
    lines = [
        line("监所家属", 30), line("301篇原创内容", 60),
        line("第一篇文章", 110), line("阅读101 赞1", 155),
        line("第二篇文章", 250), line("阅读202 赞2", 295),
    ]

    class FakeController:
        def activate(self):
            raise AssertionError("已有公众号窗口时不应先前置微信主窗口")

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
