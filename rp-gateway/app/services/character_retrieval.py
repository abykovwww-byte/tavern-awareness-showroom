"""Select the small, authoritative NPC slice needed for one narrative turn."""

from __future__ import annotations

import re
from typing import Any


MAX_RETRIEVED_CHARACTERS = 6
MAX_LIST_ITEMS = 8


def retrieve_relevant_characters(
    state: dict[str, Any],
    latest_player_message: str,
    *,
    outcome_target: str | None = None,
    limit: int = MAX_RETRIEVED_CHARACTERS,
) -> list[dict[str, Any]]:
    """Return only NPC records that can matter to the current action.

    This is intentionally deterministic rather than embedding-based: the current
    state already has stable entity ids, location and relationship edges, so the
    selection is explainable and does not require an additional model request.
    """
    characters = state.get("characters")
    if not isinstance(characters, dict):
        return []

    player_location = normalized_text(state.get("player", {}).get("location")) if isinstance(state.get("player"), dict) else ""
    action = normalized_text(f"{latest_player_message} {outcome_target or ''}")
    relationship_ids = character_ids_with_player_relationship(state.get("relationships"))
    active_thread_ids = character_ids_in_threads(state.get("active_threads"))
    ranked: list[tuple[int, str, dict[str, Any], list[str]]] = []

    for raw_id, raw_character in characters.items():
        if not isinstance(raw_id, str) or not isinstance(raw_character, dict):
            continue
        character_id = raw_id.strip()
        if not character_id:
            continue
        score = 0
        reasons: list[str] = []
        aliases = {normalized_text(character_id), normalized_text(raw_character.get("name"))}
        aliases.discard("")
        if any(f" {alias} " in f" {action} " for alias in aliases):
            score += 100
            reasons.append("mentioned_by_player")
        if player_location and normalized_text(raw_character.get("location")) == player_location:
            score += 30
            reasons.append("same_location")
        if character_id in active_thread_ids:
            score += 20
            reasons.append("active_thread")
        # A relationship alone is not enough to load an NPC: mature campaigns
        # often have edges to every known character. It only enriches an entity
        # already selected by the action, current location, or an active thread.
        if score and character_id in relationship_ids:
            score += 15
            reasons.append("player_relationship")
        if score:
            ranked.append((score, character_id, raw_character, reasons))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {
            "id": character_id,
            "selection_reasons": reasons,
            "character": compact_character(character_id, character),
        }
        for _score, character_id, character, reasons in ranked[: max(limit, 0)]
    ]


def relationship_scene_character_ids(
    state: dict[str, Any],
    latest_player_message: str,
    *,
    outcome_target: str | None = None,
    character_aliases: dict[str, list[str]] | None = None,
    use_scene_state: bool = True,
    use_seed_signals: bool = True,
) -> set[str]:
    """Return reliable committed presence, or the deterministic legacy fallback scope."""

    characters = state.get("characters")
    if not isinstance(characters, dict):
        return set()
    scene_state = state.get("scene_state") if use_scene_state else None
    if (
        isinstance(scene_state, dict)
        and scene_state.get("stale") is False
        and isinstance(scene_state.get("present_character_ids"), list)
    ):
        return {
            str(character_id)
            for character_id in scene_state["present_character_ids"]
            if str(character_id) in characters
        }

    player = state.get("player") if use_seed_signals else None
    player_location = normalized_text(player.get("location")) if isinstance(player, dict) else ""
    action = normalized_text(latest_player_message)
    target = normalized_text(outcome_target)
    declared_aliases = character_aliases or {}
    active_thread_ids = (
        character_ids_in_threads(state.get("active_threads"))
        if use_seed_signals
        else set()
    )
    ranked: list[tuple[int, str]] = []

    for raw_id, raw_character in characters.items():
        if not isinstance(raw_id, str) or not isinstance(raw_character, dict):
            continue
        character_id = raw_id.strip()
        if not character_id:
            continue
        aliases = {
            normalized_text(character_id),
            normalized_text(raw_character.get("name")),
            normalized_text(raw_character.get("display_name")),
            *(
                normalized_text(alias)
                for alias in declared_aliases.get(character_id, [])
                if isinstance(alias, str)
            ),
        }
        aliases.discard("")
        mentioned = any(f" {alias} " in f" {action} " for alias in aliases)
        targeted = any(f" {alias} " in f" {target} " for alias in aliases)
        same_location = bool(
            use_seed_signals
            and player_location
            and normalized_text(raw_character.get("location")) == player_location
        )
        if not (mentioned or targeted or same_location):
            continue

        score = 0
        if mentioned or targeted:
            score += 100
        if same_location:
            score += 30
        if use_seed_signals and character_id in active_thread_ids:
            score += 20
        ranked.append((score, character_id))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    return {
        character_id
        for _score, character_id in ranked[:MAX_RETRIEVED_CHARACTERS]
    }


def selected_character_relationships(state: dict[str, Any], characters: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    selected_ids = {entry.get("id") for entry in characters if isinstance(entry.get("id"), str)}
    relationships = state.get("relationships")
    if not selected_ids or not isinstance(relationships, dict):
        return {}

    selected: dict[str, dict[str, Any]] = {}
    for relationship_id, relationship in relationships.items():
        if not isinstance(relationship_id, str) or not isinstance(relationship, dict):
            continue
        endpoints = {str(relationship.get("from") or ""), str(relationship.get("to") or "")}
        if endpoints & selected_ids:
            selected[relationship_id] = compact_relationship(relationship)
    return selected


def latest_player_action(messages: list[Any]) -> str:
    for message in reversed(messages):
        if getattr(message, "role", None) == "user" and isinstance(getattr(message, "content", None), str):
            return message.content
    return ""


def character_ids_with_player_relationship(relationships: Any) -> set[str]:
    if not isinstance(relationships, dict):
        return set()
    ids: set[str] = set()
    for relationship in relationships.values():
        if not isinstance(relationship, dict):
            continue
        source = str(relationship.get("from") or "")
        target = str(relationship.get("to") or "")
        if source == "player" and target:
            ids.add(target)
        elif target == "player" and source:
            ids.add(source)
    return ids


def character_ids_in_threads(threads: Any) -> set[str]:
    if not isinstance(threads, list):
        return set()
    ids: set[str] = set()
    for thread in threads:
        if not isinstance(thread, dict):
            continue
        for key in ("character_id", "character_ids", "participants", "entities", "npc_ids"):
            value = thread.get(key)
            if isinstance(value, str):
                ids.add(value)
            elif isinstance(value, list):
                ids.update(str(item) for item in value if isinstance(item, (str, int)))
    return ids


def compact_character(character_id: str, character: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {"id": character_id}
    for key in (
        "name",
        "status",
        "location",
        "attitude_to_player",
        "fear",
        "loyalty",
        "current_goal",
        "last_confirmed_update",
    ):
        value = character.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    for key in ("knowledge", "obligations", "hard_constraints"):
        value = character.get(key)
        if isinstance(value, list) and value:
            compact[key] = value[:MAX_LIST_ITEMS]
    return compact


def compact_relationship(relationship: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("from", "to", "suspicion", "fear", "loyalty"):
        value = relationship.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    notes = relationship.get("notes")
    if isinstance(notes, list) and notes:
        compact["notes"] = notes[-MAX_LIST_ITEMS:]
    return compact


def normalized_text(value: Any) -> str:
    return re.sub(r"[\W_]+", " ", str(value or "").casefold()).strip()
