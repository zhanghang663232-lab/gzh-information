"""Select a bounded OCR-review model without changing the Mac UI executor."""

from __future__ import annotations

def create_client(provider: str, api_key: str | None = None, model_id: str | None = None):
    if provider == "deepseek":
        from .deepseek import DeepSeekClient

        return DeepSeekClient(api_key, model_id=model_id) if model_id else DeepSeekClient(api_key)
    if provider == "doubao":
        from .ark import ArkClient

        return ArkClient(api_key, model_id=model_id) if model_id else ArkClient(api_key)
    raise ValueError("不支持的模型供应商")


def create_card_reviewer(
    provider: str, api_key: str | None = None, model_id: str | None = None,
) -> object:
    from .deepseek import OCRCardReviewer

    return OCRCardReviewer(create_client(provider, api_key, model_id))
