from dataclasses import dataclass

import pytest

from gzh_reader.candidate_queue import prioritize_candidates


@dataclass
class Card:
    title: str
    read_num: int = 0
    like_num: int = 0


def test_changed_metrics_do_not_make_saved_title_first_priority():
    cards = [Card("旧文章", 150, 12), Card("新文章", 20, 1)]
    completed = [{"title": "旧文章", "readNum": 100, "likeNum": 10}]

    result = prioritize_candidates(cards, completed)

    assert result.order == (1, 0)
    assert result.deferred_duplicate == frozenset({0})
    assert len(result.order) == len(cards)


def test_model_order_is_preserved_within_priority_groups():
    cards = [Card("旧甲"), Card("新甲"), Card("旧乙"), Card("新乙")]

    result = prioritize_candidates(
        cards, [{"title": "旧甲"}, {"title": "旧乙"}],
        preferred_order=[2, 3, 0, 1],
    )

    assert result.order == (3, 1, 2, 0)
    assert result.deferred_duplicate == frozenset({0, 2})


def test_same_title_reposts_and_same_title_new_cards_are_not_dropped():
    cards = [Card("可能重发"), Card("新文章"), Card("可能重发"), Card("新文章")]

    result = prioritize_candidates(cards, [{"title": "可能重发"}])

    assert result.order == (1, 3, 0, 2)
    assert result.deferred_duplicate == frozenset({0, 2})


def test_whitespace_is_only_a_scheduling_hint_and_blank_titles_are_not_duplicates():
    cards = [Card("  已保存 文章\n"), Card(""), Card("相似文章？")]

    result = prioritize_candidates(
        cards, [{"title": "已保存文章"}, {"title": "  "},
                {"title": None}, {}, {"title": "相似文章！"}],
    )

    assert result.order == (1, 2, 0)
    assert result.deferred_duplicate == frozenset({0})


def test_no_saved_titles_and_all_saved_titles_keep_original_order():
    cards = [Card("甲"), Card("乙")]
    assert prioritize_candidates(cards, []).order == (0, 1)
    assert prioritize_candidates(cards, [{"title": "甲"}, {"title": "乙"}]).order == (0, 1)
    assert prioritize_candidates([], []).order == ()


@pytest.mark.parametrize("bad_order", [
    [0], [0, 0], [0, 2], [-1, 0], [True, 0], [0.0, 1], ["0", 1], [1, 0, 2],
])
def test_invalid_model_indices_are_rejected(bad_order):
    with pytest.raises(ValueError, match="整数索引"):
        prioritize_candidates([Card("甲"), Card("乙")], [], preferred_order=bad_order)


def test_inputs_are_not_mutated():
    cards = [Card("旧文章"), Card("新文章")]
    completed = [{"title": "旧文章"}]
    order = [0, 1]

    prioritize_candidates(cards, completed, preferred_order=order)

    assert [card.title for card in cards] == ["旧文章", "新文章"]
    assert completed == [{"title": "旧文章"}]
    assert order == [0, 1]
