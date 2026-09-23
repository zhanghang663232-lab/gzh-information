from gzh_reader.human_agent import (
    OcrLine,
    account_name_from_profile,
    article_matches_account,
    meaningful_body,
    parse_count,
    parse_profile_cards,
    parse_share_num,
    profile_identity_conflicts,
    profile_matches_account,
)


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
