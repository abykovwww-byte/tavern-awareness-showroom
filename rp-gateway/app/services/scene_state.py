"""Revision-7 narrator bundle parsing and deterministic scene projection."""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.models.schemas import RPNarratorBundle, SceneAllowance


SCENE_STATE_SCHEMA = "rp-gateway.scene-state.v1"
SCENE_BUNDLE_SCHEMA = "rp-gateway.rp-narrator-bundle.v1"
MAX_SCENE_DELTA_OPERATIONS = 16
MAX_SCENE_PRESENT_CHARACTERS = 64
MAX_SCENE_ENTITY_ID_CHARS = 128
MAX_SCENE_EVIDENCE_CHARS = 512

FIRST_PERSON_MARKERS = {"я", "мы", "i", "we"}
FIRST_PERSON_MOVEMENT_WORDS = {
    "иду", "идем", "пойду", "пойдем", "направляюсь", "направляемся",
    "отправляюсь", "отправляемся", "перехожу", "переходим", "ухожу", "уходим",
    "еду", "едем", "двигаюсь", "двигаемся", "возвращаюсь", "возвращаемся",
    "go", "going", "leave", "leaving", "walk", "walking", "head", "heading",
    "return", "returning",
}
ARRIVAL_WORDS = {
    "приходит", "пришел", "пришла", "входит", "вошел", "вошла", "подходит",
    "подошел", "подошла", "появляется", "появился", "появилась", "arrives",
    "arrived", "enters", "entered", "comes", "came",
}
DEPARTURE_WORDS = {
    "уходит", "ушел", "ушла", "выходит", "вышел", "вышла", "покидает",
    "покинул", "покинула", "leaves", "left", "departs", "departed", "exits",
    "exited",
}
NEGATION_WORDS = {"не", "нет", "ни", "без", "not", "no", "never", "without"}
DESTINATION_FILLER_WORDS = {
    "я", "мы", "i", "we", "к", "ко", "в", "во", "на", "из", "от", "до",
    "туда", "сюда", "go", "going", "head", "heading", "to", "the", "a", "an",
}
DESTINATION_CUES = {"к", "ко", "в", "во", "на", "до", "за", "через", "to", "toward", "towards", "into"}
DESTINATION_PREFIX_MODIFIERS = {"лишь", "только", "прямо", "сразу", "only", "straight", "directly"}
DESTINATION_REFERENCE_WORDS = {
    "это", "эта", "эту", "этот", "этой", "этом", "тема", "тему", "дело",
    "него", "нему", "ней", "нее", "них", "ним", "this", "that", "it", "them",
}
CORRECTION_MARKERS = {
    "лишь", "просто", "упомянул", "упомянула",
}
REMAIN_WORDS = {"остаюсь", "остаемся", "останусь", "остаться", "remain", "remains", "staying", "stay"}
ROLE_RELATION_WORDS = {
    "служит", "служу", "служил", "служила", "приказчик", "приказчиком", "слуга",
    "слугой", "дружинник", "дружинником", "верен", "верна", "принадлежит",
    "belongs", "belong", "serves", "serve", "loyal", "steward", "servant",
    "member", "retainer",
}
ROLE_CORRECTION_WORDS = {
    "неверно", "ошибочно", "будто", "якобы", "опроверг", "опровергла",
    "false", "incorrect", "incorrectly", "supposedly", "denied", "denies",
}
ROLE_COMPLEMENT_BOUNDARIES = {
    "а", "но", "и", "зато", "однако", "and", "but", "while", "however",
}
ROLE_COMITATIVE_MARKERS = {"вместе", "рядом", "совместно", "with", "alongside"}


@dataclass
class SceneMaterialization:
    text: str
    valid: bool
    violations: list[str] = field(default_factory=list)
    scene_state: dict[str, Any] | None = None
    claims: dict[str, Any] = field(default_factory=dict)
    applied_operations: list[dict[str, Any]] = field(default_factory=list)
    dropped_operations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def repair_instruction(self) -> str:
        details = "; ".join(self.violations)
        return (
            f"Верни один корректный {SCENE_BUNDLE_SCHEMA}. "
            "Исправь scene_claims и scene_delta строго по списку нарушений: "
            f"{details}"
        )


def normalize_anchor_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold().replace("ё", "е")
    return re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).strip()


