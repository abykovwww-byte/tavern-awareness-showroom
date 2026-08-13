#!/usr/bin/env python3
"""Validate WorldPack-owned RP relationship models."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "rp-relationships.v2"
TOP_LEVEL_KEYS = {
    "schema_version", "axes", "events", "character_weights", "roles",
    "wounds", "clocks", "plot", "characters", "trust_mapping",
}
BOUNDARY_EVENTS = {"crack", "ultimatum", "plot", "strike", "favour"}


def error(errors: list[str], path: Path, message: str) -> None:
    errors.append(f"{path}: {message}")


def object_value(value: Any, errors: list[str], path: Path, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        error(errors, path, f"{label} must be an object")
        return {}
    return value


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def validate_characters(
    characters: dict[str, Any],
    errors: list[str],
    path: Path,
    character_ids: set[str] | None,
) -> None:
    seen_aliases: dict[str, str] = {}
    for character_id, raw_character in characters.items():
        character = object_value(raw_character, errors, path, f"characters.{character_id}")
        aliases = character.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            error(errors, path, f"characters.{character_id}.aliases must contain at least one form")
            continue
        for index, alias in enumerate(aliases):
            if not isinstance(alias, str) or not alias.strip() or len(alias) > 120:
                error(errors, path, f"characters.{character_id}.aliases[{index}] must be a non-empty string of at most 120 characters")
                continue
            normalized = normalize_alias(alias)
            if not normalized:
                error(errors, path, f"characters.{character_id}.aliases[{index}] is empty after normalization")
                continue
            previous = seen_aliases.get(normalized)
            if previous is not None and previous != character_id:
                error(errors, path, f"duplicate normalized alias {alias!r} for {previous} and {character_id}")
            else:
                seen_aliases[normalized] = character_id
        if character_ids is not None and character_id not in character_ids:
            error(errors, path, f"characters references unknown state character: {character_id}")
    if character_ids is not None:
        for character_id in sorted(character_ids - set(characters)):
            error(errors, path, f"state character has no relationship aliases: {character_id}")


def validate_trust_mapping(mapping: dict[str, Any], errors: list[str], path: Path) -> None:
    if mapping.get("kind") != "linear":
        error(errors, path, "trust_mapping.kind must be linear")
    input_range = mapping.get("in")
    output_range = mapping.get("out")
    for label, value in (("in", input_range), ("out", output_range)):
        if (
            not isinstance(value, list)
            or len(value) != 2
            or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
            or value[0] >= value[1]
        ):
            error(errors, path, f"trust_mapping.{label} must be two increasing integers")
    if (
        isinstance(output_range, list)
        and len(output_range) == 2
        and all(isinstance(item, int) and not isinstance(item, bool) for item in output_range)
        and output_range[0] >= output_range[1]
    ):
        error(errors, path, "trust_mapping.out must be monotonic increasing")


def validate_bands(axis: dict[str, Any], errors: list[str], path: Path) -> None:
    bands = axis.get("bands")
    if not isinstance(bands, list) or not bands:
        error(errors, path, "axes.loyalty.bands must be a non-empty list")
        return
    axis_min = axis.get("min")
    axis_max = axis.get("max")
    if not isinstance(axis_min, int) or not isinstance(axis_max, int) or axis_min >= axis_max:
        error(errors, path, "axes.loyalty min/max must be increasing integers")
        return
    seen: set[str] = set()
    previous_max: int | None = None
    minimum_bands: list[tuple[int, int]] = []
    for index, raw_band in enumerate(bands):
        if not isinstance(raw_band, dict):
            error(errors, path, f"band {index} must be an object")
            continue
        band_id = raw_band.get("id")
        if not isinstance(band_id, str) or not band_id:
            error(errors, path, f"band {index} has no id")
        elif band_id in seen:
            error(errors, path, f"duplicate band id: {band_id}")
        else:
            seen.add(band_id)
        if not isinstance(raw_band.get("label"), str) or not raw_band["label"].strip():
            error(errors, path, f"band {band_id or index} has no label")
        has_min = "min" in raw_band
        has_max = "max" in raw_band
        if has_min == has_max:
            error(errors, path, f"band {band_id or index} must define exactly one of min/max")
            continue
        boundary = raw_band.get("min") if has_min else raw_band.get("max")
        if not isinstance(boundary, int) or not axis_min <= boundary <= axis_max:
            error(errors, path, f"band {band_id or index} boundary is outside the axis")
            continue
        if has_max:
            if minimum_bands:
                error(errors, path, f"band {band_id or index} max boundary follows min boundaries")
            if previous_max is not None and boundary <= previous_max:
                error(errors, path, f"band {band_id or index} overlaps a previous band")
            previous_max = boundary
        else:
            minimum_bands.append((index, boundary))
        opens = raw_band.get("opens")
        if opens is not None and opens not in BOUNDARY_EVENTS:
            error(errors, path, f"band {band_id or index} opens unknown event: {opens}")
        if opens is not None and raw_band.get("band_on") not in {"cross", "resolution"}:
            error(errors, path, f"band {band_id or index} has invalid band_on")
    if minimum_bands:
        first_min = minimum_bands[0][1]
        if previous_max is not None and first_min <= previous_max:
            error(errors, path, "positive band boundary overlaps the preceding band")
        previous_min: int | None = None
        for index, boundary in minimum_bands:
            if previous_min is not None and boundary <= previous_min:
                error(errors, path, f"band {bands[index].get('id', index)} overlaps a previous band")
            previous_min = boundary


def validate_model(model: Any, path: Path, character_ids: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(model, dict):
        return [f"{path}: model must be an object"]
    unknown = sorted(set(model) - TOP_LEVEL_KEYS)
    if unknown:
        error(errors, path, f"unknown top-level fields: {', '.join(unknown)}")
    if model.get("schema_version") != SCHEMA_VERSION:
        error(errors, path, f"schema_version must be {SCHEMA_VERSION}")
    axes = object_value(model.get("axes"), errors, path, "axes")
    if set(axes) != {"loyalty"}:
        error(errors, path, "first slice must define only the loyalty axis")
    loyalty = object_value(axes.get("loyalty"), errors, path, "axes.loyalty")
    for field in ("per_turn_cap", "band_deadband"):
        if not isinstance(loyalty.get(field), int) or loyalty[field] < 0:
            error(errors, path, f"axes.loyalty.{field} must be a non-negative integer")
    validate_bands(loyalty, errors, path)

    characters = object_value(model.get("characters"), errors, path, "characters")
    validate_characters(characters, errors, path, character_ids)
    trust_mapping = object_value(model.get("trust_mapping"), errors, path, "trust_mapping")
    validate_trust_mapping(trust_mapping, errors, path)

    roles = object_value(model.get("roles"), errors, path, "roles")
    wounds = object_value(model.get("wounds"), errors, path, "wounds")
    events = object_value(model.get("events"), errors, path, "events")
    positive_favour_resolvers: list[str] = []
    for event_id, raw_event in events.items():
        event = object_value(raw_event, errors, path, f"event {event_id}")
        if event.get("axis") != "loyalty":
            error(errors, path, f"event {event_id} references unknown axis")
        weight = event.get("weight")
        if not isinstance(weight, int) or isinstance(weight, bool) or not -30 <= weight <= 15:
            error(errors, path, f"event {event_id} weight must be between -30 and 15")
        decay = event.get("decay_turns")
        if decay is not None and (not isinstance(decay, int) or isinstance(decay, bool) or decay <= 0):
            error(errors, path, f"event {event_id} decay_turns must be null or a positive integer")
        resolves = event.get("resolves")
        if resolves is not None:
            if not isinstance(resolves, list) or not resolves or any(not isinstance(item, str) for item in resolves):
                error(errors, path, f"event {event_id} resolves must be a non-empty list of event ids")
            else:
                unknown_resolutions = sorted(set(resolves) - BOUNDARY_EVENTS)
                if unknown_resolutions:
                    error(
                        errors,
                        path,
                        f"event {event_id} resolves unknown boundary events: {', '.join(unknown_resolutions)}",
                    )
                if len(resolves) != len(set(resolves)):
                    error(errors, path, f"event {event_id} resolves must not contain duplicates")
                if "favour" in resolves:
                    if isinstance(weight, int) and not isinstance(weight, bool) and weight > 0:
                        positive_favour_resolvers.append(event_id)
                    else:
                        error(errors, path, f"event {event_id} that resolves favour must have positive weight")
        wound = event.get("wound")
        if wound is not None and wound not in wounds:
            error(errors, path, f"event {event_id} references unknown wound: {wound}")
    if not positive_favour_resolvers:
        error(errors, path, "events must declare at least one positive event that resolves favour")

    character_weights = object_value(model.get("character_weights"), errors, path, "character_weights")
    for character_id, raw_config in character_weights.items():
        config = object_value(raw_config, errors, path, f"character_weights.{character_id}")
        role = config.get("role", "subordinate")
        if role not in roles:
            error(errors, path, f"character {character_id} references unknown role: {role}")
        if character_ids is not None and character_id not in character_ids:
            error(errors, path, f"character_weights references unknown character: {character_id}")
        multipliers = object_value(config.get("multipliers", {}), errors, path, f"character_weights.{character_id}.multipliers")
        for event_id, multiplier in multipliers.items():
            if event_id not in events:
                error(errors, path, f"character {character_id} references unknown event: {event_id}")
            if not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool) or multiplier <= 0:
                error(errors, path, f"character {character_id} multiplier for {event_id} must be positive")

    clocks = object_value(model.get("clocks"), errors, path, "clocks")
    for clock in sorted(BOUNDARY_EVENTS):
        if not isinstance(clocks.get(clock), int) or isinstance(clocks.get(clock), bool) or clocks[clock] <= 0:
            error(errors, path, f"clocks.{clock} must be a positive integer")
    plot = object_value(model.get("plot"), errors, path, "plot")
    chance = plot.get("discovery_chance_per_turn")
    if not isinstance(chance, (int, float)) or isinstance(chance, bool) or not 0 <= chance <= 1:
        error(errors, path, "plot.discovery_chance_per_turn must be between 0 and 1")
    if not isinstance(plot.get("tell_required_every_turn"), bool):
        error(errors, path, "plot.tell_required_every_turn must be boolean")
    return errors


def validate_worldpack(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{manifest_path}: cannot read manifest: {exc}"]
    declaration = manifest.get("relationships")
    if declaration is None:
        return []
    if not isinstance(declaration, dict) or set(declaration) != {"schema_version", "model"}:
        return [f"{manifest_path}: relationships must contain only schema_version and model"]
    if declaration.get("schema_version") != SCHEMA_VERSION:
        error(errors, manifest_path, f"relationships.schema_version must be {SCHEMA_VERSION}")
    relative_model = declaration.get("model")
    if not isinstance(relative_model, str) or not relative_model:
        error(errors, manifest_path, "relationships.model must be a relative path")
        return errors
    model_path = (manifest_path.parent / relative_model).resolve()
    try:
        model_path.relative_to(manifest_path.parent.resolve())
    except ValueError:
        error(errors, manifest_path, "relationships.model escapes the WorldPack")
        return errors
    try:
        model = json.loads(model_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        error(errors, model_path, f"cannot read model: {exc}")
        return errors
    state_seed_path = manifest_path.parent / str((manifest.get("files") or {}).get("state_seed", "state-seed.json"))
    try:
        state = json.loads(state_seed_path.read_text(encoding="utf-8"))
        character_ids = set((state.get("characters") or {}).keys())
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        error(errors, state_seed_path, f"cannot read character ids: {exc}")
        character_ids = None
    errors.extend(validate_model(model, model_path, character_ids))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worldpacks", type=Path, default=Path("worldpacks"))
    args = parser.parse_args()
    errors: list[str] = []
    for manifest_path in sorted(args.worldpacks.glob("*/manifest.json")):
        errors.extend(validate_worldpack(manifest_path))
    if errors:
        print("Relationship model validation failed:")
        for item in errors:
            print(f"- {item}")
        return 1
    print("Relationship models valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
