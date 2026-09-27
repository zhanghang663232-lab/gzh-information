"""Narrow, model-independent decisions for the local Mac UI executor."""

from __future__ import annotations

from dataclasses import dataclass


TOOL_NAMES = frozenset({
    "open_article", "read_article", "read_visible_interactions",
    "return_to_profile", "scroll_list", "pause",
})


@dataclass(frozen=True, slots=True)
class ToolAction:
    name: str
    observation_id: str
    candidate_id: str | None = None


class ToolSchemaError(RuntimeError):
    pass


def validate_action(answer: object, observation: dict) -> ToolAction:
    """Accept only actions and candidate IDs supplied by the current observer."""
    if not isinstance(answer, dict):
        raise ToolSchemaError("模型没有返回 JSON 对象")
    if set(answer) - {"action", "observation_id", "candidate_id"}:
        raise ToolSchemaError("模型返回了未允许的参数")
    name = answer.get("action")
    allowed = observation.get("allowed_actions", [])
    if not isinstance(name, str) or name not in TOOL_NAMES or name not in allowed:
        raise ToolSchemaError("模型选择了当前状态不允许的动作")
    obs_id = answer.get("observation_id")
    if not isinstance(obs_id, str) or obs_id != observation.get("observation_id"):
        raise ToolSchemaError("模型使用了过期的页面观察")
    candidate_id = answer.get("candidate_id")
    if name == "open_article":
        valid_ids = {
            item.get("id") for item in observation.get("candidates", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if not isinstance(candidate_id, str) or candidate_id not in valid_ids:
            raise ToolSchemaError("模型返回了不存在的当前页面文章编号")
    elif candidate_id is not None:
        raise ToolSchemaError("当前动作不接受文章编号")
    return ToolAction(name, obs_id, candidate_id)