def _phrase_positions(tokens: list[str], phrase: list[str]) -> list[int]:
    if not phrase:
        return []
    return [
        index
        for index in range(0, len(tokens) - len(phrase) + 1)
        if tokens[index : index + len(phrase)] == phrase
    ]


def _entity_aliases(entity_id: str, item: Any, declared: list[str] | None = None) -> set[str]:
    values = [entity_id, entity_id.replace("-", " ")]
    if isinstance(item, dict):
        for key in ("name", "display_name", "title"):
            if isinstance(item.get(key), str):
                values.append(item[key])
        aliases = item.get("aliases")
        if isinstance(aliases, list):
            values.extend(alias for alias in aliases if isinstance(alias, str))
    values.extend(alias for alias in declared or [] if isinstance(alias, str))
    return {normalized for value in values if (normalized := normalize_anchor_text(value))}


def _contains_phrase(tokens: list[str], phrase: str) -> bool:
    return bool(_phrase_positions(tokens, phrase.split()))


def _positive_transition(
    tokens: list[str],
    aliases: set[str],
    verbs: set[str],
    *,
    entity_id: str | None = None,
    aliases_by_entity: dict[str, set[str]] | None = None,
) -> bool:
    clause_boundaries = {"а", "но", "зато", "and", "but", "while"}
    for verb_position, token in enumerate(tokens):
        if token not in verbs:
            continue
        if set(tokens[max(0, verb_position - 3) : verb_position]) & NEGATION_WORDS:
            continue
        for alias in aliases:
            for alias_position in _phrase_positions(tokens, alias.split()):
                alias_end = alias_position + len(alias.split()) - 1
                distance = min(abs(alias_position - verb_position), abs(alias_end - verb_position))
                between = tokens[min(alias_end, verb_position) + 1 : max(alias_position, verb_position)]
                if distance > 4 or set(between) & clause_boundaries:
                    continue
                if entity_id is not None and aliases_by_entity:
                    nearest: dict[str, int] = {}
                    for candidate_id, candidate_aliases in aliases_by_entity.items():
                        distances = [
                            min(
                                abs(position - verb_position),
                                abs(position + len(candidate_alias.split()) - 1 - verb_position),
                            )
                            for candidate_alias in candidate_aliases
                            for position in _phrase_positions(tokens, candidate_alias.split())
                        ]
                        if distances:
                            nearest[candidate_id] = min(distances)
                    if nearest:
                        minimum = min(nearest.values())
                        closest = [candidate_id for candidate_id, value in nearest.items() if value == minimum]
                        if closest != [entity_id]:
                            continue
                return True
    return False


def _first_person_movement_position(message: str) -> int | None:
    tokens = normalize_anchor_text(message).split()
    for index, token in enumerate(tokens):
        if token not in FIRST_PERSON_MOVEMENT_WORDS:
            continue
        preceding = tokens[max(0, index - 3) : index]
        if not set(preceding) & FIRST_PERSON_MARKERS:
            continue
        if set(preceding) & NEGATION_WORDS:
            continue
        return index
    return None


def _explicit_first_person_destination(message: str) -> bool:
    tokens = normalize_anchor_text(message).split()
    movement_position = _first_person_movement_position(message)
    if movement_position is None:
        return False
    following = tokens[movement_position + 1 :]
    while following and following[0] in DESTINATION_PREFIX_MODIFIERS:
        following = following[1:]
    if len(following) < 2 or following[0] not in DESTINATION_CUES:
        return False
    destination_head = following[1]
    return (
        destination_head not in DESTINATION_FILLER_WORDS
        and destination_head not in DESTINATION_REFERENCE_WORDS
        and destination_head not in CORRECTION_MARKERS
        and destination_head not in REMAIN_WORDS
    )


def _location_mentions(state: dict[str, Any], message: str) -> list[str]:
    tokens = normalize_anchor_text(message).split()
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    return [
        str(location_id)
        for location_id, item in sorted(locations.items())
        if any(_contains_phrase(tokens, alias) for alias in _entity_aliases(str(location_id), item))
    ]


def _unambiguous_location_mentions(state: dict[str, Any], message: str) -> list[str]:
    mentioned = _location_mentions(state, message)
    return mentioned if len(mentioned) == 1 else []


