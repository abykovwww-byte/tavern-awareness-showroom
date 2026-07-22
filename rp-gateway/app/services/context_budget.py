"""Token-budget helpers shared by party history and long-term memory."""

from __future__ import annotations

import math
import re
from typing import Any


# Russian prose tokenizes more densely than English. Keep the estimate conservative.
TOKEN_CHARS = 2.5


def estimate_tokens(text: Any) -> int:
    value = str(text or "")
    if not value:
        return 0
    return max(1, math.ceil(len(value) / TOKEN_CHARS))


def turn_tokens(turn: dict[str, Any]) -> int:
    return estimate_tokens(turn.get("player_message")) + estimate_tokens(turn.get("narrative_response")) + 8


def split_turns_by_token_budget(
    turns: list[dict[str, Any]],
    token_budget: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return older overflow turns and the newest complete turns that fit."""
    selected: list[dict[str, Any]] = []
    used = 0
    for turn in reversed(turns):
        cost = turn_tokens(turn)
        if selected and used + cost > token_budget:
            break
        selected.append(turn)
        used += cost
    selected.reverse()
    return turns[: len(turns) - len(selected)], selected


def oldest_turns_within_token_budget(turns: list[dict[str, Any]], token_budget: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    used = 0
    for turn in turns:
        cost = turn_tokens(turn)
        if selected and used + cost > token_budget:
            break
        selected.append(turn)
        used += cost
    return selected


def turns_token_count(turns: list[dict[str, Any]]) -> int:
    return sum(turn_tokens(turn) for turn in turns)


def model_context_limit_tokens(model_profile: Any) -> int | None:
    if not model_profile:
        return None
    params = getattr(model_profile, "params", {}) or {}
    for key in ("context_tokens", "context_length", "max_context_tokens", "max_model_len"):
        parsed = int_from_value(params.get(key))
        if parsed:
            return parsed

    text = f"{getattr(model_profile, 'context_window', '')} {getattr(model_profile, 'description', '')}".lower()
    compact = re.sub(r"[,_\s]", "", text)
    match = re.search(r"(\d+(?:\.\d+)?)([mk])", compact)
    if match:
        multiplier = 1_000_000 if match.group(2) == "m" else 1_000
        return int(float(match.group(1)) * multiplier)
    match = re.search(r"(\d[\d,_\s]{3,})\s*(?:tokens?|токен)", text)
    return int_from_value(match.group(1)) if match else None


def int_from_value(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value > 0:
        return int(value)
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        if digits:
            parsed = int(digits)
            return parsed if parsed > 0 else None
    return None
