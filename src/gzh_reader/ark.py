"""Optional Volcengine Ark/Doubao JSON review of public list OCR."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request


class ArkError(RuntimeError):
    pass


class ArkClient:
    URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    DEFAULT_MODEL = "doubao-seed-2-1-lite-260915"

    def __init__(
        self, api_key: str | None = None, *, model_id: str | None = None,
        timeout: int = 20,
    ) -> None:
        self._key = (api_key if api_key is not None else os.environ.get("ARK_API_KEY", "")).strip()
        if not self._key:
            raise ArkError("未配置 ARK_API_KEY；不要把密钥发到聊天或写入仓库")
        if not self._key.isascii() or any(ch.isspace() for ch in self._key):
            raise ArkError("ARK_API_KEY 格式无效：必须是单行 ASCII 密钥")
        self.model_id = (model_id or self.DEFAULT_MODEL).strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", self.model_id):
            raise ArkError("豆包模型 ID 格式无效")
        self.timeout = timeout

    def complete_json(self, system: str, user: str, *, max_tokens: int = 300) -> tuple[dict, dict]:
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system + " 只输出一个 JSON 对象。"},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
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
            raise ArkError(f"火山方舟 API 返回 HTTP {exc.code}；请检查密钥、模型权限或额度") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ArkError(f"火山方舟 API 连接失败：{type(exc).__name__}") from None
        if len(raw) > 1_000_000:
            raise ArkError("火山方舟 API 响应超出预期大小")
        try:
            envelope = json.loads(raw)
            choice = envelope["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ArkError("火山方舟 API 回复未完整结束，已丢弃结果")
            answer = json.loads(choice["message"]["content"])
            if not isinstance(answer, dict):
                raise ValueError("expected object")
            usage = envelope.get("usage") or {}
            return answer, {key: usage.get(key) for key in (
                "prompt_tokens", "completion_tokens", "total_tokens"
            )}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ArkError("火山方舟 API 返回了无效 JSON，已丢弃结果") from exc

    def test(self) -> dict:
        answer, usage = self.complete_json(
            "请输出 JSON 对象。", "连接测试：请输出 {\"ok\":true}。", max_tokens=32,
        )
        if answer.get("ok") is not True:
            raise ArkError("火山方舟 API 已响应，但连接测试结果不符合预期")
        return {"model": self.model_id, "connected": True, "usage": usage}