def _bound_location_mentions(state: dict[str, Any], message: str) -> list[str]:
    bound: set[str] = set()
    for clause in re.split(r"[.!?;\r\n]+", message):
        movement_position = _first_person_movement_position(clause)
        if movement_position is None:
            continue
        tokens = normalize_anchor_text(clause).split()
        locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
        for location_id, item in sorted(locations.items()):
            for alias in _entity_aliases(str(location_id), item):
                for position in _fuzzy_phrase_positions(tokens, alias):
                    if position <= movement_position:
                        continue
                    between = tokens[movement_position + 1 : position]
                    cue_positions = [
                        index
                        for index, token in enumerate(between)
                        if token in DESTINATION_CUES
                    ]
                    if cue_positions and len(between) - cue_positions[-1] <= 4:
                        bound.add(str(location_id))
    return sorted(bound)


def _movement_cancelled_by_current_scene(state: dict[str, Any], message: str) -> bool:
    tokens = normalize_anchor_text(message).split()
    current = initial_scene_state(state)["location_id"]
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    current_aliases = _entity_aliases(current, locations.get(current))
    for remain_position, token in enumerate(tokens):
        if token not in REMAIN_WORDS:
            continue
        trailing = tokens[remain_position + 1 :]
        if any(_contains_phrase(trailing, alias) for alias in current_aliases):
            return True
        if "но" in tokens[max(0, remain_position - 3) : remain_position] or "but" in tokens[
            max(0, remain_position - 3) : remain_position
        ]:
            return True
    return False


def _stable_affiliations(
    state: dict[str, Any],
    authored: dict[str, str] | None = None,
) -> dict[str, str]:
    characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
    factions = state.get("factions") if isinstance(state.get("factions"), dict) else {}
    result = {
        str(character_id): loyalty
        for character_id, character in sorted(characters.items())
        if isinstance(character, dict)
        and isinstance((loyalty := character.get("loyalty")), str)
        and 0 < len(loyalty) <= MAX_SCENE_ENTITY_ID_CHARS
        and loyalty in factions
    }
    for character_id, affiliation in sorted((authored or {}).items()):
        if (
            character_id in characters
            and isinstance(affiliation, str)
            and 0 < len(affiliation) <= MAX_SCENE_ENTITY_ID_CHARS
        ):
            result[str(character_id)] = affiliation
        if len(result) >= MAX_SCENE_PRESENT_CHARACTERS:
            break
    return result


def _characters_at_location(state: dict[str, Any], location_id: str) -> list[str]:
    characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
    return sorted(
        str(character_id)
        for character_id, character in characters.items()
        if isinstance(character, dict)
        and str(character.get("location") or "") == location_id
        and str(character.get("status") or "alive").lower() not in {"dead", "missing"}
    )[:MAX_SCENE_PRESENT_CHARACTERS]


def initial_scene_state(
    state: dict[str, Any],
    authored_stable_affiliations: dict[str, str] | None = None,
) -> dict[str, Any]:
    meta = state.get("meta") if isinstance(state.get("meta"), dict) else {}
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
    stable = _stable_affiliations(state, authored_stable_affiliations)
    current = state.get("scene_state")
    if isinstance(current, dict) and current.get("schema_version") == SCENE_STATE_SCHEMA:
        current_stale = bool(current.get("stale", True))
        persisted_stable = current.get("stable_affiliations")
        if isinstance(persisted_stable, dict):
            combined_authored = {
                **persisted_stable,
                **(authored_stable_affiliations or {}),
            }
            if current_stale:
                stable = {
                    str(character_id): affiliation
                    for character_id, affiliation in combined_authored.items()
                    if isinstance(character_id, str)
                    and 0 < len(character_id) <= MAX_SCENE_ENTITY_ID_CHARS
                    and isinstance(affiliation, str)
                    and 0 < len(affiliation) <= MAX_SCENE_ENTITY_ID_CHARS
                }
                stable = dict(
                    list(sorted(stable.items()))[:MAX_SCENE_PRESENT_CHARACTERS]
                )
            else:
                stable = _stable_affiliations(state, combined_authored)
        location_id = str(current.get("location_id") or "unknown")
        present = current.get("present_character_ids")
        if (
            (current_stale or location_id == "unknown" or location_id in locations)
            and isinstance(present, list)
            and len(present) <= MAX_SCENE_PRESENT_CHARACTERS
            and all(
                isinstance(item, str)
                and 0 < len(item) <= MAX_SCENE_ENTITY_ID_CHARS
                and (current_stale or item in characters)
                for item in present
            )
        ):
            return {
                "schema_version": SCENE_STATE_SCHEMA,
                "location_id": location_id,
                "present_character_ids": sorted(set(present)),
                "stable_affiliations": stable,
                "as_of_state_version": int(current.get("as_of_state_version") or 0),
                "as_of_party_turn": int(current.get("as_of_party_turn") or 0),
                "stale": current_stale,
                "stale_reason": current.get("stale_reason"),
            }

    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    location_id = str(player.get("location") or "unknown")
    if location_id != "unknown" and location_id not in locations:
        location_id = "unknown"
    return {
        "schema_version": SCENE_STATE_SCHEMA,
        "location_id": location_id,
        "present_character_ids": _characters_at_location(state, location_id),
        "stable_affiliations": stable,
        "as_of_state_version": int(meta.get("state_version") or 0),
        "as_of_party_turn": int(meta.get("turn") or 0),
        "stale": True,
        "stale_reason": "legacy_bootstrap",
    }


