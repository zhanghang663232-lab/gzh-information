import pytest

from gzh_reader.agent_runtime import AgentDecisionError, ToolDecisionAgent
from gzh_reader.tool_schema import ToolSchemaError, validate_action


OBSERVATION = {
    "observation_id": "obs-17", "state": "profile_ready",
    "allowed_actions": ["open_article", "scroll_list", "pause"],
    "candidates": [{"id": "card-4", "title": "示例标题"}],
}


def test_tool_schema_rejects_stale_candidate_and_coordinates():
    assert validate_action({"action": "open_article", "observation_id": "obs-17",
                            "candidate_id": "card-4"}, OBSERVATION).candidate_id == "card-4"
    for bad in (
        {"action": "open_article", "observation_id": "obs-16", "candidate_id": "card-4"},
        {"action": "open_article", "observation_id": "obs-17", "candidate_id": "card-5"},
        {"action": "open_article", "observation_id": "obs-17", "candidate_id": "card-4", "x": 300},
        {"action": "read_article", "observation_id": "obs-17"},
    ):
        with pytest.raises(ToolSchemaError):
            validate_action(bad, OBSERVATION)


def test_small_model_gets_one_retry_and_no_body_or_coordinates():
    class Client:
        def __init__(self):
            self.calls = []

        def complete_json(self, system, user, *, max_tokens):
            self.calls.append((system, user, max_tokens))
            if len(self.calls) == 1:
                return {"action": "open_article", "observation_id": "obs-17",
                        "candidate_id": "made-up"}, {}
            return {"action": "open_article", "observation_id": "obs-17",
                    "candidate_id": "card-4"}, {}

    client = Client()
    result = ToolDecisionAgent(client).decide({**OBSERVATION, "body": "私人全文"})
    assert result.candidate_id == "card-4"
    assert len(client.calls) == 2
    assert "私人全文" not in str(client.calls)


def test_small_model_invalid_twice_pauses_without_action():
    class Client:
        def complete_json(self, system, user, *, max_tokens):
            return {"action": "click", "observation_id": "obs-17", "x": 300}, {}

    with pytest.raises(AgentDecisionError, match="连续两次"):
        ToolDecisionAgent(Client()).decide(OBSERVATION)
