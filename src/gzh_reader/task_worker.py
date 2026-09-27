"""Single collection run in a separate process so UI work has a hard stop."""

from __future__ import annotations

import json
from pathlib import Path

from .urls import redact


def _safe_error(exc: Exception, payload: dict) -> str:
    message = redact(str(exc))
    for field in ("model_api_key", "deepseek_api_key"):
        secret = payload.get(field)
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return f"{type(exc).__name__}: {message[:260]}"


def run_collect_task(payload: dict, events) -> None:
    """The only worker entrypoint; never send a credential through progress events."""
    from .human_agent import MacHumanAccountCollector

    def progress(stage: str, detail: dict) -> None:
        events.put({"kind": "progress", "stage": stage, "detail": detail})

    try:
        options = {}
        provider = payload.get("model_provider", "deepseek")
        api_key = payload.get("model_api_key") or payload.get("deepseek_api_key")
        model_id = payload.get("model_id")
        if payload.get("deepseek_review"):
            from .model_client import create_card_reviewer

            options["reviewer"] = create_card_reviewer(provider, api_key, model_id)
        if payload.get("agent_mode"):
            from .agent_runtime import CardPlanAgent
            from .model_client import create_client

            options["agent"] = CardPlanAgent(create_client(provider, api_key, model_id))
        workspace = MacHumanAccountCollector(progress, **options).collect(
            payload["url"], Path(payload["output"]),
            account_name=payload.get("account_name"),
            max_articles=payload.get("max_articles"),
            max_new_articles=payload.get("max_new_articles", 5),
        )
        progress_path = workspace / "audit" / "human-agent-progress.json"
        try:
            report = json.loads(progress_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
        finished = report.get("complete") is True
        events.put({
            "kind": "final", "stage": "complete" if finished else "incomplete",
            "detail": {
                "workspace": str(workspace),
                "captured": report.get("captured_article_count"),
                "declared": report.get("declared_article_count"),
                "reason": report.get("stop_reason", "unverified"),
            },
        })
    except Exception as exc:  # noqa: BLE001 - process boundary must report all failures
        events.put({
            "kind": "final", "stage": "failed",
            "detail": {"error": _safe_error(exc, payload)},
        })
