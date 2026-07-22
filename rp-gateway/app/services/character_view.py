"""Structured player/NPC sheet extraction from authoritative party state."""

from __future__ import annotations

from typing import Any


def party_character_sheets(state: dict[str, Any]) -> dict[str, Any]:
    player = state.get("player", {}) if isinstance(state.get("player"), dict) else {}
    characters = state.get("characters", {}) if isinstance(state.get("characters"), dict) else {}
    locations = state.get("locations", {}) if isinstance(state.get("locations"), dict) else {}
    relationships = state.get("relationships", {}) if isinstance(state.get("relationships"), dict) else {}
    timeline = state.get("timeline", []) if isinstance(state.get("timeline"), list) else []
    active_threads = state.get("active_threads", []) if isinstance(state.get("active_threads"), list) else []

    sheets = []
    for character_id, character in sorted(characters.items()):
        if not isinstance(character, dict):
            continue
        sheet = {
            "id": character_id,
            "name": str(character.get("name") or character_id),
            "status": character.get("status", "unknown"),
            "location": character.get("location", "unknown"),
            "location_label": location_label(character.get("location", "unknown"), locations),
            "attitude_to_player": character.get("attitude_to_player", ""),
            "trust": character.get("trust"),
            "fear": character.get("fear"),
            "loyalty": character.get("loyalty", ""),
            "current_goal": character.get("current_goal", ""),
            "knowledge": safe_list(character.get("knowledge")),
            "obligations": safe_list(character.get("obligations")),
            "hard_constraints": safe_list(character.get("hard_constraints")),
            "secrets": safe_list(character.get("secrets")),
            "last_confirmed_update": character.get("last_confirmed_update"),
            "relationship": relationship_for(character_id, relationships),
            "threads": threads_for(character_id, active_threads),
            "last_seen": last_seen(character_id, timeline),
        }
        sheets.append(sheet)

    return {
        "player": {
            "id": player.get("character_id") or "player",
            "name": player.get("name") or "Игрок",
            "status": player.get("status", "unknown"),
            "location": player.get("location", "unknown"),
            "location_label": location_label(player.get("location", "unknown"), locations),
            "description": player.get("description", ""),
            "resources": player.get("resources", {}),
            "known_abilities": safe_list(player.get("known_abilities")),
            "constraints": safe_list(player.get("constraints")),
            "known_world_facts": safe_list(player.get("known_world_facts")),
        },
        "characters": sheets,
        "counts": {
            "characters": len(sheets),
            "active": sum(1 for sheet in sheets if sheet.get("status") == "alive"),
            "with_obligations": sum(1 for sheet in sheets if sheet.get("obligations")),
            "with_threads": sum(1 for sheet in sheets if sheet.get("threads")),
        },
    }


def relationship_for(character_id: str, relationships: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [f"player_{character_id}", f"{character_id}_player"]
    for key in candidates:
        value = relationships.get(key)
        if isinstance(value, dict):
            relation = dict(value)
            relation["id"] = key
            return relation
    for key, value in relationships.items():
        if not isinstance(value, dict):
            continue
        if value.get("from") == "player" and value.get("to") == character_id:
            relation = dict(value)
            relation["id"] = key
            return relation
        if value.get("from") == character_id and value.get("to") == "player":
            relation = dict(value)
            relation["id"] = key
            return relation
    return None


def threads_for(character_id: str, active_threads: list[Any]) -> list[dict[str, Any]]:
    matches = []
    needle = character_id.lower()
    for thread in active_threads:
        if not isinstance(thread, dict):
            continue
        text = " ".join(str(value) for value in thread.values()).lower()
        if needle in text:
            matches.append(thread)
    return matches


def last_seen(character_id: str, timeline: list[Any]) -> dict[str, Any] | None:
    for event in reversed(timeline):
        if not isinstance(event, dict):
            continue
        participants = event.get("participants", [])
        if isinstance(participants, list) and character_id in participants:
            return {
                "turn": event.get("turn"),
                "event": event.get("event"),
                "confirmed": event.get("confirmed"),
            }
    return None


def location_label(location_id: Any, locations: dict[str, Any]) -> str:
    location_text = str(location_id or "unknown")
    location = locations.get(location_text)
    if isinstance(location, dict):
        for key in ("name", "title", "label"):
            value = location.get(key)
            if value:
                return str(value)
        description = str(location.get("description") or "").strip()
        if description:
            return description.split(":")[0].split(".")[0].split(";")[0][:90]
    return humanize_slug(location_text)


def humanize_slug(value: str) -> str:
    if not value or value == "unknown":
        return "unknown"
    return value.replace("-", " ").replace("_", " ")


def safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
