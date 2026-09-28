"""Bounded model decisions over already observed public article cards."""

from __future__ import annotations

import json
from typing import Protocol

from .human_agent import ProfileCard
from .tool_schema import ToolAction, ToolSchemaError, validate_action


class JsonDecisionClient(Protocol):
    def complete_json(self, system: str, user: str, *, max_tokens: int = 300) -> tuple[dict, dict]: ...


class AgentDecisionError(RuntimeError):
    pass


class CardPlanAgent:
    """A model may reorder visible cards or pause; it never supplies coordinates."""

    def __init__(self, client: JsonDecisionClient, *, max_calls: int = 30):
        self.client = client
        self.max_calls = max_calls
        self.calls = 0

    @staticmethod
    def _validate(answer: dict, count: int) -> list[int] | None:
        if answer.get("action") == "pause" and answer.get("card_ids") == []:
            return None
        if answer.get("action") != "open_in_order":
            raise AgentDecisionError("模型选择了未允许的动作，已停止")
        indices = answer.get("card_ids")
        if not isinstance(indices, list) or len(indices) != count or any(
            not isinstance(value, int) or isinstance(value, bool) for value in indices
        ) or set(indices) != set(range(count)):
            raise AgentDecisionError("模型返回的文章编号不完整或含重复项，已停止")
        return indices

    def plan_cards(self, cards: list[ProfileCard]) -> list[int] | None:
        if not cards:
            return []
        if len(cards) > 20 or self.calls >= self.max_calls:
            # Model calls are optional ordering advice, not the acquisition
            # engine. Exhausting the cost budget must not impose an unrelated
            # whole-account article limit; preserve the observed UI order.
            return list(range(len(cards)))
        public_cards = [
            {"id": index, "title": card.title[:120],
             "readNum": card.read_num, "likeNum": card.like_num}
            for index, card in enumerate(cards)
        ]
        self.calls += 1
        try:
            answer, _ = self.client.complete_json(
                "你只对已识别的公众号文章卡片排序。输出 JSON："
                '{"action":"open_in_order","card_ids":[0,1]}，'
                '或 {"action":"pause","card_ids":[]}。'
                "必须包含每个原始编号恰好一次。不能编造文章或坐标。",
                json.dumps(public_cards, ensure_ascii=False),
                max_tokens=160,
            )
        except RuntimeError as exc:
            raise AgentDecisionError(f"模型决策失败：{type(exc).__name__}") from None
        return self._validate(answer, len(cards))


class ToolDecisionAgent:
    """One small decision at a time; the executor alone controls the Mac."""

    def __init__(self, client: JsonDecisionClient, *, max_calls: int = 50):
        self.client = client
        self.max_calls = max_calls
        self.calls = 0

    def decide(self, observation: dict) -> ToolAction:
        if self.calls >= self.max_calls:
            raise AgentDecisionError("模型调用预算已用完，已保留断点")
        if not isinstance(observation.get("observation_id"), str):
            raise AgentDecisionError("观察缺少页面编号，已停止")
        allowed = observation.get("allowed_actions")
        if not isinstance(allowed, list) or not allowed:
            raise AgentDecisionError("当前页面没有可供模型执行的动作")
        candidates = observation.get("candidates", [])
        if not isinstance(candidates, list) or len(candidates) > 20:
            raise AgentDecisionError("候选文章数量异常，已停止模型决策")
        # Send only public navigation metadata supplied by the observer.
        packet = {
            "observation_id": observation["observation_id"],
            "state": observation.get("state"),
            "allowed_actions": allowed,
            "candidates": [
                {"id": item.get("id"), "title": str(item.get("title", ""))[:120]}
                for item in candidates if isinstance(item, dict)
            ],
        }
        prompt = json.dumps(packet, ensure_ascii=False)
        error_hint = ""
        for attempt in range(2):
            if self.calls >= self.max_calls:
                break
            self.calls += 1
            try:
                answer, _ = self.client.complete_json(
                    "只能从 allowed_actions 选择一个动作。输出 JSON 对象，字段为 "
                    "action、observation_id，打开文章时另加 candidate_id。"
                    "不得输出坐标、命令或未给出的文章编号。" + error_hint,
                    prompt, max_tokens=140,
                )
                return validate_action(answer, packet)
            except (RuntimeError, ToolSchemaError) as exc:
                if attempt == 0:
                    error_hint = "上次输出无效，请按原格式重新选择一次。"
                    continue
                raise AgentDecisionError("模型连续两次返回无效动作，已保留断点") from None
        raise AgentDecisionError("模型调用预算已用完，已保留断点")
