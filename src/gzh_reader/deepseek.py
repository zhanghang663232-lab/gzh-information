"""Opt-in, bounded DeepSeek review of ambiguous public account-list OCR."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .human_agent import OcrLine, ProfileCard


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    URL = "https://api.deepseek.com/chat/completions"
    MODEL = "deepseek-flash"

    def __init__(self, api_key: str | None = None, *, timeout: int = 20):
        self._key = (api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")).strip()
        if not self._key:
            raise DeepSeekError("未配置 DEEPSEEK_API_KEY；不要把密钥发到聊天或写入仓库")
        self.timeout = timeout

    def complete_json(self, system: str, user: str, *, max_tokens: int = 300) -> tuple[dict, dict]:
        payload = {
            "model": self.MODEL,
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        request = urllib.request.Request(
            self.URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
        except urllib.error.HTTPError as exc:
            raise DeepSeekError(f"DeepSeek API 返回 HTTP {exc.code}；请检查密钥、余额或额度") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DeepSeekError(f"DeepSeek API 连接失败：{type(exc).__name__}") from None
        if len(raw) > 1_000_000:
            raise DeepSeekError("DeepSeek API 响应超出预期大小")
        try:
            envelope = json.loads(raw)
            choice = envelope["choices"][0]
            if choice["finish_reason"] != "stop":
                raise DeepSeekError("DeepSeek API 回复未完整结束，已丢弃结果")
            answer = json.loads(choice["message"]["content"])
            if not isinstance(answer, dict):
                raise ValueError("expected object")
            usage = envelope.get("usage") or {}
            return answer, {key: usage.get(key) for key in (
                "prompt_tokens", "completion_tokens", "total_tokens"
            )}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekError("DeepSeek API 返回了无效 JSON，已丢弃结果") from exc

    def test(self) -> dict:
        answer, usage = self.complete_json(
            "请仅输出 JSON 对象，例如 {\"ok\": true}。",
            "连接测试：请输出 JSON 对象 {\"ok\": true}。",
            max_tokens=32,
        )
        if answer.get("ok") is not True:
            raise DeepSeekError("DeepSeek API 已响应，但连接测试结果不符合预期")
        return {"model": self.MODEL, "connected": True, "usage": usage}


class DeepSeekCardReviewer:
    """Model suggests line indices; deterministic checks create clickable cards."""

    def __init__(self, client: DeepSeekClient, *, max_calls: int = 5):
        self.client = client
        self.max_calls = max_calls
        self.calls = 0
        self.seen: set[str] = set()

    def review_cards(self, lines: list[OcrLine], window_height: float) -> list[ProfileCard]:
        from .human_agent import METRIC_RE, ProfileCard, _is_noise, parse_count

        if self.calls >= self.max_calls or not lines or len(lines) > 60:
            return []
        ordered = sorted(lines, key=lambda item: item.cy)
        compact = "\n".join(f"{index}: {item.text[:120]}" for index, item in enumerate(ordered))
        if len(compact) > 6000 or compact in self.seen:
            return []
        self.seen.add(compact)
        self.calls += 1
        answer, _ = self.client.complete_json(
            "你只复核公众号公开文章列表 OCR 的行号关系。仅输出 JSON，格式示例："
            '{"pairs":[{"title":2,"metric":3}]}。'
            "title 是原标题行的编号，metric 是紧随其后的‘阅读…赞…’行编号。"
            "无法确认就输出空数组；不得编造文字或数字。",
            compact,
            max_tokens=220,
        )
        pairs = answer.get("pairs")
        if not isinstance(pairs, list):
            return []
        cards: list[ProfileCard] = []
        used: set[int] = set()
        for pair in pairs[:12]:
            if not isinstance(pair, dict):
                continue
            title_index, metric_index = pair.get("title"), pair.get("metric")
            if not isinstance(title_index, int) or isinstance(title_index, bool):
                continue
            if not isinstance(metric_index, int) or isinstance(metric_index, bool):
                continue
            if not (0 <= title_index < metric_index < len(ordered)) or metric_index in used:
                continue
            title_line, metric_line = ordered[title_index], ordered[metric_index]
            if metric_line.cy - title_line.cy > 130 or metric_line.cy > window_height - 75:
                continue
            title = title_line.text.strip()
            metric = METRIC_RE.search(re.sub(r"\s+", "", metric_line.text))
            if not title or _is_noise(title) or not metric:
                continue
            read_num, like_num = parse_count(metric.group(1)), parse_count(metric.group(2))
            if read_num is None or like_num is None:
                continue
            cards.append(ProfileCard(title, read_num, like_num, metric_line.cx, metric_line.cy))
            used.add(metric_index)
        return cards
