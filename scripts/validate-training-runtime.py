#!/usr/bin/env python3
"""Validate WorldPack-owned training runtime contracts before IaC deployment."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


RUNTIME_SCHEMA = "rp-training-runtime.v3"
PROGRAM_SCHEMA = "rp-training-program.v3"
RUNTIME_PROGRAM_SCHEMAS = {
    "rp-training-runtime.v1": "rp-training-program.v1",
    "rp-training-runtime.v2": "rp-training-program.v2",
    RUNTIME_SCHEMA: PROGRAM_SCHEMA,
}
ASSESSMENT_SCHEMA = "rp-training-assessment.v1"
FALLBACKS_SCHEMA = "rp-training-fallbacks.v1"
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


class ContractError(ValueError):
    pass


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label}: cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"{label}: {path} must contain a JSON object")
    return value


def contract_path(root: Path, declaration: dict[str, Any], key: str, required: bool = True) -> Path | None:
    relative = declaration.get(key)
    if relative is None and not required:
        return None
    if not isinstance(relative, str) or not relative.strip():
        raise ContractError(f"training_runtime.{key} must be a non-empty relative path")
    target = (root / relative).resolve()
    if root.resolve() not in target.parents:
        raise ContractError(f"training_runtime.{key} escapes the WorldPack root")
    if target.suffix.casefold() != ".json":
        raise ContractError(f"training_runtime.{key} must reference JSON")
    return target


def compile_regex(pattern: Any, context: str) -> None:
    if not isinstance(pattern, str) or not pattern:
        raise ContractError(f"{context}: regex must be a non-empty string")
    try:
        re.compile(pattern, re.IGNORECASE | re.DOTALL)
    except re.error as exc:
        raise ContractError(f"{context}: invalid regex: {exc}") from exc


def validate_expression(expression: Any, detector_ids: set[str], context: str) -> None:
    if isinstance(expression, bool):
        return
    if isinstance(expression, str):
        if expression not in detector_ids:
            raise ContractError(f"{context}: unknown detector {expression}")
        return
    if not isinstance(expression, dict) or len(expression) != 1:
        raise ContractError(f"{context}: invalid detector expression")
    operator, value = next(iter(expression.items()))
    if operator in {"all", "any"} and isinstance(value, list) and value:
        for item in value:
            validate_expression(item, detector_ids, context)
        return
    if operator == "not":
        validate_expression(value, detector_ids, context)
        return
    raise ContractError(f"{context}: unsupported detector expression")


def validate_placeholders(template: Any, resources: set[str], context: str) -> None:
    if not isinstance(template, str) or not template.strip():
        raise ContractError(f"{context}: fallback must be a non-empty string")
    for match in PLACEHOLDER_RE.finditer(template):
        key = match.group(1).strip()
        if key in {"player.name", "player.description", "role.task", "artifact.url"}:
            continue
        if key.startswith("resource.") and key.removeprefix("resource.") in resources:
            continue
        raise ContractError(f"{context}: unsupported fallback placeholder {key}")


def validate_worldpack(root: Path) -> bool:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return False
    manifest = load_json(manifest_path, root.name)
    declaration = manifest.get("training_runtime")
    if declaration is None:
        return False
    runtime_schema = declaration.get("schema_version") if isinstance(declaration, dict) else None
    if runtime_schema not in RUNTIME_PROGRAM_SCHEMAS:
        raise ContractError(f"{root.name}: unsupported training_runtime schema")

    program_path = contract_path(root, declaration, "program")
    assessment_path = contract_path(root, declaration, "assessment")
    fallbacks_path = contract_path(root, declaration, "fallbacks", required=False)
    program = load_json(program_path, root.name)
    assessment = load_json(assessment_path, root.name)
    state_path = root / str(manifest.get("files", {}).get("state_seed", "state-seed.json"))
    state = load_json(state_path, root.name)
    resources_value = state.get("player", {}).get("resources", {})
    if not isinstance(resources_value, dict):
        raise ContractError(f"{root.name}: state seed player.resources must be an object")
    resources = set(resources_value)

    if program.get("schema_version") != RUNTIME_PROGRAM_SCHEMAS[runtime_schema]:
        raise ContractError(f"{root.name}: unsupported training program schema")
    if assessment.get("schema_version") != ASSESSMENT_SCHEMA:
        raise ContractError(f"{root.name}: unsupported training assessment schema")
    if fallbacks_path:
        fallbacks = load_json(fallbacks_path, root.name)
        if fallbacks.get("schema_version") != FALLBACKS_SCHEMA:
            raise ContractError(f"{root.name}: unsupported training fallbacks schema")

    is_v3 = runtime_schema == "rp-training-runtime.v3"
    if is_v3 and (not isinstance(program.get("revision"), int) or int(program["revision"]) < 1):
        raise ContractError(f"{root.name}: program.revision must be a positive integer")

    progression = program.get("progression")
    if not isinstance(progression, dict):
        raise ContractError(f"{root.name}: program.progression must be an object")
    total_turns = int(progression.get("total_turns", 0) or 0)
    if total_turns < 1:
        raise ContractError(f"{root.name}: progression.total_turns must be positive")
    for key in ("current_window_resource", "turns_remaining_resource", "completion_status_resource"):
        resource = progression.get(key)
        if resource not in resources:
            raise ContractError(f"{root.name}: progression resource {resource!r} is absent from state seed")
    if is_v3 and (
        not isinstance(progression.get("complete_value"), str)
        or not progression["complete_value"].strip()
    ):
        raise ContractError(f"{root.name}: progression.complete_value must be a non-empty string")

    turns = program.get("turns")
    if not isinstance(turns, list) or len(turns) != total_turns:
        raise ContractError(f"{root.name}: program turn count must equal progression.total_turns")
    if [item.get("turn") for item in turns if isinstance(item, dict)] != list(range(1, total_turns + 1)):
        raise ContractError(f"{root.name}: turns must be ordered and contiguous from 1")
    global_validation = program.get("global_validation", {})
    if not isinstance(global_validation, dict):
        raise ContractError(f"{root.name}: global_validation must be an object")
    for pattern in global_validation.get("forbidden_patterns", []):
        compile_regex(pattern, f"{root.name}: global_validation")
    for adapter in program.get("role_adapters", []):
        if not isinstance(adapter, dict) or not isinstance(adapter.get("task"), str):
            raise ContractError(f"{root.name}: role adapter must contain a task")
        patterns = adapter.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            raise ContractError(f"{root.name}: role adapter must contain patterns")
        for pattern in patterns:
            compile_regex(pattern, f"{root.name}: role adapter")
    for turn in turns:
        for key in ("window", "header", "instruction"):
            if not isinstance(turn.get(key), str) or not turn[key].strip():
                raise ContractError(f"{root.name}: turn {turn.get('turn')} requires {key}")
        if is_v3:
            surfaces = turn.get("surfaces")
            if not isinstance(surfaces, list) or not surfaces:
                raise ContractError(f"{root.name}: turn {turn.get('turn')} requires non-empty surfaces")
            if not isinstance(turn.get("require_question"), bool):
                raise ContractError(f"{root.name}: turn {turn.get('turn')} requires boolean require_question")
            if not isinstance(turn.get("question"), str):
                raise ContractError(f"{root.name}: turn {turn.get('turn')} requires question")
            validate_placeholders(turn.get("fallback"), resources, f"{root.name}: turn {turn.get('turn')}")
        else:
            surface = turn.get("surface")
            if not isinstance(surface, dict):
                raise ContractError(f"{root.name}: turn {turn.get('turn')} requires email or messenger surface")
            surfaces = [surface]
        surface_types = [surface.get("type") for surface in surfaces if isinstance(surface, dict)]
        if len(surface_types) != len(surfaces) or any(
            surface_type not in {"email", "messenger"} for surface_type in surface_types
        ):
            raise ContractError(f"{root.name}: turn {turn.get('turn')} requires email or messenger surfaces")
        if len(set(surface_types)) != len(surface_types):
            raise ContractError(f"{root.name}: turn {turn.get('turn')} surface types must be unique")
        require_question = turn.get("require_question", False) if is_v3 else surfaces[0].get("require_question")
        if require_question and (
            not isinstance(turn.get("question"), str) or not turn["question"].strip()
        ):
            raise ContractError(f"{root.name}: turn {turn.get('turn')} requires a question")
        variation_budget = turn.get("variation_budget", [])
        if not isinstance(variation_budget, list) or any(
            not isinstance(item, str) or not item.strip() for item in variation_budget
        ):
            raise ContractError(f"{root.name}: turn {turn.get('turn')} variation_budget must contain non-empty strings")
        for surface in surfaces:
            if is_v3 and ("fallback" in surface or "require_question" in surface):
                raise ContractError(
                    f"{root.name}: turn {turn.get('turn')} keeps fallback and require_question at turn level"
                )
            if surface.get("links", "none") not in {"none", "artifact"}:
                raise ContractError(f"{root.name}: turn {turn.get('turn')} has unsupported links policy")
            if is_v3 and "links" not in surface:
                raise ContractError(f"{root.name}: turn {turn.get('turn')} surface requires links policy")
            count = surface.get("count", 1)
            if (is_v3 and "count" not in surface) or not isinstance(count, int) or count < 1:
                raise ContractError(f"{root.name}: turn {turn.get('turn')} surface count must be positive")
            must_include = surface.get("must_include", [])
            if not isinstance(must_include, list) or any(
                not isinstance(item, str) or not item.strip() for item in must_include
            ):
                raise ContractError(f"{root.name}: turn {turn.get('turn')} surface.must_include must contain non-empty strings")
            for pattern in [*surface.get("required_patterns", []), *surface.get("forbidden_patterns", [])]:
                compile_regex(pattern, f"{root.name}: turn {turn.get('turn')}")
            if not is_v3:
                validate_placeholders(surface.get("fallback"), resources, f"{root.name}: turn {turn.get('turn')}")

    debrief = program.get("debrief")
    if not isinstance(debrief, dict):
        raise ContractError(f"{root.name}: program.debrief must be an object")
    for key in ("header", "instruction"):
        if not isinstance(debrief.get(key), str) or not debrief[key].strip():
            raise ContractError(f"{root.name}: debrief requires {key}")
    validate_placeholders(debrief.get("fallback"), resources, f"{root.name}: debrief")
    for score in debrief.get("scores", []):
        if not isinstance(score, dict) or score.get("resource") not in resources:
            raise ContractError(f"{root.name}: debrief score references an unknown state resource")
        if not isinstance(score.get("max"), int) or int(score["max"]) < 1:
            raise ContractError(f"{root.name}: debrief score requires a positive integer max")
        if not isinstance(score.get("label"), str) or not score["label"].strip():
            raise ContractError(f"{root.name}: debrief score requires a label")
    for resource in debrief.get("evidence_resources", []):
        if resource not in resources:
            raise ContractError(f"{root.name}: debrief evidence references unknown resource {resource}")

    detectors = assessment.get("detectors")
    if not isinstance(detectors, dict):
        raise ContractError(f"{root.name}: assessment.detectors must be an object")
    detector_ids = set(detectors)
    for detector_id, spec in detectors.items():
        if not SAFE_ID_RE.fullmatch(str(detector_id)) or not isinstance(spec, dict):
            raise ContractError(f"{root.name}: invalid detector {detector_id}")
        kind = spec.get("type")
        if kind in {"text_regex", "text_regex_count"}:
            patterns = spec.get("patterns")
            if not isinstance(patterns, list) or not patterns:
                raise ContractError(f"{root.name}: detector {detector_id} requires patterns")
            for pattern in patterns:
                compile_regex(pattern, f"{root.name}: detector {detector_id}")
            exclusions = spec.get("exclude_patterns", [])
            if not isinstance(exclusions, list):
                raise ContractError(
                    f"{root.name}: detector {detector_id} exclude_patterns must be a list"
                )
            for pattern in exclusions:
                compile_regex(pattern, f"{root.name}: detector {detector_id} exclusion")
        elif kind == "expression":
            validate_expression(spec.get("value"), detector_ids, f"{root.name}: detector {detector_id}")
        elif kind not in {"interaction_event", "profile_overlap"}:
            raise ContractError(f"{root.name}: unsupported detector type {kind}")

    rule_ids: set[str] = set()
    rules = assessment.get("rules")
    if not isinstance(rules, list):
        raise ContractError(f"{root.name}: assessment.rules must be a list")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ContractError(f"{root.name}: assessment rule must be an object")
        rule_id = str(rule.get("id") or "")
        if not SAFE_ID_RE.fullmatch(rule_id) or rule_id in rule_ids:
            raise ContractError(f"{root.name}: invalid or duplicate rule {rule_id}")
        rule_ids.add(rule_id)
        validate_expression(rule.get("when", True), detector_ids, f"{root.name}: rule {rule_id}")
        rule_turns = rule.get("turns", "*")
        if rule_turns != "*" and (
            not isinstance(rule_turns, list)
            or any(not isinstance(turn, int) or turn < 1 for turn in rule_turns)
        ):
            raise ContractError(f"{root.name}: rule {rule_id} has invalid turns")
        effects = rule.get("effects")
        if not isinstance(effects, list) or not effects:
            raise ContractError(f"{root.name}: rule {rule_id} requires effects")
        for effect in effects:
            if not isinstance(effect, dict) or len(effect) != 1:
                raise ContractError(f"{root.name}: rule {rule_id} has an invalid effect")
            kind, spec = next(iter(effect.items()))
            if kind not in {"increment", "set", "append_evidence"} or not isinstance(spec, dict):
                raise ContractError(f"{root.name}: rule {rule_id} has unsupported effect {kind}")
            resource = spec.get("resource")
            if resource not in resources:
                raise ContractError(f"{root.name}: rule {rule_id} references unknown resource {resource}")
            if kind == "increment":
                try:
                    int(spec["value"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise ContractError(
                        f"{root.name}: rule {rule_id} increment requires an integer value"
                    ) from exc
            if kind == "append_evidence":
                labels = spec.get("labels", {})
                if not isinstance(labels, dict) or any(
                    detector_id not in detector_ids or not isinstance(label, str)
                    for detector_id, label in labels.items()
                ):
                    raise ContractError(f"{root.name}: rule {rule_id} has invalid evidence labels")
                if "fallback" in spec and not isinstance(spec["fallback"], str):
                    raise ContractError(f"{root.name}: rule {rule_id} has invalid evidence fallback")

    aggregates = assessment.get("aggregates", {})
    if not isinstance(aggregates, dict):
        raise ContractError(f"{root.name}: assessment.aggregates must be an object")
    for resource, aggregate in aggregates.items():
        if resource not in resources or not isinstance(aggregate, dict):
            raise ContractError(f"{root.name}: aggregate references unknown resource {resource}")
        components = aggregate.get("bounded_sum")
        if not isinstance(components, list) or not components:
            raise ContractError(f"{root.name}: aggregate {resource} requires bounded_sum inputs")
        for component in components:
            if component not in resources:
                raise ContractError(f"{root.name}: aggregate {resource} references unknown component {component}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worldpacks", type=Path, required=True)
    args = parser.parse_args()
    root = args.worldpacks.resolve()
    if not root.is_dir():
        print(f"training runtime validation failed: worldpacks directory not found: {root}", file=sys.stderr)
        return 2
    validated = 0
    errors: list[str] = []
    for candidate in sorted(path for path in root.iterdir() if path.is_dir()):
        try:
            validated += int(validate_worldpack(candidate))
        except ContractError as exc:
            errors.append(str(exc))
    if errors:
        for error in errors:
            print(f"training runtime validation failed: {error}", file=sys.stderr)
        return 1
    print(f"validated {validated} WorldPack training runtime contract(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
