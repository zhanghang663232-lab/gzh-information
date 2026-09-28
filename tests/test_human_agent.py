import json
import hashlib
import subprocess

import pytest
from pathlib import Path

from gzh_reader.human_agent import (
    ArticleNotOpenedError,
    HumanCapture,
    MacHumanAccountCollector,
    MacHumanController,
    MiniProgramInterceptedError,
    WechatLoginRequiredError,
    OcrLine,
    ProfileCard,
    Window,
    account_tab_line,
    copy_link_menu_line,
    article_menu_x,
    article_title_visible,
    card_signature,
    completed_human_rows,
    account_name_from_profile,
    article_matches_account,
    meaningful_body,
    is_login_required_text,
    parse_count,
    parse_profile_cards,
    parse_share_num,
    profile_identity_conflicts,
    profile_matches_account,
    target_article_tab,
    target_article_tabs,
    menu_matches_article_tab,
    verified_article_menu_center,
    select_article_browser,
    titles_conflict,
    tab_menu_x,
    unique_known_cards,
    verified_account_name,
)
from gzh_reader.storage import Store


def line(text: str, y: float, x: float = 20) -> OcrLine:
    return OcrLine(text=text, x=x, y=y, width=180, height=20)


def test_parse_count_keeps_zero_and_converts_wan():
    assert parse_count("0") == 0
    assert parse_count("1.2万") == 12000
    assert parse_count("100001") == 100001


def test_link_title_conflict_allows_ocr_punctuation_but_rejects_other_article():
    assert not titles_conflict("罚金没缴完，还能减刑吗？", "罚金没缴完还能减刑吗")
    assert titles_conflict("服刑人员的劳动改造，家属关心的都在这里", "罚金没缴完，还能减刑吗？")


def test_parse_profile_cards_skips_clipped_card_and_clicks_visible_title():
    lines = [
        line("监所家属", 20),
        line("300篇原创内容", 45),
        line("无期徒刑，真的要坐一辈子吗？", 110),
        line("阅读731 赞5", 155),
        line("亲人入狱后，家属最容易忽略的三件事", 280),
        line("阅读2230 赞10", 325),
    ]
    cards = parse_profile_cards(lines, 800)
    assert [item.title for item in cards] == ["亲人入狱后，家属最容易忽略的三件事"]
    assert cards[0].read_num == 2230 and cards[0].like_num == 10
    assert cards[0].click_y == 290


def test_profile_metrics_do_not_join_friend_share_count_into_likes():
    lines = [
        line("监所家属", 30), line("301篇原创内容", 60),
        line("罚金没缴完，还能减刑吗？", 432),
        line("阅读3.8万 赞159 1个朋友转发", 454),
    ]
    cards = parse_profile_cards(lines, 809)
    assert len(cards) == 1
    assert cards[0].read_num == 38000
    assert cards[0].like_num == 159


def test_account_name_and_share_parsing():
    lines = [line("•••", 10), line("监所家属", 30), line("300篇原创内容", 60)]
    assert account_name_from_profile(lines) == "监所家属"
    assert parse_share_num("点赞 5  转发 12") == 12
    assert parse_share_num("阅读 5652 点赞 23 1个朋友转发") is None


def test_account_identity_must_match_profile_and_article_author():
    profile = [line("监所家属", 30), line("300篇原创内容", 60)]
    assert profile_matches_account(profile, "监所家属")
    assert not profile_matches_account(profile, "安徽监狱")
    assert article_matches_account("阅读 10\n监所家属\n写留言", "监所家属")
    assert not article_matches_account("阅读 10\n安徽监狱\n写留言", "监所家属")
    assert article_matches_account("阅读 10\nNetskaot\n写留言", "Netskao")
    assert not article_matches_account("阅读 10\nNetskaoTech\n写留言", "Netskao")


def test_noisy_badge_footer_needs_clean_article_byline():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.focus_browser = lambda: browser
    controller.ocr = lambda window, **kwargs: [
        line("Netskao 2026年9月26日", 115)
    ]
    controller.verify_article_account("Netskao")
    with pytest.raises(RuntimeError, match="顶部未能核对"):
        controller.verify_article_account("Netskaot")
    controller.ocr = lambda window, **kwargs: [line("原创 NetskaoTech 2026年9月26日", 115)]
    with pytest.raises(RuntimeError, match="顶部未能核对"):
        controller.verify_article_account("Netskao")


