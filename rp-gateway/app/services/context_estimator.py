"""LLM context accounting for recorded party prompts."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from app.core.config import Settings
from app.models.schemas import ModelProfileSummary
from app.services.state_store import StateStore


TOKEN_CHARS = 2.5


def estimate_party_context(
    store: StateStore,
    settings: Settings,
    model_profile: ModelProfileSummary | None,
) -> dict[str, Any]:
    all_turns = store.turn_history(limit=10000)
    latest_turn = store.latest_turn(include_prompt=True)
    if latest_turn and latest_turn.get("prompt_json"):
        prompt_messages = load_prompt_messages(latest_turn["prompt_json"])
        if prompt_messages is None:
            return empty_recorded_context(settings, model_profile, len(all_turns), latest_turn, source="invalid_recorded_prompt")
        prompt_source = "recorded_last_turn"
        source_turn_limit = None
        message_prompt_limit = None
    else:
        return empty_recorded_context(settings, model_profile, len(all_turns), latest_turn)

    prompt_text = "\n".join(f"{message['role']}: {message['content']}" for message in prompt_messages)

    non_system_messages = [message for message in prompt_messages if message.get("role") != "system"]
    retained_history_messages = max(len(non_system_messages) - 1, 0)
    retained_history_turns_estimate = math.ceil(retained_history_messages / 2)
    prior_turns_total = max(len(all_turns) - 1, 0)
    omitted_history_turns_estimate = max(prior_turns_total - retained_history_turns_estimate, 0)
    state_summary_text = first_system_content(prompt_messages, "Relevant state summary:")
    memory_text = first_system_content(prompt_messages, "LONG_TERM_PARTY_MEMORY")
    relevant_characters_text = first_system_content(prompt_messages, "RELEVANT_CHARACTERS")
    history_text = "\n".join(str(message.get("content") or "") for message in non_system_messages[:-1])
    context_limit_tokens = settings.effective_party_context_limit_tokens
    prompt_tokens = estimate_tokens(prompt_text)
    completion_reserved_tokens = int((model_profile.params if model_profile else {}).get("max_tokens") or 0)
    total_with_reserved = prompt_tokens + completion_reserved_tokens
    usage_ratio = total_with_reserved / context_limit_tokens if context_limit_tokens else None

    return {
        "model": model_profile.model if model_profile else settings.narrative_model,
        "model_title": model_profile.title if model_profile else settings.narrative_model,
        "context_window": model_profile.context_window if model_profile else "",
        "context_limit_tokens": context_limit_tokens,
        "estimated_prompt_tokens": prompt_tokens,
        "estimated_prompt_chars": len(prompt_text),
        "completion_reserved_tokens": completion_reserved_tokens,
        "estimated_total_tokens": total_with_reserved,
        "usage_ratio": usage_ratio,
        "severity": severity_for_usage(usage_ratio),
        "state_summary_tokens": estimate_tokens(state_summary_text),
        "relevant_characters_tokens": estimate_tokens(relevant_characters_text) if relevant_characters_text else 0,
        "memory_summary_tokens": estimate_tokens(memory_text) if memory_text else 0,
        "memory_covered_turns": None,
        "direct_history_tokens": estimate_tokens(history_text) if history_text else 0,
        "history_turns_total": len(all_turns),
        "history_source_turn_limit": source_turn_limit,
        "message_prompt_limit": message_prompt_limit,
        "raw_turns_kept": retained_history_turns_estimate,
        "history_token_budget": settings.effective_party_history_token_budget,
        "direct_history_messages": retained_history_messages,
        "direct_history_turns_estimate": retained_history_turns_estimate,
        "omitted_history_turns_estimate": omitted_history_turns_estimate,
        "history_limited": omitted_history_turns_estimate > 0,
        "prompt_source": prompt_source,
        "last_turn_id": latest_turn.get("id") if latest_turn else None,
        "last_request_id": latest_turn.get("request_id") if latest_turn else None,
        "notes": notes_for_context(context_limit_tokens, omitted_history_turns_estimate, retained_history_messages),
    }


def empty_recorded_context(
    settings: Settings,
    model_profile: ModelProfileSummary | None,
    history_turns_total: int,
    latest_turn: dict[str, Any] | None,
    source: str = "missing_recorded_prompt",
) -> dict[str, Any]:
    context_limit_tokens = settings.effective_party_context_limit_tokens
    if source == "invalid_recorded_prompt":
        note = "Записанный prompt_json последнего хода не удалось прочитать. Новые ходы будут считаться по свежему фактическому prompt."
    else:
        note = (
            "Для последнего хода еще нет записанного prompt_json. Новые ходы после обновления Gateway будут считаться по фактическому prompt."
            if latest_turn
            else "Ходов еще нет: фактический предыдущий prompt отсутствует."
        )
    return {
        "model": model_profile.model if model_profile else settings.narrative_model,
        "model_title": model_profile.title if model_profile else settings.narrative_model,
        "context_window": model_profile.context_window if model_profile else "",
        "context_limit_tokens": context_limit_tokens,
        "estimated_prompt_tokens": 0,
        "estimated_prompt_chars": 0,
        "completion_reserved_tokens": int((model_profile.params if model_profile else {}).get("max_tokens") or 0),
        "estimated_total_tokens": 0,
        "usage_ratio": None,
        "severity": "unknown",
        "state_summary_tokens": 0,
        "relevant_characters_tokens": 0,
        "memory_summary_tokens": 0,
        "memory_covered_turns": None,
        "direct_history_tokens": 0,
        "history_turns_total": history_turns_total,
        "history_source_turn_limit": None,
        "message_prompt_limit": None,
        "raw_turns_kept": 0,
        "history_token_budget": settings.effective_party_history_token_budget,
        "direct_history_messages": 0,
        "direct_history_turns_estimate": 0,
        "omitted_history_turns_estimate": 0,
        "history_limited": False,
        "prompt_source": source,
        "last_turn_id": latest_turn.get("id") if latest_turn else None,
        "last_request_id": latest_turn.get("request_id") if latest_turn else None,
        "notes": [note],
    }


def first_system_content(messages: list[dict[str, Any]], prefix: str) -> str:
    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") == "system" and content.startswith(prefix):
            return content
    return ""


def load_prompt_messages(value: Any) -> list[dict[str, Any]] | None:
    try:
        messages = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(messages, list):
        return None
    normalized: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            return None
        normalized.append(message)
    return normalized


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / TOKEN_CHARS))


def parse_context_limit_tokens(model_profile: ModelProfileSummary | None) -> int | None:
    if not model_profile:
        return None
    for key in ("context_tokens", "context_length", "max_context_tokens", "max_model_len"):
        value = model_profile.params.get(key)
        parsed = int_from_value(value)
        if parsed:
            return parsed
    text = f"{model_profile.context_window} {model_profile.description}".lower().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)\s*([mkкм])", text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    multiplier = 1_000_000 if suffix in {"m", "м"} else 1_000
    return int(number * multiplier)


def int_from_value(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        if digits:
            parsed = int(digits)
            return parsed if parsed > 0 else None
    return None


def severity_for_usage(usage_ratio: float | None) -> str:
    if usage_ratio is None:
        return "unknown"
    if usage_ratio >= 0.9:
        return "danger"
    if usage_ratio >= 0.75:
        return "warning"
    if usage_ratio >= 0.5:
        return "watch"
    return "ok"


def notes_for_context(
    context_limit_tokens: int | None,
    omitted_history_turns_estimate: int,
    retained_history_messages: int,
) -> list[str]:
    notes: list[str] = []
    if context_limit_tokens is None:
        notes.append("Лимит модели неизвестен: показываем размер prompt без процента.")
    if omitted_history_turns_estimate > 0:
        notes.append("Старые ходы уже не попадают в прямой диалоговый prompt; они остаются в storage и state.")
    if retained_history_messages % 2:
        notes.append("Лимит сообщений может оставлять неполную пару user/GM на границе истории.")
    return notes
