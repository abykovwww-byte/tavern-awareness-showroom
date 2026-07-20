"""Approximate LLM context accounting for party prompts."""

from __future__ import annotations

import math
import re
from typing import Any

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage, ModelProfileSummary, Outcome
from app.services.narrative import NarrativeClient
from app.services.state_store import StateStore


SOURCE_TURN_LIMIT = 8
NARRATIVE_MESSAGE_LIMIT = 12
TOKEN_CHARS = 3.5


def estimate_party_context(
    store: StateStore,
    settings: Settings,
    model_profile: ModelProfileSummary | None,
) -> dict[str, Any]:
    state = store.get_state()
    all_turns = store.turn_history(limit=10000)
    source_turns = all_turns[-SOURCE_TURN_LIMIT:]
    request_messages = history_messages(source_turns)
    request_messages.append(ChatMessage(role="user", content="[следующий ход игрока]"))

    request = ChatCompletionRequest(model=settings.narrative_model, messages=request_messages, stream=False)
    outcome = placeholder_outcome()
    prompt_messages = NarrativeClient(settings).narrative_messages(request, state, outcome, repair_instruction=None)
    prompt_text = "\n".join(f"{message['role']}: {message['content']}" for message in prompt_messages)

    retained_history_messages = min(len(source_turns) * 2, max(NARRATIVE_MESSAGE_LIMIT - 1, 0))
    retained_history_turns_estimate = math.ceil(retained_history_messages / 2)
    omitted_history_turns_estimate = max(len(all_turns) - retained_history_turns_estimate, 0)
    state_summary_text = str(narrative_state_summary(state))
    history_text = "\n".join(str(message.content or "") for message in request_messages[-NARRATIVE_MESSAGE_LIMIT:-1])
    context_limit_tokens = parse_context_limit_tokens(model_profile)
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
        "direct_history_tokens": estimate_tokens(history_text) if history_text else 0,
        "history_turns_total": len(all_turns),
        "history_source_turn_limit": SOURCE_TURN_LIMIT,
        "message_prompt_limit": NARRATIVE_MESSAGE_LIMIT,
        "direct_history_messages": retained_history_messages,
        "direct_history_turns_estimate": retained_history_turns_estimate,
        "omitted_history_turns_estimate": omitted_history_turns_estimate,
        "history_limited": omitted_history_turns_estimate > 0,
        "notes": notes_for_context(context_limit_tokens, omitted_history_turns_estimate, retained_history_messages),
    }


def history_messages(turns: list[dict[str, Any]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for turn in turns:
        messages.append(ChatMessage(role="user", content=turn["player_message"]))
        messages.append(ChatMessage(role="assistant", content=turn["narrative_response"]))
    return messages


def narrative_state_summary(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "campaign_id": state.get("meta", {}).get("campaign_id"),
        "turn": state.get("meta", {}).get("turn"),
        "player": state.get("player", {}),
        "relationships": state.get("relationships", {}),
        "constraints": state.get("world_constraints", []),
    }


def placeholder_outcome() -> Outcome:
    return Outcome(
        check_id="context-estimate",
        action_type="feasibility",
        actor="player",
        result="partial_success",
        roll=10,
        difficulty=10,
        modifiers={},
        final_score=10,
        consequences=["Placeholder outcome for prompt size estimation."],
        authoritative_block=(
            "Mechanical outcome placeholder for context estimate. "
            "Actual turns include the real check result, consequences and forbidden reinterpretations."
        ),
    )


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