def fallback_scene_state(
    state: dict[str, Any],
    authored_stable_affiliations: dict[str, str] | None = None,
) -> dict[str, Any]:
    scene = initial_scene_state(state, authored_stable_affiliations)
    scene["stale"] = True
    scene["stale_reason"] = "safe_fallback"
    return scene


def mark_scene_stale(
    state: dict[str, Any],
    reason: str,
    authored_stable_affiliations: dict[str, str] | None = None,
) -> dict[str, Any]:
    scene = initial_scene_state(state, authored_stable_affiliations)
    scene["stale"] = True
    scene["stale_reason"] = reason
    return scene


def scene_claim_baseline(
    state: dict[str, Any],
    authored_stable_affiliations: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Use current authoritative locations to re-anchor an explicitly stale projection."""

    persisted = initial_scene_state(state, authored_stable_affiliations)
    if not persisted["stale"]:
        return persisted
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    location_id = str(player.get("location") or "unknown")
    if location_id not in locations:
        location_id = "unknown"
    return {
        **persisted,
        "location_id": location_id,
        "present_character_ids": _characters_at_location(state, location_id),
        "stable_affiliations": _stable_affiliations(
            state,
            authored_stable_affiliations,
        ),
    }


def unresolved_noncanonical_fallback_turns(
    state: dict[str, Any],
    turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep a fallback marker only until a later reliable scene projection covers it."""

    as_of_party_turn = int(initial_scene_state(state).get("as_of_party_turn") or 0)
    current_party_turn = int(
        (state.get("meta") if isinstance(state.get("meta"), dict) else {}).get("turn")
        or 0
    )
    return [
        turn
        for turn in turns
        if not turn.get("noncanonical_safe_fallback")
        or (
            isinstance(turn.get("party_turn"), int)
            and not isinstance(turn.get("party_turn"), bool)
            and int(turn["party_turn"]) <= current_party_turn
            and int(turn["party_turn"]) > as_of_party_turn
        )
    ]


def scene_state_boundary_block(state: dict[str, Any]) -> str:
    scene = initial_scene_state(state)
    return (
        "SCENE_STATE_BOUNDARY\n"
        f"stale={str(bool(scene['stale'])).lower()}\n"
        f"as_of_party_turn={int(scene['as_of_party_turn'])}\n"
        f"as_of_state_version={int(scene['as_of_state_version'])}\n"
        "When stale, this is only the last reliable projection; unresolved player input is newer."
    )


def build_scene_transition_allowance(
    state: dict[str, Any],
    latest_user_message: str,
    *,
    character_aliases: dict[str, list[str]] | None = None,
    authored_stable_affiliations: dict[str, str] | None = None,
) -> dict[str, Any]:
    scene = scene_claim_baseline(state, authored_stable_affiliations)
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
    aliases_by_character = character_aliases if isinstance(character_aliases, dict) else {}
    tokens = normalize_anchor_text(latest_user_message).split()
    all_character_aliases = {
        str(character_id): _entity_aliases(
            str(character_id),
            character,
            aliases_by_character.get(str(character_id)),
        )
        for character_id, character in characters.items()
    }

    destinations: list[str] = []
    bound_location_mentions = _bound_location_mentions(state, latest_user_message)
    if _movement_cancelled_by_current_scene(state, latest_user_message):
        destinations = []
    elif len(bound_location_mentions) == 1:
        destinations = bound_location_mentions
    elif not bound_location_mentions and _explicit_first_person_destination(
        latest_user_message
    ):
        destinations = sorted(
            str(location_id)
            for location_id in locations
            if str(location_id) != scene["location_id"]
        )
    arrivals = [
        str(character_id)
        for character_id, character in sorted(characters.items())
        if str(character_id) not in scene["present_character_ids"]
        and _positive_transition(
            tokens,
            _entity_aliases(str(character_id), character, aliases_by_character.get(str(character_id))),
            ARRIVAL_WORDS,
            entity_id=str(character_id),
            aliases_by_entity=all_character_aliases,
        )
    ]
    departures = [
        str(character_id)
        for character_id, character in sorted(characters.items())
        if str(character_id) in scene["present_character_ids"]
        and _positive_transition(
            tokens,
            _entity_aliases(str(character_id), character, aliases_by_character.get(str(character_id))),
            DEPARTURE_WORDS,
            entity_id=str(character_id),
            aliases_by_entity=all_character_aliases,
        )
    ]
    return {
        "current_location_id": str(scene["location_id"]),
        "allowed_destination_ids": destinations[:MAX_SCENE_PRESENT_CHARACTERS],
        "allowed_arrival_ids": arrivals[:MAX_SCENE_PRESENT_CHARACTERS],
        "allowed_departure_ids": departures[:MAX_SCENE_PRESENT_CHARACTERS],
        "stable_affiliations": dict(list(scene["stable_affiliations"].items())[:MAX_SCENE_PRESENT_CHARACTERS]),
        "character_aliases": {
            str(character_id): [str(alias)[:MAX_SCENE_ENTITY_ID_CHARS] for alias in aliases[:8]]
            for character_id, aliases in sorted(aliases_by_character.items())
            if character_id in characters and isinstance(aliases, list)
        },
    }


def _allowance_from_outcome(authoritative_outcome: dict[str, Any] | None) -> SceneAllowance:
    outcome = authoritative_outcome if isinstance(authoritative_outcome, dict) else {}
    direct = outcome.get("scene_allowance")
    if isinstance(direct, dict):
        try:
            return SceneAllowance.model_validate(direct)
        except ValidationError:
            return SceneAllowance()
    block = str(outcome.get("authoritative_block") or "")
    match = re.search(
        r"<SCENE_TRANSITION_ALLOWANCE>\s*(\{.*?\})\s*</SCENE_TRANSITION_ALLOWANCE>",
        block,
        flags=re.DOTALL,
    )
    if match:
        try:
            return SceneAllowance.model_validate(json.loads(match.group(1)))
        except (ValidationError, ValueError, TypeError, json.JSONDecodeError):
            return SceneAllowance()
    return SceneAllowance()


def _response_content(response: dict[str, Any]) -> str:
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        return ""
    return message["content"]


def _parse_bundle(response: dict[str, Any]) -> tuple[RPNarratorBundle | None, list[str]]:
    content = _response_content(response).strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        content = fence.group(1).strip()
    try:
        return RPNarratorBundle.model_validate_json(content), []
    except ValidationError as exc:
        return None, [
            "scene bundle schema violation at "
            + ".".join(str(item) for item in error.get("loc", ()))
            + f": {error.get('msg')}"
            for error in exc.errors(include_url=False)
        ]


def _affiliation_aliases(state: dict[str, Any], affiliation_id: str) -> set[str]:
    factions = state.get("factions") if isinstance(state.get("factions"), dict) else {}
    return _entity_aliases(affiliation_id, factions.get(affiliation_id))


def _strip_quoted_spans(value: str) -> str:
    """Remove bounded direct speech before checking narrator-authored role claims."""

    return re.sub(
        r"«[^»]*»|“[^”]*”|\"[^\"]*\"|'[^']*'",
        " ",
        value,
    )


def _fuzzy_phrase_positions(tokens: list[str], phrase: str) -> list[int]:
    """Match finite affiliation aliases while tolerating a single case suffix."""

    phrase_tokens = phrase.split()
    if not phrase_tokens:
        return []
    positions: list[int] = []
    for index in range(0, len(tokens) - len(phrase_tokens) + 1):
        candidate = tokens[index : index + len(phrase_tokens)]
        if all(
            token == expected
            or (
                min(len(token), len(expected)) >= 3
                and (token.startswith(expected) or expected.startswith(token))
            )
            for token, expected in zip(candidate, phrase_tokens)
        ):
            positions.append(index)
    return positions


def _stable_affiliation_violations(
    narrative_text: str,
    state: dict[str, Any],
    allowance: SceneAllowance,
) -> list[str]:
    stable = allowance.stable_affiliations
    if not stable:
        return []
    characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
    character_aliases = {
        character_id: _entity_aliases(
            character_id,
            characters.get(character_id),
            allowance.character_aliases.get(character_id),
        )
        for character_id in stable
    }
    violations: list[str] = []
    for raw_sentence in re.split(r"(?<=[.!?])\s+|[\r\n]+", narrative_text):
        stripped = _strip_quoted_spans(raw_sentence).strip()
        if not stripped or "?" in stripped or stripped[:1] in {'"', "'", "«"}:
            continue
        tokens = normalize_anchor_text(stripped).split()
        relation_positions = [index for index, token in enumerate(tokens) if token in ROLE_RELATION_WORDS]
        if not relation_positions:
            continue
        for relation_position in relation_positions:
            if set(tokens[max(0, relation_position - 5) : relation_position]) & ROLE_CORRECTION_WORDS:
                continue
            if set(tokens[max(0, relation_position - 3) : relation_position]) & NEGATION_WORDS:
                continue

            subject_distances: dict[str, int] = {}
            for subject_id, aliases in character_aliases.items():
                positions = [
                    position
                    for alias in aliases
                    for position in _phrase_positions(tokens, alias.split())
                    if position <= relation_position
                ]
                if positions:
                    subject_distances[subject_id] = min(
                        relation_position - position for position in positions
                    )
            if not subject_distances:
                continue
            subject_distance = min(subject_distances.values())
            subjects = sorted(
                subject_id
                for subject_id, distance in subject_distances.items()
                if distance == subject_distance
            )
            if len(subjects) != 1 or subject_distance > 6:
                continue
            subject_id = subjects[0]
            expected = stable[subject_id]

            complement_end = min(relation_position + 9, len(tokens))
            for index in range(relation_position + 1, complement_end):
                if tokens[index] in ROLE_COMPLEMENT_BOUNDARIES | ROLE_COMITATIVE_MARKERS:
                    complement_end = index
                    break
            complement = tokens[relation_position + 1 : complement_end]
            if not complement or set(complement) & NEGATION_WORDS:
                continue

            expected_aliases = _affiliation_aliases(state, expected)
            if any(_fuzzy_phrase_positions(complement, alias) for alias in expected_aliases):
                continue

            foreign_aliases: set[str] = set()
            factions = state.get("factions") if isinstance(state.get("factions"), dict) else {}
            for faction_id in factions:
                if str(faction_id) != expected:
                    foreign_aliases.update(_affiliation_aliases(state, str(faction_id)))
            for other_id, other_affiliation in stable.items():
                if other_affiliation == expected:
                    continue
                foreign_aliases.update(_affiliation_aliases(state, other_affiliation))
                foreign_aliases.update(character_aliases.get(other_id, set()))
            if any(
                _phrase_positions(complement, alias.split())
                for alias in foreign_aliases
            ):
                violations.append(
                    f"narrative contradicts stable affiliation for {subject_id}: {expected}"
                )
    return violations


def _projection(state: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    return {
        "location_id": str(scene.get("location_id") or "unknown"),
        "present_character_ids": sorted(set(str(item) for item in scene.get("present_character_ids") or [])),
    }


def _apply_operation_projection(
    state: dict[str, Any],
    projection: dict[str, Any],
    operation: dict[str, Any],
) -> dict[str, Any]:
    updated = copy.deepcopy(projection)
    operation_type = operation["type"]
    if operation_type == "move_player":
        updated["location_id"] = operation["location_id"]
        updated["present_character_ids"] = _characters_at_location(state, operation["location_id"])
    elif operation_type == "character_arrive":
        updated["present_character_ids"] = sorted(
            set(updated["present_character_ids"]) | {operation["character_id"]}
        )
    else:
        updated["present_character_ids"] = sorted(
            set(updated["present_character_ids"]) - {operation["character_id"]}
        )
    return updated


def materialize_scene_bundle(
    raw_response: dict[str, Any],
    state: dict[str, Any],
    *,
    latest_user_message: str,
    party_turn: int,
    authoritative_outcome: dict[str, Any] | None = None,
) -> SceneMaterialization:
    bundle, schema_violations = _parse_bundle(raw_response)
    if bundle is None:
        return SceneMaterialization(text="", valid=False, violations=schema_violations)

    text = bundle.narrative_text.strip()
    claims = bundle.scene_claims.model_dump(mode="json")
    operations = [operation.model_dump(mode="json") for operation in bundle.scene_delta]
    locations = state.get("locations") if isinstance(state.get("locations"), dict) else {}
    characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
    violations: list[str] = []
    if claims["location_id"] not in locations:
        violations.append(f"scene_claims uses unknown location_id: {claims['location_id']}")
    unknown_claims = sorted(set(claims["present_character_ids"]) - set(characters))
    if unknown_claims:
        violations.append(f"scene_claims uses unknown character ID: {unknown_claims[0]}")
    if violations:
        return SceneMaterialization(text=text, valid=False, violations=violations, claims=claims)

    allowance = _allowance_from_outcome(authoritative_outcome)
    dynamic = SceneAllowance.model_validate(
        build_scene_transition_allowance(
            state,
            latest_user_message,
            character_aliases=allowance.character_aliases,
            authored_stable_affiliations=allowance.stable_affiliations,
        )
    )
    current = scene_claim_baseline(state, allowance.stable_affiliations)
    if allowance.current_location_id not in {"unknown", current["location_id"]}:
        return SceneMaterialization(
            text=text,
            valid=False,
            violations=["scene allowance current_location_id no longer matches authoritative scene"],
            claims=claims,
        )
    stable = _stable_affiliations(state, allowance.stable_affiliations)
    effective_allowance = SceneAllowance(
        current_location_id=current["location_id"],
        allowed_destination_ids=sorted(
            set(allowance.allowed_destination_ids) | set(dynamic.allowed_destination_ids)
        )[:MAX_SCENE_PRESENT_CHARACTERS],
        allowed_arrival_ids=sorted(
            set(allowance.allowed_arrival_ids) | set(dynamic.allowed_arrival_ids)
        )[:MAX_SCENE_PRESENT_CHARACTERS],
        allowed_departure_ids=sorted(
            set(allowance.allowed_departure_ids) | set(dynamic.allowed_departure_ids)
        )[:MAX_SCENE_PRESENT_CHARACTERS],
        stable_affiliations=stable,
        character_aliases=allowance.character_aliases,
    )
    role_violations = _stable_affiliation_violations(text, state, effective_allowance)
    if role_violations:
        return SceneMaterialization(text=text, valid=False, violations=role_violations, claims=claims)

    base = _projection(state, current)
    candidate = copy.deepcopy(base)
    seen_targets: set[tuple[str, str]] = set()
    semantic_operations: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        operation_type = operation["type"]
        location_id = operation["location_id"]
        if location_id not in locations:
            violations.append(f"scene_delta[{index}] uses unknown location ID: {location_id}")
            continue
        target = (operation_type, str(operation.get("character_id") or "player"))
        if target in seen_targets:
            violations.append(f"scene_delta[{index}] duplicates a scene transition")
            continue
        seen_targets.add(target)
        if operation_type == "move_player":
            if location_id not in effective_allowance.allowed_destination_ids:
                violations.append(f"scene_delta[{index}] changes location without player movement allowance")
                continue
        else:
            character_id = operation["character_id"]
            if character_id not in characters:
                violations.append(f"scene_delta[{index}] uses unknown character ID: {character_id}")
                continue
            if operation_type == "character_arrive":
                if character_id not in effective_allowance.allowed_arrival_ids:
                    violations.append(f"scene_delta[{index}] adds a character without authoritative arrival")
                    continue
                if character_id in candidate["present_character_ids"]:
                    violations.append(f"scene_delta[{index}] arrives a character already present")
                    continue
                if location_id != candidate["location_id"]:
                    violations.append(f"scene_delta[{index}] arrival location is outside the current scene")
                    continue
            else:
                if character_id not in candidate["present_character_ids"]:
                    violations.append(f"scene_delta[{index}] departs a character who is absent or outside the scene")
                    continue
                if character_id not in effective_allowance.allowed_departure_ids:
                    violations.append(f"scene_delta[{index}] removes a character without authoritative departure")
                    continue
                if location_id == candidate["location_id"]:
                    violations.append(f"scene_delta[{index}] departure destination must leave the current scene")
                    continue
        semantic_operations.append(operation)
        candidate = _apply_operation_projection(state, candidate, operation)

    if violations:
        return SceneMaterialization(text=text, valid=False, violations=violations, claims=claims)

    explained_location = claims["location_id"] == base["location_id"] or any(
        operation["type"] == "move_player" and operation["location_id"] == claims["location_id"]
        for operation in semantic_operations
    )
    location_base_present = (
        set(base["present_character_ids"])
        if claims["location_id"] == base["location_id"]
        else set(_characters_at_location(state, claims["location_id"]))
    )
    claim_present = set(claims["present_character_ids"])
    added = claim_present - location_base_present
    removed = location_base_present - claim_present
    explained_added = {
        operation["character_id"]
        for operation in semantic_operations
        if operation["type"] == "character_arrive"
    }
    explained_removed = {
        operation["character_id"]
        for operation in semantic_operations
        if operation["type"] == "character_depart"
    }
    if not explained_location:
        violations.append(
            f"scene_claims location_id mismatch: expected {base['location_id']}, received {claims['location_id']}"
        )
    if added - explained_added or removed - explained_removed:
        violations.append(
            "scene_claims present_character_ids mismatch: "
            f"expected transitions from {base['present_character_ids']}, received {claims['present_character_ids']}"
        )
    if violations:
        return SceneMaterialization(text=text, valid=False, violations=violations, claims=claims)

    canonical = copy.deepcopy(base)
    applied: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    normalized_action = normalize_anchor_text(latest_user_message)
    normalized_narrative = normalize_anchor_text(text)
    effect_aliases_by_entity = {
        str(character_id): _entity_aliases(
            str(character_id),
            character,
            effective_allowance.character_aliases.get(str(character_id)),
        )
        for character_id, character in characters.items()
    }
    for operation in semantic_operations:
        if operation["type"] == "move_player":
            claim_anchored = claims["location_id"] == operation["location_id"]
            evidence_source = normalized_action
            evidence_effect_anchored = (
                _explicit_first_person_destination(operation["evidence"])
                or (
                    _first_person_movement_position(operation["evidence"]) is not None
                    and operation["location_id"]
                    in _unambiguous_location_mentions(state, operation["evidence"])
                )
            )
        elif operation["type"] == "character_arrive":
            claim_anchored = operation["character_id"] in claim_present
            evidence_source = normalized_narrative
            evidence_effect_anchored = _positive_transition(
                normalize_anchor_text(operation["evidence"]).split(),
                _entity_aliases(
                    operation["character_id"],
                    characters.get(operation["character_id"]),
                    effective_allowance.character_aliases.get(operation["character_id"]),
                ),
                ARRIVAL_WORDS,
                entity_id=operation["character_id"],
                aliases_by_entity=effect_aliases_by_entity,
            )
        else:
            claim_anchored = operation["character_id"] not in claim_present
            evidence_source = normalized_narrative
            evidence_effect_anchored = _positive_transition(
                normalize_anchor_text(operation["evidence"]).split(),
                _entity_aliases(
                    operation["character_id"],
                    characters.get(operation["character_id"]),
                    effective_allowance.character_aliases.get(operation["character_id"]),
                ),
                DEPARTURE_WORDS,
                entity_id=operation["character_id"],
                aliases_by_entity=effect_aliases_by_entity,
            )
        normalized_evidence = normalize_anchor_text(operation["evidence"])
        if (
            not normalized_evidence
            or normalized_evidence not in evidence_source
            or not evidence_effect_anchored
        ):
            dropped.append({**operation, "reason": "unanchored_evidence"})
            continue
        if not claim_anchored:
            dropped.append({**operation, "reason": "unanchored_scene_claim"})
            continue
        canonical = _apply_operation_projection(state, canonical, operation)
        applied.append(operation)

    if not dropped and canonical != claims:
        return SceneMaterialization(
            text=text,
            valid=False,
            violations=[
                "scene_claims mismatch after authorized scene_delta: "
                f"expected {canonical}, received {claims}"
            ],
            claims=claims,
        )

    next_scene = {
        "schema_version": SCENE_STATE_SCHEMA,
        "location_id": canonical["location_id"],
        "present_character_ids": canonical["present_character_ids"],
        "stable_affiliations": stable,
        "as_of_state_version": (
            current["as_of_state_version"]
            if dropped
            else int(state.get("meta", {}).get("state_version") or 0) + 1
        ),
        "as_of_party_turn": current["as_of_party_turn"] if dropped else int(party_turn),
        "stale": bool(dropped),
        "stale_reason": "unanchored_scene_delta" if dropped else None,
    }
    return SceneMaterialization(
        text=text,
        valid=True,
        scene_state=next_scene,
        claims=claims,
        applied_operations=applied,
        dropped_operations=dropped,
    )