def test_explicit_wechat_relogin_notice_stops_before_profile_actions():
    assert is_login_required_text("为了你的账号安全，\n请重新登录。")
    assert not is_login_required_text("普通页面提示：稍后再试")
    dialog = Window(7, "", 100, 100, 560, 310, 0, 123, onscreen=True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.Quartz = type("Q", (), {"CGWindowListCopyWindowInfo": lambda *args: []})
    controller.windows = lambda: [dialog]
    controller.ocr = lambda window: [line("为了你的账号安全，请重新登录。", 100)]
    assert controller.login_required()
    with pytest.raises(WechatLoginRequiredError, match="采集已停止"):
        controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属")


def test_explicit_account_name_beats_search_tab_ocr():
    lines = [line("六 监所家属-搜一搜", 10), line("监所家属", 65),
             line("301篇原创内容", 110)]
    assert verified_account_name(lines, "监所家属") == "监所家属"
    with pytest.raises(RuntimeError, match="公众号主页不匹配"):
        verified_account_name(lines, "安徽监狱")


def test_article_title_visible_tolerates_punctuation_but_not_other_story():
    assert article_title_visible(
        "第一次给服刑人员写信，怎么写？",
        [line("第一次给服刑人员写信怎么写", 100)],
    )
    assert not article_title_visible(
        "第一次给服刑人员写信，怎么写？",
        [line("服刑几年和十几年，服刑人员生活有何不同", 100)],
    )


def test_tab_menu_uses_observed_spacing_not_fixed_window_ratio():
    tabs = [line("监所家属 - 搜一搜", 10, 90), line("监所家属", 10, 305),
            line("监所家属 - 搜一搜", 10, 500), line("文章标题", 10, 710)]
    assert tab_menu_x(tabs, 1022) == pytest.approx(871.7)
    assert tab_menu_x([tabs[-1]], 1022) is None


def test_copy_link_finds_dropdown_inside_article_window(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 1022, 768, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.Quartz = type("Q", (), {})
    controller.focus_browser = lambda: browser
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.windows = lambda: [browser]
    controller.hotkey = lambda key, flags: None
    controller.article_menu_center = lambda window, title, lines: (835, 24)
    controller.require_foreground = lambda window: None
    clicked = []
    controller.click = lambda x, y: clicked.append((x, y))
    controller.ocr = lambda window, **kwargs: (
        [line("监所家属 - 搜一搜", 10, 487),
         line("亲人刚进监狱那几个月，家属千万别", 10, 680),
         line("亲人刚进监狱那几个月，家属千万别做这件事", 100, 300)]
        if not clicked else [line("复制链接", 200, 760), line("刷新", 160, 760),
                             line("调整文字大小", 240, 760)]
    )
    controller.clipboard = lambda: "https://mp.weixin.qq.com/s/test" if len(clicked) == 2 else ""

    class Board:
        def clearContents(self):
            pass

    class Pasteboard:
        @staticmethod
        def generalPasteboard():
            return Board()

    controller.AppKit = type("A", (), {"NSPasteboard": Pasteboard})
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.copy_link("亲人刚进监狱那几个月，家属千万别做这件事") == "https://mp.weixin.qq.com/s/test"
    assert len(clicked) == 2


def test_copy_link_menu_requires_real_menu_context():
    item = line("复制链接", 200, 760)
    assert copy_link_menu_line([item]) is None
    assert copy_link_menu_line([item, line("刷新", 160), line("全文翻译", 240)]) is item


def test_copy_link_stops_before_menu_when_article_title_is_missing():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda title: browser
    controller.require_foreground = lambda window: None
    controller.dismiss_miniprogram_prompt = lambda: False
    controller.ocr = lambda window, **kwargs: [line("其他文章标题", 100)]
    controller.article_menu_center = lambda window, title, lines: pytest.fail("标题不符时不能定位菜单")
    controller.click = lambda x, y: pytest.fail("标题不符时不能点击")
    with pytest.raises(RuntimeError, match="标题未确认"):
        controller.copy_link("被称为监狱中的监狱，严管队里有多难熬？")


def test_copy_link_rejects_menu_on_other_article_tab():
    browser = Window(42, "微信 (窗口)", 0, 0, 1022, 768, 0, 123)
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda name: browser
    controller.require_foreground = lambda window: None
    controller.dismiss_miniprogram_prompt = lambda: False
    controller.ocr = lambda window, **kwargs: [
        line(title[:18], 10, 680), line(title, 100, 300),
    ]
    controller.article_menu_center = lambda window, title, lines: (410, 24)
    controller.click = lambda x, y: pytest.fail("菜单不属于目标标签时不能点击")
    with pytest.raises(RuntimeError, match="不属于目标文章标签"):
        controller.copy_link(title)


def test_copy_link_identifies_ai_agreement_without_accepting_it(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 1022, 768, 0, 123)
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda name: browser
    controller.require_foreground = lambda window: None
    controller.dismiss_miniprogram_prompt = lambda: False
    controller.article_menu_center = lambda window, title, lines: (835, 24)
    controller.ocr = lambda window, **kwargs: (
        [line("微信小微功能服务协议", 250)]
        if kwargs.get("visible") and "region" not in kwargs else
        [] if kwargs.get("visible") else
        [line(title[:18], 10, 680), line(title, 100, 300)]
    )
    controller.click = lambda x, y: None
    controller.hotkey = lambda key, flags: pytest.fail("协议不能被自动接受或拒绝")
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", iter([0, 5]).__next__)
    with pytest.raises(RuntimeError, match="未同意或拒绝"):
        controller.copy_link(title)


def test_copy_link_reads_open_menu_outside_initial_crop(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 1022, 768, 0, 123)
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda name: browser
    controller.require_foreground = lambda window: None
    controller.dismiss_miniprogram_prompt = lambda: False
    controller.article_menu_center = lambda window, expected, lines: (835, 24)
    controller.ocr = lambda window, **kwargs: (
        [] if "region" in kwargs else
        [line("复制链接", 150, 780), line("刷新", 190, 780),
         line("调整文字大小", 230, 780)] if kwargs.get("visible") else
        [line(title[:18], 10, 680), line(title, 100, 300)]
    )
    clicks = []
    controller.click = lambda x, y: clicks.append((x, y))
    controller.clipboard = lambda: "https://mp.weixin.qq.com/s/verified" if len(clicks) == 2 else ""

    class Board:
        def clearContents(self):
            pass

    controller.AppKit = type("A", (), {"NSPasteboard": type("P", (), {"generalPasteboard": lambda: Board()})})
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", iter([0, 5]).__next__)
    assert controller.copy_link(title) == "https://mp.weixin.qq.com/s/verified"
    assert len(clicks) == 2


def test_target_tab_requires_unique_distinctive_title():
    tabs = [line("监所家属 - 搜一搜", 10, 90), line("监所家属", 10, 305),
            line("监所家属 - 搜一搜", 10, 500),
            line("亲人刚进监狱那几个月，家属千万别", 10, 710)]
    assert target_article_tab("亲人刚进监狱那几个月，家属千万别做这件事", tabs, 1022) == tabs[-1]
    older_duplicate = line("亲人刚进监狱那几个月，家属千万别", 10, 505)
    assert target_article_tab(
        "亲人刚进监狱那几个月，家属千万别做这件事",
        [older_duplicate, tabs[-1]], 1022,
    ) is None
    assert target_article_tab("完全不同的文章标题", tabs, 1022) is None
    article_tab = OcrLine("亲人刚进监狱那几个月，家属千万别", 680, 10, 120, 20)
    previous = OcrLine("监所家属 - 搜一搜", 487, 10, 100, 20)
    assert article_menu_x(
        "亲人刚进监狱那几个月，家属千万别做这件事", [previous, article_tab], 1022
    ) == pytest.approx(832.47)
    assert article_menu_x("亲人刚进监狱那几个月，家属千万别做这件事", [article_tab], 1022) is None


def test_menu_must_be_inside_observed_article_tab():
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    lines = [line(title[:18], 10, 680), line("其他文章标题", 10, 950)]
    assert menu_matches_article_tab(title, lines, 1100, 835)
    assert not menu_matches_article_tab(title, lines, 1100, 410)
    assert not menu_matches_article_tab(title, lines, 1100, 975)
    assert not menu_matches_article_tab(title, [line(title, 100)], 1100, 835)


def test_menu_validation_ignores_wechat_ai_toolbar_but_not_another_tab():
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    target = line(title[:18], 10, 700)
    ai_control = OcrLine("••向AI", 972, 10, 55, 20)
    assert not menu_matches_article_tab(title, [target, ai_control], 1077, 998)
    assert menu_matches_article_tab(title, [target, ai_control], 1077, 1036)
    other_tab = line("另一篇文章标题", 10, 890)
    assert not menu_matches_article_tab(title, [target, other_tab], 1077, 1036)


def test_verified_menu_selects_only_target_tab_when_other_menus_exist():
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    lines = [line("其他文章标题", 10, 300), line(title[:18], 10, 680)]
    assert verified_article_menu_center(
        title, lines, 1022, [(410, 24), (835, 24)]
    ) == (835, 24)
    assert verified_article_menu_center(
        title, lines, 1022, [(835, 24), (850, 24)]
    ) is None
    assert verified_article_menu_center(
        title, lines, 1022, [(410, 24)]
    ) is None


def test_target_article_tabs_tolerate_ocr_badge_prefix():
    """OCR may read a tab badge count (e.g. '40') as a prefix to the title."""
    title = "苹果史上最贵新品要开卖了，强的离谱！"
    observed = OcrLine(text="40 苹果史上最贵新品要开卖了.（", x=404, y=17, width=208, height=15)
    matches = target_article_tabs(title, [observed], 1077)
    assert len(matches) == 1
    assert verified_article_menu_center(title, [observed], 1077, [(605, 24)]) == (605, 24)
    unrelated = OcrLine(text="旧版苹果史上最贵新品要开卖了.（", x=404, y=17, width=208, height=15)
    assert target_article_tabs(title, [unrelated], 1077) == []


def test_article_browser_selection_prefers_verified_title_over_first_error_window():
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    error = Window(41, "微信 (窗口)", 0, 0, 440, 751, 0, 123, True, 1)
    article = Window(42, "微信 (窗口)", 450, 0, 900, 800, 0, 123, True, 1)
    observed = [
        (error, [line("页面无法访问", 80)]),
        (article, [line(title, 100), line("正文" * 40, 200)]),
    ]
    assert select_article_browser(title, "监所家属", observed) == article
    assert select_article_browser(title, "监所家属", observed[:1]) is None
    assert select_article_browser(title, "监所家属", observed + [observed[1]]) is None


def test_wait_article_uses_verified_second_window_not_first_error_page():
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    error = Window(41, "微信 (窗口)", 0, 0, 440, 751, 0, 123, True, 1)
    article = Window(42, "微信 (窗口)", 450, 0, 900, 800, 0, 123, True, 1)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.browser_windows = lambda: [error, article]
    controller.ocr = lambda window, **kwargs: (
        [line("页面无法访问", 80)] if window.number == 41
        else [line(title, 100), line("正文" * 40, 200)]
    )
    controller.click = lambda *args: pytest.fail("目标文章已显示，不能点击其他窗口")
    assert controller.wait_article(title, timeout=1) == article
    assert controller.active_article_number == article.number


def test_account_tab_allows_ocr_prefix_but_not_search_tab():
    account = line("X 监所家属", 10, 326)
    search = line("六 监所家属-搜一搜", 10, 549)
    assert account_tab_line("监所家属", [search, account]) is account
    assert account_tab_line("监所家属", [search]) is None


def test_wait_article_selects_target_tab_even_if_another_article_is_active(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 1022, 768, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda title: browser
    controller.browser_windows = lambda: [browser]
    clicked = []
    controller.click = lambda x, y: clicked.append((x, y))
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    tab = OcrLine("• 亲人刚进监狱那几个月...", 680, 10, 130, 20)
    other = [tab, line("另一篇完全不同的文章", 100), line("正文" * 50, 210)]
    target = [tab, line(title, 100), line("正文" * 50, 210)]
    controller.ocr = lambda window, **kwargs: target if clicked else other
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.wait_article(title, timeout=1) == browser
    assert clicked == [(tab.cx, tab.cy)]


def test_wait_article_restores_scroll_position_before_title_check(monkeypatch):
    browser = Window(42, "微信 (窗口)", 100, 50, 1022, 768, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda title: browser
    controller.browser_windows = lambda: [browser]
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    tab = OcrLine("• 亲人刚进监狱那几个月...", 680, 10, 130, 20)
    clicks = []
    keys = []
    controller.click = lambda x, y: clicks.append((x, y))
    controller.hotkey = lambda key, flags: keys.append((key, flags))
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    controller.require_foreground = lambda window: None
    controller._scrollbar_thumb = lambda window: pytest.fail("回顶部不应抓滚动条截图")
    controller.ocr = lambda window, **kwargs: (
        [tab, line(title, 90), line("这是正文。" * 25, 170)]
        if keys else [tab, line("文章底部的小程序提示。" * 10, 100)]
    )
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.wait_article(title, timeout=1) == browser
    assert clicks == [(browser.x + tab.cx, browser.y + tab.cy)]
    assert keys == [(126, 1)]


def test_wait_article_accepts_visible_title_on_short_image_article():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda name: browser
    controller.browser_windows = lambda: [browser]
    controller.ocr = lambda window, **kwargs: [line("亲人入狱后最该知道的事情", 90)]
    controller.click = lambda *args: pytest.fail("已显示的标题不应导致重复点击")
    assert controller.wait_article("亲人入狱后最该知道的事情") == browser
    assert controller.active_article_number == browser.number


def test_pinned_article_window_never_falls_back_to_another_same_named_window():
    target = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True, 1)
    other = Window(43, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True, 1)
    controller = MacHumanController.__new__(MacHumanController)
    controller.active_article_number = target.number
    controller.windows = lambda: [other, target]
    assert controller.active_article_window() == target
    controller.windows = lambda: [other]
    with pytest.raises(ArticleNotOpenedError, match="拒绝切换到其他同名窗口"):
        controller.active_article_window()


def test_focus_browser_uses_verified_article_window_even_when_other_is_first():
    target = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True, 1)
    other = Window(43, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True, 1)
    controller = MacHumanController.__new__(MacHumanController)
    controller.active_article_number = target.number
    controller.tabbed_profile_account = "监所家属"
    controller.require_active_session = lambda: None
    controller.windows = lambda: [other, target]
    controller.window = lambda title: other
    raised = []
    controller._raise = lambda window: raised.append(window.number) or True
    controller.require_foreground = lambda window: None
    assert controller.focus_browser() == target
    assert raised == [target.number]


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
        line("亲人入狱后，家属最容易忽略的三件事", 180),
        line("阅读2579 赞10", 205),
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


def test_raise_accepts_only_frontmost_exact_window_with_duplicate_titles(monkeypatch):
    target = Window(7, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    other = Window(8, "微信 (窗口)", 50, 50, 900, 800, 0, 123, True)

    class Running:
        def processIdentifier(self):
            return 123
        def activateWithOptions_(self, options):
            pass

    class AppKit:
        NSApplicationActivateAllWindows = 1
        NSApplicationActivateIgnoringOtherApps = 2
        class NSRunningApplication:
            runningApplicationsWithBundleIdentifier_ = staticmethod(lambda bundle: [Running()])
            runningApplicationWithProcessIdentifier_ = staticmethod(lambda pid: Running())

    class AX:
        kAXWindowsAttribute = "windows"
        kAXTitleAttribute = "title"
        AXUIElementCreateApplication = staticmethod(lambda pid: pid)
        AXUIElementCopyAttributeValue = staticmethod(
            lambda item, attr, unused: (0, [{"title": "微信 (窗口)"}] * 2)
            if attr == "windows" else (0, item["title"])
        )

    controller = MacHumanController.__new__(MacHumanController)
    controller.AX = AX
    controller.AppKit = AppKit
    controller.windows = lambda: [target, other]
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller._raise(target)
    assert not controller._raise(other)


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
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.windows = lambda: [browser]
    controller.activate = lambda: None
    controller._raise = lambda window: True
    controller.ocr = lambda window, **kwargs: [line("监所家属", 90), line("301篇原创内容", 190)]
    controller._close_window = lambda window: pytest.fail("不应关闭公众号标签页")
    assert controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属") == browser
    assert controller.tabbed_profile_account == "监所家属"


def test_tabbed_profile_finds_verified_second_browser_not_first_error_page():
    error = Window(41, "微信 (窗口)", 0, 0, 440, 751, 0, 123, True)
    profile = Window(42, "微信 (窗口)", 500, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.windows = lambda: [error, profile]
    controller.ocr = lambda window: (
        [line("页面无法访问", 100)] if window.number == 41 else
        [line("监所家属", 90), line("301篇原创内容", 190)]
    )
    controller._raise = lambda window: window.number == 42
    controller.click = lambda x, y: pytest.fail("已找到主页时不应点击")
    assert controller._tabbed_profile("监所家属") == profile


def test_tabbed_profile_refuses_two_matching_windows_without_clicking():
    first = Window(41, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    second = Window(42, "微信 (窗口)", 500, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.windows = lambda: [first, second]
    controller.ocr = lambda window: [line("监所家属", 90), line("301篇原创内容", 190)]
    controller._raise = lambda window: pytest.fail("歧义窗口不能前置")
    assert controller._tabbed_profile("监所家属") is None
    assert controller.profile_probe_reason == "multiple_matching_profiles"


def test_open_profile_stops_on_blank_wechat_window_without_clicking():
    main = Window(1, "微信", 0, 0, 880, 640, 0, 42, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller._tabbed_profile = lambda account: None
    controller.window = lambda title: main if title == "微信" else None
    controller.windows = lambda: []
    controller.activate = lambda: None
    controller.wait_window = lambda title: main
    controller._raise = lambda window: True
    controller.ocr = lambda window: []
    controller.click = lambda x, y: pytest.fail("空白窗口不能被点击")
    with pytest.raises(RuntimeError, match="主窗口当前为空白"):
        controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属")


def test_open_profile_stops_when_unshared_main_is_not_verified_frontmost():
    main = Window(1, "微信", 0, 0, 880, 640, 0, 42, True, 0)
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller._tabbed_profile = lambda account: None
    controller.window = lambda title: main if title == "微信" else None
    controller.windows = lambda: []
    controller.activate = lambda: None
    controller.wait_window = lambda title: main
    controller._raise = lambda window: True
    controller.ocr = lambda window: pytest.fail("不可共享窗口不应反复截图")
    controller.click = lambda x, y: pytest.fail("不可共享窗口不能盲点")
    with pytest.raises(RuntimeError, match="未确认位于最前"):
        controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属")


def test_open_profile_uses_visible_ocr_but_never_clicks_without_unique_link():
    main = Window(1, "微信", 0, 0, 880, 640, 0, 42, True, 0)
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller._tabbed_profile = lambda account: None
    controller.window = lambda title: main if title == "微信" else None
    controller.windows = lambda: [main]
    controller.activate = lambda: None
    controller.wait_window = lambda title: main
    controller._raise = lambda window: True
    calls = []
    controller.ocr = lambda window, **kwargs: (calls.append(kwargs) or [line("聊天列表", 100)])
    controller.click = lambda x, y: pytest.fail("未识别链接不能点击")
    with pytest.raises(RuntimeError, match="链接不唯一（0 个）"):
        controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属")
    assert calls == [{"visible": True}]


def test_open_profile_ignores_old_offscreen_article_before_chat_link_lookup():
    browser = Window(42, "微信 (窗口)", 915, 136, 440, 751, 0, 123, False, 1)
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller._tabbed_profile = lambda account: None
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.windows = lambda: [browser]
    controller.ocr = lambda window: pytest.fail("不可见窗口不能截图")
    controller.click = lambda x, y: pytest.fail("不可见窗口不能盲点")
    controller.activate = lambda: None
    controller.wait_window = lambda title: (_ for _ in ()).throw(
        RuntimeError("当前没有可见微信主窗口")
    )
    with pytest.raises(RuntimeError, match="当前没有可见微信主窗口"):
        controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属")


def test_tabbed_article_must_replace_profile_before_capture(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.browser_windows = lambda: [browser]
    controller.ocr = lambda window, **kwargs: [line("监所家属", 90), line("301篇原创内容", 190)]
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", iter([0, 0, 9]).__next__)
    with pytest.raises(RuntimeError, match="未确认目标文章已打开.*profile_still_active"):
        controller.wait_article("第一篇文章")


def test_wait_article_switches_only_to_observed_target_tab(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 1022, 768, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda title: browser
    controller.browser_windows = lambda: [browser]
    clicked = []
    controller.click = lambda x, y: clicked.append((x, y))

    def current_lines(window, **kwargs):
        if clicked:
            return [line("亲人刚进监狱那几个月，家属千万别做这件事", 70),
                    line("这是文章正文。" * 20, 180)]
        return [line("监所家属 - 搜一搜", 10, 90),
                line("监所家属", 10, 305),
                line("监所家属 - 搜一搜", 10, 500),
                line("亲人刚进监狱那几个月，家属千万别", 10, 710),
                line("监所家属", 70), line("301篇原创内容", 100)]

    controller.ocr = current_lines
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", lambda: 0)
    assert controller.wait_article("亲人刚进监狱那几个月，家属千万别做这件事") == browser
    assert clicked == [(800, 20)]


def test_wait_article_recovers_duplicate_title_tabs_by_clicking_label(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 1100, 768, 0, 123)
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda name: browser
    controller.browser_windows = lambda: [browser]
    clicked = []
    controller.click = lambda x, y: clicked.append((x, y))

    def current_lines(window, **kwargs):
        if clicked:
            return [line(title, 70), line("这是文章正文。" * 20, 180)]
        return [
            line("监所家属", 10, 150),
            line(title[:15], 10, 420),
            line(title[:15], 10, 740),
            line("监所家属", 70),
            line("302篇原创内容", 100),
        ]

    controller.ocr = current_lines
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", lambda: 0)
    observed = current_lines(browser)
    assert len(target_article_tabs(title, observed, browser.width)) == 2
    assert target_article_tab(title, observed, browser.width) is None
    assert controller.wait_article(title) == browser
    assert clicked == [(830, 20)]


def test_wait_article_retries_one_ocr_timeout_without_clicking(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 1100, 768, 0, 123)
    title = "亲人刚进监狱那几个月，家属千万别做这件事"
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda name: browser
    controller.browser_windows = lambda: [browser]
    controller.click = lambda x, y: pytest.fail("OCR 超时重试不能点击界面")
    calls = []

    def ocr(window, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RuntimeError("微信窗口 OCR 超时（阶段 recognize_start）")
        return [line(title, 70), line("这是文章正文。" * 20, 180)]

    controller.ocr = ocr
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", lambda: 0)
    assert controller.wait_article(title) == browser
    assert len(calls) == 2
    assert calls[0]["top_fraction"] == 0.3
    assert calls[1]["visible"] is True
    assert calls[1]["region"] == (0, 0, browser.width, 260)


def test_confirm_current_article_scrolls_to_top_without_switching_tabs():
    browser = Window(42, "微信 (窗口)", 0, 0, 1100, 768, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda name: browser
    controller.require_foreground = lambda window: None
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    keys = []
    controller.hotkey = lambda key, flags: keys.append((key, flags))
    controller.click = lambda x, y: pytest.fail("恢复当前文章不能切换标签")
    controller.ocr = lambda window, **kwargs: [
        line("被称为监狱中的监狱，严管队里有多难熬？", 70),
        line("文章正文" * 30, 170),
    ]
    assert controller.confirm_current_article("被称为监狱中的监狱，严管队里有多难熬？") == browser
    assert keys == [(126, 1)]


def test_tabbed_profile_fallback_never_accepts_author_only(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.windows = lambda: [browser]
    controller.ocr = lambda window: [line("监所家属", 720), line("正文内容" * 20, 400)]
    controller._raise = lambda window: True
    clicks = []
    controller.click = lambda x, y: clicks.append((x, y))
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    monkeypatch.setattr("gzh_reader.human_agent.time.monotonic", iter([0, 9]).__next__)
    assert controller._tabbed_profile("监所家属") is None
    assert clicks == []


def test_open_profile_does_not_close_article_when_footer_is_missing():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller._tabbed_profile = lambda account: None
    controller.windows = lambda: [browser]
    controller.ocr = lambda window: [line("阅读 562", 480), line("监所家属", 500)]
    controller._raise = lambda window: True
    controller.require_foreground = lambda window: None
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    controller.hotkey = lambda key, flags: None
    controller._close_window = lambda window: pytest.fail("不能关闭无法确认页尾的文章")
    with pytest.raises(RuntimeError, match="页尾未能唯一确认"):
        controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属")


def test_open_profile_uses_verified_article_footer_link_before_closing(monkeypatch):
    browser = Window(42, "微信 (窗口)", 10, 20, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.windows = lambda: [browser]
    clicked = []
    controller._tabbed_profile = lambda account: browser if clicked else None
    controller.ocr = lambda window: [line("阅读 562", 680), line("监所家属", 720)]
    controller._raise = lambda window: True
    controller.require_foreground = lambda window: None
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    controller.hotkey = lambda key, flags: None
    controller.click = lambda x, y: clicked.append((x, y))
    controller._close_window = lambda window: pytest.fail("不应关闭可读文章窗口")
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.open_profile("https://mp.weixin.qq.com/s/test", "监所家属") == browser
    # 新版微信页尾账号名文字本身不可点，点击的是名称左侧的圆形头像
    assert clicked == [(2, 750)]
    assert controller.tabbed_profile_account == "监所家属"


def test_open_profile_scrolls_only_unique_target_article_to_find_footer(monkeypatch):
    error = Window(41, "微信 (窗口)", 0, 0, 440, 751, 0, 123, True)
    article = Window(42, "微信 (窗口)", 500, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller.windows = lambda: [error, article]
    events = []
    controller._tabbed_profile = lambda account: article if "click" in events else None
    controller._raise = lambda window: window.number == 42
    controller.require_foreground = lambda window: events.append("foreground")
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    controller.hotkey = lambda key, flags: events.append("scroll")
    controller.click = lambda x, y: events.append("click")
    controller._close_window = lambda window: pytest.fail("不能关闭任何窗口")

    def read(window):
        if window.number == 41:
            return [line("页面无法访问", 100)]
        if "scroll" in events:
            return [line("Netskao", 720, 80), line("点赞 2", 740)]
        return [line("Netskao", 210, 80), line("正文" * 25, 400)]

    controller.ocr = read
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.open_profile("https://mp.weixin.qq.com/s/test", "Netskao") == article
    assert events == ["foreground", "scroll", "foreground", "click"]


def test_open_profile_refuses_multiple_target_articles_before_scrolling():
    windows = [Window(n, "微信 (窗口)", n * 10, 0, 900, 800, 0, 123, True)
               for n in (41, 42)]
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller.windows = lambda: windows
    controller._tabbed_profile = lambda account: None
    controller.ocr = lambda window: [line("Netskao", 210), line("正文" * 25, 400)]
    controller._raise = lambda window: pytest.fail("歧义文章不能前置")
    with pytest.raises(RuntimeError, match="无法唯一确认"):
        controller.open_profile("https://mp.weixin.qq.com/s/test", "Netskao")


def test_blank_account_name_accepts_only_unique_verified_profile():
    article = Window(41, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    profile = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller.browser_windows = lambda: [article, profile]
    controller.window = lambda title: None
    controller.ocr = lambda window: (
        [line("Netskao", 60), line("110篇原创内容", 110)]
        if window.number == 42 else [line("文章正文" * 20, 400)]
    )
    controller._raise = lambda window: window.number == 42
    controller._close_window = lambda window: pytest.fail("留空名称不能关闭文章窗口")
    assert controller.open_profile("https://mp.weixin.qq.com/s/test") == profile
    assert controller.tabbed_profile_account == "Netskao"


def test_blank_account_name_refuses_article_only_without_closing():
    article = Window(41, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.require_active_session = lambda: None
    controller.browser_windows = lambda: [article]
    controller.window = lambda title: None
    controller.ocr = lambda window: [line("Netskao", 700), line("文章正文" * 20, 400)]
    controller._raise = lambda window: pytest.fail("不能前置未经核对的窗口")
    controller._close_window = lambda window: pytest.fail("不能关闭文章窗口")
    with pytest.raises(RuntimeError, match="必须有且只有一个"):
        controller.open_profile("https://mp.weixin.qq.com/s/test")


def test_tabbed_close_refuses_when_profile_is_current():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda title: browser if title == "微信 (窗口)" else None
    controller.ocr = lambda window: [line("监所家属", 90), line("301篇原创内容", 190)]
    controller._close_window = lambda window: pytest.fail("不应关闭公众号标签页")
    controller.close_article()


def test_tabbed_close_returns_to_verified_profile_without_command_w():
    article = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    profile = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "Netskao"
    controller.active_article_number = 42
    controller.window = lambda title: article
    controller.browser_by_number = lambda number: article
    controller.ocr = lambda window: [line("文章正文" * 30, 200)]
    controller.require_active_session = lambda: None
    controller._tabbed_profile = lambda account: profile
    controller._close_window = lambda window: pytest.fail("标签页模式不得关闭整个窗口")
    controller.close_article()
    assert controller.active_article_number is None


def test_tabbed_close_recovers_via_pinned_article_footer(monkeypatch):
    article = Window(42, "微信 (窗口)", 10, 20, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "Netskao"
    controller.active_article_number = 42
    controller.require_active_session = lambda: None
    controller.browser_by_number = lambda number: article if number == 42 else None
    clicked = []
    controller._tabbed_profile = lambda account: article if clicked else None
    controller.ocr = lambda window: [line("Netskao", 720, 80), line("正文" * 30, 200)]
    controller._raise = lambda window: True
    controller.require_foreground = lambda window: None
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    controller.hotkey = lambda key, flags: None
    controller.click = lambda x, y: clicked.append((x, y))
    controller._close_window = lambda window: pytest.fail("不得关闭整个文章窗口")
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    controller.close_article()
    assert clicked == [(62, 750)]
    assert controller.active_article_number is None


def test_tabbed_close_does_not_recover_from_unpinned_window():
    article = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "Netskao"
    controller.require_active_session = lambda: None
    controller.window = lambda title: article
    controller.ocr = lambda window: [line("文章正文" * 30, 200)]
    controller._tabbed_profile = lambda account: None
    controller._raise = lambda window: pytest.fail("未锁定文章不得前置")
    with pytest.raises(RuntimeError, match="已验证文章窗口"):
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


def test_wait_article_cancels_centered_miniprogram_prompt_and_stops(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.tabbed_profile_account = "监所家属"
    controller.window = lambda title: browser
    controller.browser_windows = lambda: [browser]
    controller.ocr = lambda window, **kwargs: (
        [line("旧文章", 80)] if "top_fraction" in kwargs
        else [line("即将打开小程序", 400)]
    )
    controller.dismiss_miniprogram_prompt = lambda: True
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    with pytest.raises(MiniProgramInterceptedError, match="已取消并停止"):
        controller.wait_article("目标文章", timeout=1)


def test_copy_link_stops_before_clicking_when_miniprogram_prompt_was_open():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.window = lambda title: browser
    controller.require_foreground = lambda window: None
    controller.dismiss_miniprogram_prompt = lambda: True
    controller.hotkey = lambda key, flags: pytest.fail("不得继续操作弹窗后的页面")
    with pytest.raises(MiniProgramInterceptedError, match="拒绝继续复制链接"):
        controller.copy_link("目标文章")


def test_copy_body_focuses_verified_paragraph_and_rejects_stale_link(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.focus_browser = lambda: browser
    controller.dismiss_miniprogram_prompt = lambda: False
    title = "监狱里的生活是什么样子"
    controller.ocr = lambda window, **kwargs: [
        line(title, 100, 300),
        line("原创 作者 监所家属 2026年9月17日", 135, 300),
        line("这是文章正文的第一段，可以确认点击目标在文章内部。", 200, 300),
    ]
    clicks = []
    controller.click = lambda x, y: clicks.append((x, y))
    controller.hotkey = lambda key, flags: None
    controller.clipboard = lambda: title + "\n" + "正文" * 100
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    class Board:
        def clearContents(self):
            pass
    controller.AppKit = type("A", (), {"NSPasteboard": type("P", (), {"generalPasteboard": lambda: Board()})})
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.copy_page_text(title) == title + "\n" + "正文" * 100
    assert clicks == [(390, 210)]
    controller.clipboard = lambda: "https://mp.weixin.qq.com/s/old"
    controller.ocr_page_text = lambda *args: (_ for _ in ()).throw(
        RuntimeError("OCR 未确认页尾；正文未入库")
    )
    with pytest.raises(RuntimeError, match="OCR 未确认页尾"):
        controller.copy_page_text(title)
    controller.ocr_page_text = lambda *args: title + "\n" + "逐屏识别的文章正文。" * 20
    assert "逐屏识别" in controller.copy_page_text(title)
    assert controller.last_body_source == "visible_ocr"


def test_copy_body_refuses_to_click_when_paragraph_not_visible():
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.focus_browser = lambda: browser
    controller.dismiss_miniprogram_prompt = lambda: False
    controller.ocr = lambda window, **kwargs: [line("监狱里的生活是什么样子", 100, 300)]
    controller.click = lambda x, y: pytest.fail("没有正文时不应点击")
    controller.ocr_page_text = lambda *args: "监狱里的生活是什么样子\n" + "后续逐屏识别正文。" * 20
    assert "后续逐屏识别" in controller.copy_page_text("监狱里的生活是什么样子")


def test_ocr_body_fallback_needs_repeated_footer(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    title = "苹果史上最贵新品要开卖了，强的离谱！"
    first = [line(title, 100), line("这是一段足够长的文章正文，用于核对逐屏 OCR 是否保存了可见段落。", 180)]
    last = [line("第二屏正文继续说明该产品的发布信息与配置变化。" * 5, 150),
            line("阅读 101", 700)]
    pages = iter([first, last, last, last])
    controller = MacHumanController.__new__(MacHumanController)
    controller.browser_by_number = lambda number: browser
    controller.require_foreground = lambda window: None
    controller.dismiss_miniprogram_prompt = lambda: False
    controller.ocr = lambda window: next(pages)
    controller.hotkey = lambda *args: None
    controller.wheel = lambda *args: None
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    body = controller.ocr_page_text(title, browser)
    assert body.count("阅读 101") == 1
    assert title in body


def test_ocr_body_fallback_refuses_repeated_non_footer(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123, True)
    title = "目标文章标题"
    page = [line(title, 100), line("可见正文" * 30, 180)]
    controller = MacHumanController.__new__(MacHumanController)
    controller.browser_by_number = lambda number: browser
    controller.require_foreground = lambda window: None
    controller.dismiss_miniprogram_prompt = lambda: False
    controller.ocr = lambda window: page
    controller.hotkey = lambda *args: None
    controller.wheel = lambda *args: None
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="连续重复"):
        controller.ocr_page_text(title, browser)


def test_page_bottom_accepts_short_footer_after_window_identity_check(monkeypatch):
    browser = Window(42, "微信 (窗口)", 0, 0, 900, 800, 0, 123)
    controller = MacHumanController.__new__(MacHumanController)
    controller.focus_browser = lambda: browser
    controller.window = lambda title: browser
    controller.hotkey = lambda key, flags: None
    controller.ocr = lambda window: [line("监所家属", 700), line("阅读 5910", 680)]
    controller.Quartz = type("Q", (), {"kCGEventFlagMaskCommand": 1})
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    assert controller.page_bottom() == "监所家属\n阅读 5910"


def test_capture_records_action_sequence_without_article_text(monkeypatch):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)

    class FakeController:
        def click(self, x, y):
            pass

        def wait_window(self, title, timeout):
            return profile

        def copy_link(self, title):
            return "https://mp.weixin.qq.com/s/test"

        def copy_page_text(self, title):
            return title + "\n" + "文章正文" * 30

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


def test_capture_already_open_never_clicks_or_closes_tab(monkeypatch):
    browser = Window(1, "微信 (窗口)", 0, 0, 900, 800, 0, 42)

    class FakeController:
        def click(self, x, y):
            pytest.fail("恢复当前文章不能点击列表卡片")
        def wait_article(self, title, timeout):
            return browser
        def copy_link(self, title):
            return "https://mp.weixin.qq.com/s/test"
        def copy_page_text(self, title):
            return title + "\n" + "文章正文" * 40
        def page_bottom(self):
            return "阅读 20\n监所家属"
        def close_article(self):
            pytest.fail("恢复当前文章不能关闭用户标签")
        def focus_profile(self):
            pytest.fail("恢复当前文章不能切回主页")

    collector = MacHumanAccountCollector(controller=FakeController())
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    capture = collector._capture_card(
        browser, ProfileCard("测试文章标题", None, None, 0, 0), "监所家属",
        already_open=True, close_after=False,
    )
    assert capture.url == "https://mp.weixin.qq.com/s/test"
    assert capture.read_num == 20


def test_recover_open_article_saves_verified_capture(tmp_path: Path):
    browser = Window(1, "微信 (窗口)", 0, 0, 900, 800, 0, 42)

    class Controller:
        def window(self, title):
            return browser

    collector = MacHumanAccountCollector(controller=Controller())
    calls = []

    def capture(window, card, account_name, **kwargs):
        calls.append(kwargs)
        return HumanCapture(
            "https://mp.weixin.qq.com/s/recovered", card.title,
            card.title + "\n" + "文章正文" * 40,
            20, None, None, None,
        )

    collector._capture_card = capture
    audit_dir = tmp_path / "监所家属" / "audit"
    audit_dir.mkdir(parents=True)
    fingerprint = hashlib.sha256("测试文章标题".encode()).hexdigest()
    (audit_dir / "human-agent-last-error.json").write_text(json.dumps({
        "account": "监所家属", "title": "测试文章标题",
        "viewport_fingerprint": fingerprint, "card_index": 0,
        "card_count": 1,
    }), encoding="utf-8")
    root, saved = collector.recover_open_article(
        title="测试文章标题", account_name="监所家属",
        seed_url="https://mp.weixin.qq.com/s/seed", output=tmp_path,
    )
    assert saved
    assert calls == [{"already_open": True, "close_after": False}]
    assert Store(root / "database" / "archive.sqlite3").rows(
        "SELECT COUNT(*) AS n FROM articles"
    )[0]["n"] == 1
    assert (root / "audit" / "coverage.json").exists()
    hints = json.loads((root / "audit" / "human-agent-card-hints.json").read_text())
    assert hints["cards"][fingerprint + "|0"] == "https://mp.weixin.qq.com/s/recovered"


def test_link_receipt_survives_body_failure_and_resume_skips_menu(monkeypatch):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)
    calls = []

    class FakeController:
        def click(self, x, y):
            pass
        def wait_window(self, title, timeout):
            return profile
        def copy_link(self, title):
            calls.append("copy_link")
            return "https://mp.weixin.qq.com/s/test"
        def copy_page_text(self, title):
            calls.append("copy_body")
            if calls.count("copy_body") == 1:
                raise RuntimeError("正文暂时不可复制")
            return title + "\n" + "文章正文" * 30
        def page_bottom(self):
            return "阅读 20\n监所家属"
        def close_article(self):
            pass
        def focus_profile(self):
            return profile

    collector = MacHumanAccountCollector(controller=FakeController())
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    saved = []
    card = ProfileCard("标题", 20, 1, 100, 200)
    with pytest.raises(RuntimeError, match="正文暂时不可复制"):
        collector._capture_card(profile, card, "监所家属", on_link=saved.append)
    assert saved == ["https://mp.weixin.qq.com/s/test"]
    result = collector._capture_card(
        profile, card, "监所家属", known_url=saved[0],
    )
    assert result.url == saved[0]
    assert calls.count("copy_link") == 1


def test_article_title_mismatch_leaves_unconfirmed_tab_open(monkeypatch):
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
    assert actions == ["click", "title_mismatch", "focus_profile"]


def test_cleanup_error_does_not_hide_article_open_failure(monkeypatch):
    profile = Window(1, "微信 (窗口)", 0, 0, 900, 800, 0, 42)

    class FakeController:
        def click(self, x, y):
            pass

        def wait_article(self, title, timeout):
            from gzh_reader.human_agent import ArticleNotOpenedError
            raise ArticleNotOpenedError("文章未打开")

        def close_article(self):
            raise RuntimeError("清理失败")

        def focus_profile(self):
            return profile

    collector = MacHumanAccountCollector(controller=FakeController())
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda seconds: None)
    with pytest.raises(RuntimeError, match="文章未打开"):
        collector._capture_card(profile, ProfileCard("卡片", 20, 1, 100, 200), "监所家属")


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
                "INSERT INTO content_snapshots(article_key,checksum,markdown_path,status) VALUES(?,?,?,'ok')",
                (biz, biz, f"raw/{biz}.md"),
            )
            db.execute(
                "INSERT INTO metric_snapshots(article_key,readNum,likeNum,status) VALUES(?,10,0,'ok')",
                (biz,),
            )
            (tmp_path / "raw").mkdir(exist_ok=True)
            (tmp_path / "raw" / f"{biz}.md").write_text("这是有效正文。" * 30, encoding="utf-8")
    rows = completed_human_rows(store, "监所家属", tmp_path)
    assert {row["title"] for row in rows} == {"human-old", "human:new"}


def test_resume_does_not_skip_url_only_body_marked_ok(tmp_path):
    store = Store(tmp_path / "archive.sqlite3")
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw" / "link.md").write_text(
        "https://mp.weixin.qq.com/s/one", encoding="utf-8"
    )
    with store.connect() as db:
        db.execute("INSERT INTO accounts(biz,name,source,status) VALUES('human:one','监所家属','mac_human_agent','ok')")
        db.execute("INSERT INTO articles(stable_key,biz,url,title,status) VALUES('one','human:one','https://mp.weixin.qq.com/s/one','文章','ok')")
        db.execute("INSERT INTO content_snapshots(article_key,markdown_path,status) VALUES('one','raw/link.md','ok')")
        db.execute("INSERT INTO metric_snapshots(article_key,readNum,status) VALUES('one',1,'ok')")
    assert completed_human_rows(store, "监所家属", tmp_path) == []


def test_small_batches_resume_without_reopening_known_cards(tmp_path: Path):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)
    lines = [
        line("监所家属", 30), line("301篇原创内容", 60),
        line("第一篇文章", 180), line("阅读101 赞1", 205),
        line("第二篇文章", 350), line("阅读202 赞2", 395),
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

    def capture(window, card, account_name, **kwargs):
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
    first_store = Store(workspace / "database" / "archive.sqlite3")
    assert first_store.rows("SELECT status FROM metric_snapshots ORDER BY id DESC LIMIT 1")[0]["status"] == "missing"
    layers = {row["layer"] for row in first_store.rows("SELECT layer FROM missing_records")}
    assert {"metrics.shareNum", "metrics.commentNum"} <= layers
    assert "metrics.readNum" not in layers
    collector.collect(url, tmp_path, account_name="监所家属", max_new_articles=1)
    assert opened == ["第一篇文章", "第二篇文章"]
    assert len(Store(workspace / "database" / "archive.sqlite3").rows("SELECT url FROM articles")) == 2


def test_reobserves_card_coordinates_after_each_article(tmp_path: Path, monkeypatch):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)
    initial = [
        line("监所家属", 30), line("301篇原创内容", 60),
        line("第一篇文章", 180), line("阅读101 赞1", 205),
        line("第二篇文章", 350), line("阅读202 赞2", 395),
    ]
    shifted = [
        line("监所家属", 30), line("301篇原创内容", 60),
        line("第二篇文章", 180), line("阅读202 赞2", 205),
    ]

    class Controller:
        shifted = False

        def open_profile(self, url, account_name):
            return profile
        def scroll_to_top(self):
            return profile
        def focus_profile(self):
            return profile
        def ocr(self, window):
            return shifted if self.shifted else initial
        def scroll(self):
            return False

    controller = Controller()
    collector = MacHumanAccountCollector(controller=controller)
    opened = []
    monkeypatch.setattr("gzh_reader.human_agent.time.sleep", lambda _: None)

    def capture(window, card, account_name, **kwargs):
        opened.append((card.title, card.click_y))
        controller.shifted = True
        return HumanCapture(
            url="https://mp.weixin.qq.com/s/" + str(len(opened)),
            title=card.title, body="这是公开文章正文。" * 30,
            read_num=card.read_num, like_num=card.like_num,
            share_num=None, comment_num=None,
        )

    collector._capture_card = capture
    collector.collect(
        "https://mp.weixin.qq.com/s/example", tmp_path,
        account_name="监所家属", max_new_articles=2,
    )
    assert opened == [("第一篇文章", 190), ("第二篇文章", 190)]


def test_empty_list_rechecks_profile_before_scrolling(tmp_path: Path):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)
    header = [line("监所家属", 30), line("301篇原创内容", 60)]

    class Controller:
        calls = 0
        scrolled = False

        def open_profile(self, url, account_name):
            return profile
        def scroll_to_top(self):
            return profile
        def focus_profile(self):
            return profile
        def ocr(self, window):
            self.calls += 1
            return [] if self.calls >= 3 else header
        def scroll(self):
            self.scrolled = True
            return True

    controller = Controller()
    collector = MacHumanAccountCollector(controller=controller)
    workspace = collector.collect(
        "https://mp.weixin.qq.com/s/example", tmp_path,
        account_name="监所家属", max_new_articles=1,
    )
    progress = json.loads((workspace / "audit" / "human-agent-progress.json").read_text(encoding="utf-8"))
    assert progress["stop_reason"] == "profile_lost_before_scroll"
    assert progress["attempts"] == 0
    assert controller.scrolled is False


def test_saved_url_with_different_title_stops_batch(tmp_path: Path):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)

    class Controller:
        title = "第一篇文章标题"

        def open_profile(self, url, account_name):
            return profile
        def scroll_to_top(self):
            return profile
        def focus_profile(self):
            return profile
        def ocr(self, window):
            return [line("监所家属", 30), line("301篇原创内容", 60),
                    line(self.title, 180), line("阅读101 赞1", 205)]
        def scroll(self):
            return False

    controller = Controller()
    collector = MacHumanAccountCollector(controller=controller)

    def capture(window, card, account_name, **kwargs):
        return HumanCapture(
            url="https://mp.weixin.qq.com/s/same-url", title=card.title,
            body=card.title + "\n" + "这是公开文章正文。" * 30,
            read_num=101, like_num=1, share_num=None, comment_num=None,
        )

    collector._capture_card = capture
    seed = "https://mp.weixin.qq.com/s/example"
    workspace = collector.collect(seed, tmp_path, account_name="监所家属", max_new_articles=1)
    controller.title = "完全不同的第二篇文章"
    collector.collect(seed, tmp_path, account_name="监所家属", max_new_articles=1)
    progress = json.loads((workspace / "audit" / "human-agent-progress.json").read_text(encoding="utf-8"))
    assert progress["stop_reason"] == "link_title_conflict"
    assert progress["new_in_run"] == 0
    assert len(Store(workspace / "database" / "archive.sqlite3").rows("SELECT url FROM articles")) == 1


def test_stale_viewport_link_hint_is_not_reused_for_new_card(tmp_path: Path):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)

    class Controller:
        title = "第一篇文章标题"
        def open_profile(self, url, account_name):
            return profile
        def scroll_to_top(self):
            return profile
        def focus_profile(self):
            return profile
        def ocr(self, window):
            return [line("监所家属", 30), line("301篇原创内容", 60),
                    line(self.title, 180), line("阅读101 赞1", 205)]
        def scroll(self):
            return False

    controller = Controller()
    collector = MacHumanAccountCollector(controller=controller)
    seen_known_urls = []

    def capture(window, card, account_name, *, known_url=None, **kwargs):
        seen_known_urls.append(known_url)
        return HumanCapture(
            url="https://mp.weixin.qq.com/s/" + str(len(seen_known_urls)),
            title=card.title, body=card.title + "\n" + "这是公开文章正文。" * 30,
            read_num=101, like_num=1, share_num=None, comment_num=None,
        )

    collector._capture_card = capture
    seed = "https://mp.weixin.qq.com/s/example"
    workspace = collector.collect(seed, tmp_path, account_name="监所家属", max_new_articles=1)
    controller.title = "完全不同的第二篇文章"
    fingerprint = hashlib.sha256(controller.title.encode()).hexdigest()
    hints_path = workspace / "audit" / "human-agent-card-hints.json"
    hints = json.loads(hints_path.read_text(encoding="utf-8"))
    hints["cards"][fingerprint + "|0"] = "https://mp.weixin.qq.com/s/1"
    hints_path.write_text(json.dumps(hints, ensure_ascii=False), encoding="utf-8")
    collector.collect(seed, tmp_path, account_name="监所家属", max_new_articles=1)
    assert seen_known_urls == [None, None]
    assert len(Store(workspace / "database" / "archive.sqlite3").rows("SELECT url FROM articles")) == 2


def test_agent_order_uses_original_card_ids_for_capture(tmp_path: Path):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)
    lines = [line("监所家属", 30), line("301篇原创内容", 60),
             line("第一篇文章", 180), line("阅读101 赞1", 205),
             line("第二篇文章", 350), line("阅读202 赞2", 395)]

    class Controller:
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

    class Agent:
        calls = 1
        def plan_cards(self, cards):
            return [1, 0]

    collector = MacHumanAccountCollector(controller=Controller(), agent=Agent())
    opened = []

    def capture(window, card, account_name, **kwargs):
        opened.append(card.title)
        return HumanCapture(
            url="https://mp.weixin.qq.com/s/second", title=card.title,
            body="这是公开文章正文。" * 30,
            read_num=card.read_num, like_num=card.like_num,
            share_num=None, comment_num=None,
        )

    collector._capture_card = capture
    collector.collect(
        "https://mp.weixin.qq.com/s/example", tmp_path,
        account_name="监所家属", max_new_articles=1,
    )
    assert opened == ["第二篇文章"]


def test_any_article_step_failure_stops_before_next_card(tmp_path: Path):
    profile = Window(1, "公众号", 0, 0, 400, 600, 0, 42)
    lines = [line("监所家属", 30), line("301篇原创内容", 60),
             line("第一篇文章", 180), line("阅读101 赞1", 205),
             line("第二篇文章", 350), line("阅读202 赞2", 395)]

    extra_closes = []

    class FakeController:
        def open_profile(self, url, account_name):
            return profile
        def scroll_to_top(self):
            return profile
        def focus_profile(self):
            return profile
        def ocr(self, window):
            return lines
        def close_article(self):
            extra_closes.append(True)
        def scroll(self):
            return False

    collector = MacHumanAccountCollector(controller=FakeController())
    opened = []

    def fail_capture(window, card, account_name, **kwargs):
        opened.append(card.title)
        error = RuntimeError("页尾不可读")
        error.capture_step = "page_bottom"
        raise error

    collector._capture_card = fail_capture
    workspace = collector.collect(
        "https://mp.weixin.qq.com/s/example", tmp_path,
        account_name="监所家属", max_new_articles=5,
    )
    progress = json.loads((workspace / "audit" / "human-agent-progress.json").read_text(encoding="utf-8"))
    assert opened == ["第一篇文章"]
    assert progress["stop_reason"] == "page_bottom_failed"
    assert progress["attempts"] == 1
    assert extra_closes == []
