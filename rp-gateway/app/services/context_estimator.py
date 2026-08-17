"""LLM context accounting for recorded party prompts."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from app.core.config import Settings
from app.models.schemas import ModelProfileSummary
from app.services.rp_story_memory import RPStoryMemoryUpdater
from app.services.state_store import StateStore


TOKEN_CHARS = 2.5


def estimate_party_context(
    store: StateStore,
    settings: Settings,
    model_profile: ModelProfileSummary | None,
) -> dict[str, Any]:
    all_turns = store.turn_history(limit=10000)
    latest_turn = store.latest_turn(include_prompt=True, include_response=True)
    if latest_turn and latest_turn.get("prompt_json"):
        prompt_messages = load_prompt_messages(latest_turn["prompt_json"])
        if prompt_messages is None:
            return empty_recorded_context(
                settings,
                model_profile,
                len(all_turns),
                latest_turn,
                source="invalid_recorded_prompt",
                store=store,
            )
        prompt_source = "recorded_last_turn"
        source_turn_limit = None
        message_prompt_limit = None
    else:
        return empty_recorded_context(settings, model_profile, len(all_turns), latest_turn, store=store)

    prompt_text = "\n".join(f"{message['role']}: {message['content']}" for message in prompt_messages)

    non_system_messages = [message for message in prompt_messages if message.get("role") != "system"]
    retained_history_messages = max(len(non_system_messages) - 1, 0)
    retained_history_turns_estimate = math.ceil(retained_history_messages / 2)
    prior_turns_total = max(len(all_turns) - 1, 0)
    omitted_history_turns_estimate = max(prior_turns_total - retained_history_turns_estimate, 0)
    state_summary_text = first_system_content(prompt_messages, "Relevant state summary:")
    memory_text = first_system_content(prompt_messages, "LONG_TERM_PARTY_MEMORY")
    story_memory_text = first_system_content(prompt_messages, "RP_STORY_MEMORY")
    recorded_story_coverage = story_memory_prompt_coverage(story_memory_text)
    relevant_characters_text = first_system_content(prompt_messages, "RELEVANT_CHARACTERS")
    history_text = "\n".join(str(message.get("content") or "") for message in non_system_messages[:-1])
    memory_summary = store.latest_memory_coverage()
    story_memory = store.effective_rp_story_memory() if settings.scenario_type == "rp" else None
    cache_usage = cache_usage_from_response(latest_turn.get("response_json"))
    context_limit_tokens = settings.effective_party_context_limit_tokens
    prompt_tokens = estimate_tokens(prompt_text)
    completion_reserved_tokens = int((model_profile.params if model_profile else {}).get("max_tokens") or 0)
    hard_input_budget_tokens = max(
        context_limit_tokens
        - max(settings.party_context_completion_reserve_tokens, completion_reserved_tokens),
        1,
    )
    total_with_reserved = prompt_tokens + completion_reserved_tokens
    usage_ratio = total_with_reserved / context_limit_tokens if context_limit_tokens else None

    estimate = {
        "model": model_profile.model if model_profile else settings.narrative_model,
        "model_title": model_profile.title if model_profile else settings.narrative_model,
        "context_window": model_profile.context_window if model_profile else "",
        "context_limit_tokens": context_limit_tokens,
        "estimated_prompt_tokens": prompt_tokens,
        "estimated_prompt_chars": len(prompt_text),
        "completion_reserved_tokens": completion_reserved_tokens,
        "estimated_total_tokens": total_with_reserved,
        "hard_input_budget_tokens": hard_input_budget_tokens,
        "hard_budget_status": (
            "within_budget" if prompt_tokens <= hard_input_budget_tokens else "over_budget"
        ),
        "usage_ratio": usage_ratio,
        "severity": severity_for_usage(usage_ratio),
        "state_summary_tokens": estimate_tokens(state_summary_text),
        "relevant_characters_tokens": estimate_tokens(relevant_characters_text) if relevant_characters_text else 0,
        "memory_summary_tokens": estimate_tokens(memory_text) if memory_text else 0,
        "memory_covered_turns": [memory_summary.get("from_turn_id"), memory_summary.get("to_turn_id")] if memory_summary else None,
        "prompt_cache": cache_usage,
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
    if settings.scenario_type == "rp":
        story_stats = RPStoryMemoryUpdater(settings, store).stats()
        estimate["rp_story_memory_tokens"] = estimate_tokens(story_memory_text) if story_memory_text else 0
        estimate["rp_story_memory_covered_turns"] = (
            [story_memory.get("from_turn_id"), story_memory.get("to_turn_id")] if story_memory else None
        )
        effective_story_coverage = int(story_memory.get("to_turn_id") or 0) if story_memory else 0
        pending_threshold = int(
            story_stats.get("pending_turn_threshold")
            or settings.rp_story_memory_update_turns
        )
        pending_turns = int(story_stats.get("pending_turns") or 0)
        threshold_exceeded = bool(
            story_stats.get(
                "pending_turn_threshold_exceeded",
                pending_turns >= pending_threshold,
            )
        )
        estimate["rp_story_memory_prompt_covered_through_turn_id"] = recorded_story_coverage
        estimate["rp_story_memory_effective_covered_through_turn_id"] = (
            effective_story_coverage or None
        )
        estimate["rp_story_memory_prompt_matches_effective_coverage"] = (
            int(recorded_story_coverage or 0) == effective_story_coverage
            if settings.rp_contract_revision >= 7
            else None
        )
        estimate["rp_story_memory_pending_turns"] = pending_turns
        estimate["rp_story_memory_pending_tokens"] = int(story_stats.get("pending_tokens") or 0)
        estimate["rp_story_memory_pending_turn_threshold"] = pending_threshold
        estimate["rp_story_memory_pending_threshold_exceeded"] = threshold_exceeded
        estimate["rp_story_memory_operator_status"] = story_stats.get("operator_status") or (
            "lagging" if threshold_exceeded else "normal"
        )
        estimate["rp_story_memory_hard_overflow"] = bool(story_stats.get("hard_overflow", False))
        estimate["rp_story_memory_force_refresh_attempted"] = bool(
            story_stats.get("force_refresh_attempted", False)
        )
        estimate["rp_story_memory_force_refresh_request_id"] = story_stats.get(
            "force_refresh_request_id"
        )
        estimate["rp_story_memory_force_refresh_batches"] = int(
            story_stats.get("force_refresh_batches") or 0
        )
        estimate["rp_story_memory_force_refresh_terminal_result"] = story_stats.get(
            "force_refresh_terminal_result"
        )
        estimate["rp_story_memory_force_refresh_coverage_before"] = story_stats.get(
            "force_refresh_coverage_before"
        )
        estimate["rp_story_memory_force_refresh_coverage_after"] = story_stats.get(
            "force_refresh_coverage_after"
        )
    return estimate


def empty_recorded_context(
    settings: Settings,
    model_profile: ModelProfileSummary | None,
    history_turns_total: int,
    latest_turn: dict[str, Any] | None,
    source: str = "missing_recorded_prompt",
    store: StateStore | None = None,
) -> dict[str, Any]:
    context_limit_tokens = settings.effective_party_context_limit_tokens
    completion_reserved_tokens = int(
        (model_profile.params if model_profile else {}).get("max_tokens") or 0
    )
    if source == "invalid_recorded_prompt":
        note = "Записанный prompt_json последнего хода не удалось прочитать. Новые ходы будут считаться по свежему фактическому prompt."
    else:
        note = (
            "Для последнего хода еще нет записанного prompt_json. Новые ходы после обновления Gateway будут считаться по фактическому prompt."
            if latest_turn
            else "Ходов еще нет: фактический предыдущий prompt отсутствует."
        )
    estimate = {
        "model": model_profile.model if model_profile else settings.narrative_model,
        "model_title": model_profile.title if model_profile else settings.narrative_model,
        "context_window": model_profile.context_window if model_profile else "",
        "context_limit_tokens": context_limit_tokens,
        "estimated_prompt_tokens": 0,
        "estimated_prompt_chars": 0,
        "completion_reserved_tokens": completion_reserved_tokens,
        "estimated_total_tokens": 0,
        "hard_input_budget_tokens": max(
            context_limit_tokens
            - max(settings.party_context_completion_reserve_tokens, completion_reserved_tokens),
            1,
        ),
        "hard_budget_status": "unknown",
        "usage_ratio": None,
        "severity": "unknown",
        "state_summary_tokens": 0,
        "relevant_characters_tokens": 0,
        "memory_summary_tokens": 0,
        "memory_covered_turns": None,
        "prompt_cache": {"available": False, "cached_tokens": 0, "cache_write_tokens": 0},
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
    if settings.scenario_type == "rp":
        story_stats = RPStoryMemoryUpdater(settings, store).stats() if store is not None else {}
        pending_threshold = int(
            story_stats.get("pending_turn_threshold")
            or settings.rp_story_memory_update_turns
        )
        pending_turns = int(story_stats.get("pending_turns") or 0)
        estimate["rp_story_memory_tokens"] = 0
        estimate["rp_story_memory_covered_turns"] = None
        estimate["rp_story_memory_prompt_covered_through_turn_id"] = None
        estimate["rp_story_memory_effective_covered_through_turn_id"] = story_stats.get(
            "covered_through_turn_id"
        )
        estimate["rp_story_memory_prompt_matches_effective_coverage"] = None
        estimate["rp_story_memory_pending_turns"] = pending_turns
        estimate["rp_story_memory_pending_tokens"] = int(story_stats.get("pending_tokens") or 0)
        estimate["rp_story_memory_pending_turn_threshold"] = pending_threshold
        estimate["rp_story_memory_pending_threshold_exceeded"] = bool(
            story_stats.get(
                "pending_turn_threshold_exceeded",
                pending_turns >= pending_threshold,
            )
        )
        estimate["rp_story_memory_operator_status"] = story_stats.get("operator_status") or (
            "lagging" if pending_turns >= pending_threshold else "normal"
        )
        estimate["rp_story_memory_hard_overflow"] = bool(story_stats.get("hard_overflow", False))
        estimate["rp_story_memory_force_refresh_attempted"] = bool(
            story_stats.get("force_refresh_attempted", False)
        )
        estimate["rp_story_memory_force_refresh_request_id"] = story_stats.get(
            "force_refresh_request_id"
        )
        estimate["rp_story_memory_force_refresh_batches"] = int(
            story_stats.get("force_refresh_batches") or 0
        )
        estimate["rp_story_memory_force_refresh_terminal_result"] = story_stats.get(
            "force_refresh_terminal_result"
        )
        estimate["rp_story_memory_force_refresh_coverage_before"] = story_stats.get(
            "force_refresh_coverage_before"
        )
        estimate["rp_story_memory_force_refresh_coverage_after"] = story_stats.get(
            "force_refresh_coverage_after"
        )
    return estimate


def first_system_content(messages: list[dict[str, Any]], prefix: str) -> str:
    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") == "system" and content.startswith(prefix):
            return content
    return ""


def story_memory_prompt_coverage(content: str) -> int | None:
    match = re.search(r"^covered_through_turn_id=(\d+)$", content, flags=re.MULTILINE)
    return int(match.group(1)) if match else None


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


def cache_usage_from_response(value: Any) -> dict[str, Any]:
    """Normalize cache fields returned by OpenRouter/OpenAI and Gemini-compatible APIs."""
    try:
        response = json.loads(str(value)) if isinstance(value, str) else value
    except (TypeError, ValueError, json.JSONDecodeError):
        response = None
    if not isinstance(response, dict):
        return {"available": False, "cached_tokens": 0, "cache_write_tokens": 0}
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    gemini = response.get("usage_metadata") if isinstance(response.get("usage_metadata"), dict) else {}
    cached_tokens = int(details.get("cached_tokens") or gemini.get("cached_content_token_count") or gemini.get("total_cached_tokens") or 0)
    cache_write_tokens = int(details.get("cache_write_tokens") or 0)
    return {"available": bool(details or gemini), "cached_tokens": cached_tokens, "cache_write_tokens": cache_write_tokens}


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
