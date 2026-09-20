from gzh_reader.human_agent import (
    OcrLine,
    account_name_from_profile,
    parse_count,
    parse_profile_cards,
    parse_share_num,
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
