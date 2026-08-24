"""Revision-8 RP history selection shared by runtime and prompt inspection."""

from __future__ import annotations

from typing import Any


AUTO_START_HISTORY_MESSAGE = "[AUTO_START] Старт партии"
RP_MEMORY_SECTION_KEYS = (
    "situation",
    "threads",
    "characters",
    "assets_and_rules",
    "chronology_and_hooks",
)
RP_RAW_HISTORY_ANCHOR_TURNS = 8


def eligible_rp_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only playable narrative units; legacy null kind means narrative."""

    eligible: list[dict[str, Any]] = []
    for turn in turns:
        metadata = turn.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        raw_kind = metadata.get("turn_kind")
        kind = str(raw_kind).strip() if raw_kind is not None else "narrative"
        kind = kind or "narrative"
        player = str(turn.get("player_message") or "").strip()
        narrator = str(turn.get("narrative_response") or "").strip()
        if kind == "opening_scene" and narrator:
            eligible.append(turn)
        elif kind == "narrative" and player and narrator:
            eligible.append(turn)
    return eligible


def rp_turn_messages(turn: dict[str, Any]) -> list[tuple[str, str]]:
    """Render one playable unit, suppressing only the exact auto-start message."""

    player = str(turn.get("player_message") or "")
    narrator = str(turn.get("narrative_response") or "")
    messages: list[tuple[str, str]] = []
    if player != AUTO_START_HISTORY_MESSAGE:
        messages.append(("user", player))
    messages.append(("assistant", narrator))
    return messages


def story_memory_safe_coverage(snapshot: dict[str, Any] | None) -> int:
    """Return the minimum of all five section coverages, failing closed."""

    if not snapshot:
        return 0
    memory = snapshot.get("memory")
    section_status = memory.get("section_status") if isinstance(memory, dict) else None
    if isinstance(section_status, dict):
        coverages: list[int] = []
        for section_key in RP_MEMORY_SECTION_KEYS:
            value = section_status.get(section_key)
            if not isinstance(value, dict):
                return 0
            coverage = value.get("coverage")
            if not isinstance(coverage, int) or isinstance(coverage, bool) or coverage < 0:
                return 0
            coverages.append(coverage)
        return min(coverages) if coverages else 0
    return 0


def raw_history_window(
    turns: list[dict[str, Any]],
    *,
    safe_coverage: int,
    window_turns: int,
) -> list[dict[str, Any]]:
    """Keep one cache-stable recent window and every turn not safely covered by memory."""

    eligible = eligible_rp_turns(turns)
    if not eligible:
        return []
    desired_start = max(len(eligible) - max(int(window_turns), 20), 0)
    recent_start = (
        desired_start // RP_RAW_HISTORY_ANCHOR_TURNS
    ) * RP_RAW_HISTORY_ANCHOR_TURNS
    first_uncovered = next(
        (
            index
            for index, turn in enumerate(eligible)
            if int(turn.get("id") or 0) > safe_coverage
        ),
        len(eligible),
    )
    return eligible[min(recent_start, first_uncovered) :]


def removable_covered_history_units(
    turns: list[dict[str, Any]],
    *,
    safe_coverage: int,
    minimum_turns: int = 20,
) -> int:
    """Count head units that may be dropped without opening a coverage gap."""

    retain_floor = max(int(minimum_turns), 0)
    maximum_removal = max(len(turns) - retain_floor, 0)
    covered_prefix = 0
    for turn in turns:
        if int(turn.get("id") or 0) > safe_coverage:
            break
        covered_prefix += 1
    return min(maximum_removal, covered_prefix)


def recent_rp_scan_text(
    turns: list[dict[str, Any]],
    current_player_message: str,
    *,
    depth: int = 3,
) -> str:
    """Current input plus complete recent game units for deterministic entity matching."""

    parts: list[str] = []
    for turn in eligible_rp_turns(turns)[-max(int(depth), 0):]:
        parts.extend(content for _role, content in rp_turn_messages(turn))
    if str(current_player_message).strip():
        parts.append(str(current_player_message))
    return "\n".join(parts)
