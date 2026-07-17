#!/usr/bin/env python3
"""Validate RP Stack world state and proposed state patches.

This intentionally avoids third-party dependencies so it can run on a clean
Ubuntu server with only Python 3 installed.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any


REQUIRED_TOP_LEVEL = {
    "meta": dict,
    "player": dict,
    "characters": dict,
    "factions": dict,
    "locations": dict,
    "resources": dict,
    "relationships": dict,
    "active_threads": list,
    "completed_threads": list,
    "world_constraints": list,
    "timeline": list,
    "last_turn": dict,
}

REQUIRED_META = {
    "campaign_id": str,
    "schema_version": str,
    "state_version": int,
    "turn": int,
    "last_updated": str,
}

REQUIRED_PLAYER = {
    "location": str,
    "status": str,
    "reputation": dict,
    "resources": dict,
    "known_abilities": list,
    "constraints": list,
    "known_world_facts": list,
}

REQUIRED_CHARACTER = {
    "status": str,
    "location": str,
    "attitude_to_player": str,
    "trust": int,
    "fear": int,
    "loyalty": str,
    "current_goal": str,
    "knowledge": list,
    "secrets": list,
    "obligations": list,
    "hard_constraints": list,
    "last_confirmed_update": int,
}

ALLOWED_OPS = {"add", "replace", "remove"}


class ValidationError(Exception):
    pass


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    except FileNotFoundError as exc:
        raise ValidationError(f"{path}: file not found") from exc


def require_type(obj: dict[str, Any], field: str, expected: type, prefix: str, errors: list[str]) -> None:
    if field not in obj:
        errors.append(f"{prefix}.{field}: missing required field")
    elif not isinstance(obj[field], expected):
        errors.append(f"{prefix}.{field}: expected {expected.__name__}, got {type(obj[field]).__name__}")


def validate_state(state: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(state, dict):
        return ["state: expected object"]

    for field, expected in REQUIRED_TOP_LEVEL.items():
        require_type(state, field, expected, "state", errors)

    meta = state.get("meta", {})
    if isinstance(meta, dict):
        for field, expected in REQUIRED_META.items():
            require_type(meta, field, expected, "state.meta", errors)
        if isinstance(meta.get("state_version"), int) and meta["state_version"] < 1:
            errors.append("state.meta.state_version: must be >= 1")
        if isinstance(meta.get("turn"), int) and meta["turn"] < 0:
            errors.append("state.meta.turn: must be >= 0")

    player = state.get("player", {})
    if isinstance(player, dict):
        for field, expected in REQUIRED_PLAYER.items():
            require_type(player, field, expected, "state.player", errors)

    characters = state.get("characters", {})
    if isinstance(characters, dict):
        for char_id, character in characters.items():
            if not isinstance(character, dict):
                errors.append(f"state.characters.{char_id}: expected object")
                continue
            for field, expected in REQUIRED_CHARACTER.items():
                require_type(character, field, expected, f"state.characters.{char_id}", errors)
            if isinstance(character.get("trust"), int) and not -10 <= character["trust"] <= 10:
                errors.append(f"state.characters.{char_id}.trust: must be between -10 and 10")
            if isinstance(character.get("fear"), int) and not 0 <= character["fear"] <= 10:
                errors.append(f"state.characters.{char_id}.fear: must be between 0 and 10")

    last_turn = state.get("last_turn", {})
    if isinstance(last_turn, dict):
        for field, expected in {
            "turn": int,
            "player_message": str,
            "narrator_response": str,
            "state_patch_id": str,
        }.items():
            require_type(last_turn, field, expected, "state.last_turn", errors)

    return errors


def pointer_parts(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValidationError(f"patch path must start with '/': {path}")
    if path == "/":
        raise ValidationError("root-level patch is not allowed")
    return [part.replace("~1", "/").replace("~0", "~") for part in path.strip("/").split("/")]


def resolve_parent(document: Any, path: str) -> tuple[Any, str]:
    parts = pointer_parts(path)
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as exc:
                raise ValidationError(f"patch path parent does not exist: {path}") from exc
        elif isinstance(current, dict):
            if part not in current:
                raise ValidationError(f"patch path parent does not exist: {path}")
            current = current[part]
        else:
            raise ValidationError(f"patch path parent is not a container: {path}")
    return current, parts[-1]


def target_exists(document: Any, path: str) -> bool:
    try:
        parent, key = resolve_parent(document, path)
        if isinstance(parent, list):
            return key != "-" and key.isdigit() and int(key) < len(parent)
        if isinstance(parent, dict):
            return key in parent
        return False
    except ValidationError:
        return False


def apply_operation(document: Any, operation: dict[str, Any]) -> None:
    op = operation["op"]
    path = operation["path"]
    parent, key = resolve_parent(document, path)

    if op == "add":
        value = operation["value"]
        if isinstance(parent, list):
            if key == "-":
                parent.append(value)
            else:
                parent.insert(int(key), value)
        elif isinstance(parent, dict):
            parent[key] = value
        else:
            raise ValidationError(f"add target parent is not a container: {path}")
    elif op == "replace":
        if not target_exists(document, path):
            raise ValidationError(f"replace target does not exist: {path}")
        if isinstance(parent, list):
            parent[int(key)] = operation["value"]
        else:
            parent[key] = operation["value"]
    elif op == "remove":
        if not target_exists(document, path):
            raise ValidationError(f"remove target does not exist: {path}")
        if isinstance(parent, list):
            del parent[int(key)]
        else:
            del parent[key]
    else:
        raise ValidationError(f"unsupported op: {op}")


def semantic_guard(state: dict[str, Any], operation: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    path = operation.get("path", "")
    op = operation.get("op")
    value = operation.get("value")
    reason = str(operation.get("reason", "")).lower()

    if path.startswith("/meta/"):
        errors.append(f"{path}: model patches must not edit meta directly")

    parts = path.strip("/").split("/")
    if len(parts) == 3 and parts[0] == "characters" and parts[2] == "status" and op == "replace":
        char_id = parts[1].replace("~1", "/").replace("~0", "~")
        current = state.get("characters", {}).get(char_id, {}).get("status")
        if current == "dead" and value == "alive" and "confirmed resurrection" not in reason:
            errors.append(f"{path}: dead NPC cannot become alive without confirmed resurrection mechanism")

    if len(parts) >= 3 and parts[0] == "player" and parts[1] == "resources" and op in {"add", "replace"}:
        resource_id = parts[2].replace("~1", "/").replace("~0", "~")
        resource = state.get("resources", {}).get(resource_id)
        if resource and isinstance(value, (int, float)) and value > 0:
            quantity = resource.get("quantity")
            if isinstance(quantity, (int, float)) and quantity <= 0:
                errors.append(f"{path}: player cannot gain unavailable resource without confirmed acquisition")

    return errors


def validate_patch_document(proposal: Any, state: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
    errors: list[str] = []
    if not isinstance(proposal, dict):
        return ["patch document: expected object"], None

    if not isinstance(proposal.get("turn"), int):
        errors.append("patch.turn: expected integer")
    if not isinstance(proposal.get("patch"), list):
        errors.append("patch.patch: expected array")
    if not isinstance(proposal.get("uncertain_facts", []), list):
        errors.append("patch.uncertain_facts: expected array")
    if not isinstance(proposal.get("contradictions", []), list):
        errors.append("patch.contradictions: expected array")

    if errors:
        return errors, None

    candidate = copy.deepcopy(state)
    for index, operation in enumerate(proposal["patch"]):
        prefix = f"patch.patch[{index}]"
        if not isinstance(operation, dict):
            errors.append(f"{prefix}: expected object")
            continue
        if operation.get("op") not in ALLOWED_OPS:
            errors.append(f"{prefix}.op: expected one of {sorted(ALLOWED_OPS)}")
        if not isinstance(operation.get("path"), str):
            errors.append(f"{prefix}.path: expected string")
        if not isinstance(operation.get("reason"), str) or not operation.get("reason", "").strip():
            errors.append(f"{prefix}.reason: required non-empty string")
        if not isinstance(operation.get("turn"), int):
            errors.append(f"{prefix}.turn: expected integer")
        if operation.get("op") in {"add", "replace"} and "value" not in operation:
            errors.append(f"{prefix}.value: required for add/replace")
        if errors:
            continue
        errors.extend(f"{prefix}: {message}" for message in semantic_guard(state, operation))
        try:
            apply_operation(candidate, operation)
        except Exception as exc:  # noqa: BLE001 - errors are returned to user
            errors.append(f"{prefix}: {exc}")

    errors.extend(f"candidate.{message}" for message in validate_state(candidate))
    return errors, candidate if not errors else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="state/current.json", help="Path to current state JSON.")
    parser.add_argument("--schema", default="state/schema.json", help="Path to formal schema JSON.")
    parser.add_argument("--patch", help="Optional proposed patch JSON.")
    args = parser.parse_args()

    state_path = Path(args.state)
    schema_path = Path(args.schema)

    try:
        state = load_json(state_path)
        _schema = load_json(schema_path)
        errors = validate_state(state)
        if args.patch:
            proposal = load_json(Path(args.patch))
            patch_errors, _candidate = validate_patch_document(proposal, state)
            errors.extend(patch_errors)
    except ValidationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 1

    print("VALID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

