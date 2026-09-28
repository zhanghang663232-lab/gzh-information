import pytest

from gzh_reader.agent_runtime import AgentDecisionError, CardPlanAgent
from gzh_reader.human_agent import ProfileCard


CARDS = [
    ProfileCard("第一篇", 10, 0, 100, 200),
    ProfileCard("第二篇", 20, 1, 300, 400),
]


def test_agent_can_reorder_only_observed_cards_without_coordinates():
    class Model:
        def complete_json(self, system, user, *, max_tokens):
            assert "click_x" not in user and "click_y" not in user
            assert "第一篇" in user and "第二篇" in user
            return {"action": "open_in_order", "card_ids": [1, 0]}, {}

    assert CardPlanAgent(Model()).plan_cards(CARDS) == [1, 0]


@pytest.mark.parametrize("answer", [
    {"action": "open_in_order", "card_ids": [0, 0]},
    {"action": "open_in_order", "card_ids": [0, 9]},
    {"action": "click", "x": 123, "y": 456},
    {"action": "open_in_order", "card_ids": [True, 1]},
])
def test_agent_rejects_invented_or_duplicate_actions(answer):
    class Model:
        def complete_json(self, system, user, *, max_tokens):
            return answer, {}

    with pytest.raises(AgentDecisionError):
        CardPlanAgent(Model()).plan_cards(CARDS)


def test_agent_pause_and_call_budget():
    class Model:
        def complete_json(self, system, user, *, max_tokens):
            return {"action": "pause", "card_ids": []}, {}

    agent = CardPlanAgent(Model(), max_calls=1)
    assert agent.plan_cards(CARDS) is None
    assert agent.plan_cards(CARDS) == [0, 1]
    assert agent.calls == 1
