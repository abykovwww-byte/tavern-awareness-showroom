#!/usr/bin/env python3
"""Run a bounded legacy v1 check and write an authoritative outcome proposal.

The helper is intentionally deterministic when --roll and --check-id are
provided. It does not call an LLM and does not edit state/current.json. State
changes are written as proposed patches for the iteration-2 apply workflow.
It is not part of the rp-core.v2 party turn path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CHECK_TYPES = {
    "persuasion",
    "intimidation",
    "deception",
    "stealth",
    "information",
    "resource",
    "feasibility",
    "trust",
    "conflict",
    "random_event",
}

TARGETED_CHECKS = {"persuasion", "intimidation", "deception", "trust", "conflict"}
SOCIAL_CHECKS = {"persuasion", "intimidation", "deception", "trust"}
RANGE_MIN_MAX = {
    "trust": (-10, 10),
    "suspicion": (0, 10),
    "fear": (0, 10),
    "reputation": (-10, 10),
}


class CheckError(Exception):
    """User-facing check error."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise CheckError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CheckError(f"invalid JSON in {path}: line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp.replace(path)


def append_jsonl(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip())
    value = value.strip("-._")
    return value[:80] or "check"


def check_id_seen(path: Path, check_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("check_id") == check_id:
                return event
    return None


def get_relationship(state: dict[str, Any], actor: str, target: str | None, relation_key: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if not target:
        return None, None
    relationships = state.get("relationships", {})
    candidates = [key for key in [relation_key, f"{actor}_{target}", f"{target}_{actor}", f"player_{target}"] if key]
    for key in candidates:
        relation = relationships.get(key)
        if isinstance(relation, dict):
            return key, relation
    return candidates[0] if candidates else None, None


def get_target_character(state: dict[str, Any], target: str | None) -> dict[str, Any] | None:
    if not target:
        return None
    character = state.get("characters", {}).get(target)
    return character if isinstance(character, dict) else None


def relation_modifier(check_type: str, character: dict[str, Any] | None, relationship: dict[str, Any] | None) -> int:
    trust = 0
    suspicion = 0
    fear = 0

    if character:
        trust += int(character.get("trust", 0))
        fear += int(character.get("fear", 0))
    if relationship:
        trust += int(relationship.get("trust", 0))
        suspicion += int(relationship.get("suspicion", 0))

    if check_type == "persuasion":
        return clamp(round(trust / 3) - round(suspicion / 3), -4, 4)
    if check_type == "intimidation":
        return clamp(round(fear / 3) - round(trust / 5), -4, 4)
    if check_type == "deception":
        return clamp(round(trust / 4) - round(suspicion / 2), -5, 3)
    if check_type == "trust":
        return clamp(round(trust / 4) - round(suspicion / 3), -4, 4)
    if check_type == "conflict":
        return clamp(round(fear / 4), 0, 3)
    return 0


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def outcome_from_score(score: int, roll: int, blocked: bool) -> str:
    if blocked:
        return "failure"
    if roll == 1 and score <= 0:
        return "critical_failure"
    if roll == 20 and score >= 0:
        return "critical_success"
    if score <= -10:
        return "critical_failure"
    if score <= -4:
        return "failure"
    if score <= 0:
        return "failure_with_progress"
    if score <= 5:
        return "partial_success"
    if score <= 14:
        return "success"
    return "critical_success"


def normalize_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 4}


def hard_constraint_blocks(state: dict[str, Any], target: str | None, desired: str, allow_special_state: bool) -> list[str]:
    blockers: list[str] = []
    character = get_target_character(state, target)
    desired_lc = desired.lower()

    if character and character.get("status") in {"dead", "missing", "incapacitated"} and not allow_special_state:
        blockers.append(f"target {target} status is {character.get('status')}")

    if character and "resurrect" in desired_lc and character.get("status") == "dead" and not allow_special_state:
        blockers.append("dead target cannot be restored by check result")

    desired_tokens = normalize_tokens(desired)
    candidate_constraints: list[str] = []
    if character:
        candidate_constraints.extend(str(item) for item in character.get("hard_constraints", []))
    candidate_constraints.extend(str(item.get("text", "")) for item in state.get("world_constraints", []) if isinstance(item, dict))

    for constraint in candidate_constraints:
        constraint_lc = constraint.lower()
        if not any(marker in constraint_lc for marker in ["cannot", "must not", "never", "unavailable", "blocked"]):
            continue
        if desired_tokens and desired_tokens.intersection(normalize_tokens(constraint)):
            blockers.append(constraint)

    return blockers


def numeric_available(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None


def validate_resource(state: dict[str, Any], resource: str | None, amount: float) -> tuple[bool, str, float | None]:
    if not resource:
        return True, "", None
    if amount <= 0:
        return False, "resource amount must be positive", None

    player_value = state.get("player", {}).get("resources", {}).get(resource)
    player_amount = numeric_available(player_value)
    resource_record = state.get("resources", {}).get(resource)

    if resource_record:
        if str(resource_record.get("state", "")).lower() in {"unavailable", "spent", "destroyed", "missing"}:
            return False, f"resource {resource} is {resource_record.get('state')}", player_amount
        quantity = numeric_available(resource_record.get("quantity"))
        if quantity is not None and quantity < amount and player_amount is None:
            return False, f"resource {resource} quantity is below requested amount", quantity

    if player_amount is None:
        return False, f"player does not have resource {resource}", None
    if player_amount < amount:
        return False, f"player resource {resource} would become negative", player_amount
    return True, "", player_amount


def relationship_delta(check_type: str, result: str) -> tuple[int, int]:
    if check_type not in SOCIAL_CHECKS:
        return 0, 0
    if result == "critical_failure":
        return -2, 2
    if result == "failure":
        return -1, 1
    if result == "failure_with_progress":
        return 0, 1
    if result == "partial_success":
        return 1, 0
    if result == "success":
        return 1, -1
    if result == "critical_success":
        return 2, -1
    return 0, 0


def consequences(check_type: str, result: str, target: str | None, resource: str | None, blocked_reasons: list[str]) -> list[str]:
    if blocked_reasons:
        return [f"check is blocked by hard constraint: {reason}" for reason in blocked_reasons]

    label = target or "scene"
    if result == "critical_failure":
        return [f"{label} does not grant the desired outcome", "the attempt creates a lasting complication"]
    if result == "failure":
        return [f"{label} does not grant the desired outcome", "the situation becomes more cautious or costly"]
    if result == "failure_with_progress":
        return [f"{label} does not grant the desired outcome", "one narrow lead or limited opening remains"]
    if result == "partial_success":
        return [f"{label} grants a limited, conditional, or delayed benefit"]
    if result == "success":
        return [f"{label} grants the bounded desired outcome"]
    if result == "critical_success":
        return [f"{label} grants the best bounded version of the desired outcome", "hard world constraints still apply"]
    if check_type == "resource" and resource:
        return [f"resource {resource} is checked before it can affect state"]
    return ["outcome is fixed before narration"]


def forbidden_reinterpretations(desired: str, blocked_reasons: list[str]) -> list[str]:
    items = [
        "do not change the Result field",
        "do not add an equivalent hidden success",
        "do not bypass hard world constraints",
    ]
    if desired:
        items.append(f"do not silently grant '{desired}' beyond the listed consequences")
    for reason in blocked_reasons:
        items.append(f"do not reinterpret blocked constraint as satisfied: {reason}")
    return items


def render_outcome_block(result: dict[str, Any]) -> str:
    lines = [
        "<AUTHORITATIVE_OUTCOME>",
        f"Check ID: {result['check_id']}",
        f"Action: {result['check_type']}",
        f"Target: {result.get('target') or 'scene'}",
        f"Result: {result['result']}",
        f"Roll: {result['roll']}",
        f"Final score: {result['final_score']}",
        "Consequences:",
    ]
    lines.extend(f"- {item}" for item in result["consequences"])
    lines.append("Forbidden reinterpretations:")
    lines.extend(f"- {item}" for item in result["forbidden_reinterpretations"])
    lines.append("</AUTHORITATIVE_OUTCOME>")
    return "\n".join(lines)


def patch_for_check(
    state: dict[str, Any],
    args: argparse.Namespace,
    result: dict[str, Any],
    resource_available_amount: float | None,
    relation_key: str | None,
    relationship: dict[str, Any] | None,
) -> dict[str, Any]:
    turn = max(int(state.get("meta", {}).get("turn", 0)) + 1, 1)
    participants = [args.actor]
    if args.target:
        participants.append(args.target)

    operations: list[dict[str, Any]] = [
        {
            "op": "add",
            "path": "/timeline/-",
            "value": {
                "turn": turn,
                "event": f"Check {result['check_id']} ({args.check_type}) resolved as {result['result']}.",
                "confirmed": True,
                "participants": participants,
            },
            "reason": "Records the fixed authoritative check outcome before narration.",
            "turn": turn,
        }
    ]

    if args.resource and resource_available_amount is not None and args.consume_resource and not result["blocked_reasons"]:
        new_amount = resource_available_amount - args.resource_amount
        if new_amount < 0:
            raise CheckError(f"resource {args.resource} would become negative")
        value: int | float = int(new_amount) if new_amount.is_integer() else new_amount
        operations.append(
            {
                "op": "replace",
                "path": f"/player/resources/{pointer_escape(args.resource)}",
                "value": value,
                "reason": f"Consumes {args.resource_amount:g} {args.resource} for check {result['check_id']}.",
                "turn": turn,
            }
        )

    trust_delta, suspicion_delta = relationship_delta(args.check_type, result["result"])
    if relation_key and args.target and (trust_delta or suspicion_delta):
        if relationship:
            current_trust = int(relationship.get("trust", 0))
            current_suspicion = int(relationship.get("suspicion", 0))
            new_trust = clamp(current_trust + trust_delta, *RANGE_MIN_MAX["trust"])
            new_suspicion = clamp(current_suspicion + suspicion_delta, *RANGE_MIN_MAX["suspicion"])
            if new_trust != current_trust:
                operations.append(
                    {
                        "op": "replace",
                        "path": f"/relationships/{pointer_escape(relation_key)}/trust",
                        "value": new_trust,
                        "reason": f"Bounded trust delta from check {result['check_id']}.",
                        "turn": turn,
                    }
                )
            if new_suspicion != current_suspicion:
                operations.append(
                    {
                        "op": "replace",
                        "path": f"/relationships/{pointer_escape(relation_key)}/suspicion",
                        "value": new_suspicion,
                        "reason": f"Bounded suspicion delta from check {result['check_id']}.",
                        "turn": turn,
                    }
                )
        else:
            operations.append(
                {
                    "op": "add",
                    "path": f"/relationships/{pointer_escape(relation_key)}",
                    "value": {
                        "from": args.actor,
                        "to": args.target,
                        "trust": clamp(trust_delta, *RANGE_MIN_MAX["trust"]),
                        "suspicion": clamp(suspicion_delta, *RANGE_MIN_MAX["suspicion"]),
                        "notes": [f"Created by check {result['check_id']}."],
                    },
                    "reason": f"Creates bounded relationship record from check {result['check_id']}.",
                    "turn": turn,
                }
            )

    return {
        "turn": turn,
        "check_id": result["check_id"],
        "source": "scripts/run-check.py",
        "patch": operations,
        "uncertain_facts": [],
        "contradictions": result["blocked_reasons"],
    }


def make_check_id(args: argparse.Namespace) -> str:
    if args.check_id:
        return slug(args.check_id)
    seed = f"{args.check_type}-{args.actor}-{args.target or 'scene'}-{now()}-{random.SystemRandom().randint(1000, 9999)}"
    return slug(seed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="state/current.json")
    parser.add_argument("--checks-log", default="state/checks.log")
    parser.add_argument("--last-check", default="state/last-check.json")
    parser.add_argument("--proposed-dir", default="state/proposed")
    parser.add_argument("--type", dest="check_type", required=True, choices=sorted(CHECK_TYPES))
    parser.add_argument("--actor", default="player")
    parser.add_argument("--target")
    parser.add_argument("--relation-key")
    parser.add_argument("--difficulty", type=int, default=10)
    parser.add_argument("--skill", type=int, default=0)
    parser.add_argument("--preparation", type=int, default=0)
    parser.add_argument("--leverage", type=int, default=0)
    parser.add_argument("--roll", type=int)
    parser.add_argument("--check-id")
    parser.add_argument("--desired-outcome", default="")
    parser.add_argument("--action-detail", default="")
    parser.add_argument("--resource")
    parser.add_argument("--resource-amount", type=float, default=1.0)
    parser.add_argument("--no-consume-resource", dest="consume_resource", action="store_false")
    parser.add_argument("--allow-special-state", action="store_true")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state_path = Path(args.state)
    checks_log = Path(args.checks_log)
    last_check = Path(args.last_check)
    proposed_dir = Path(args.proposed_dir)
    check_id = make_check_id(args)

    if args.roll is not None and not 1 <= args.roll <= 20:
        print("--roll must be between 1 and 20", file=sys.stderr)
        return 2

    try:
        state = load_json(state_path)
        seen = check_id_seen(checks_log, check_id)
        if seen:
            duplicate_result = {
                "duplicate": True,
                "check_id": check_id,
                "previous_result": seen.get("result"),
                "previous_proposed_patch": seen.get("proposed_patch"),
            }
            if args.format == "json":
                print(json.dumps(duplicate_result, ensure_ascii=False, indent=2))
            else:
                print(f"Duplicate check id: {check_id}")
                print(f"Previous result: {seen.get('result')}")
                if seen.get("proposed_patch"):
                    print(f"Previous proposed patch: {seen.get('proposed_patch')}")
            return 0

        if args.check_type in TARGETED_CHECKS and not args.target:
            raise CheckError(f"{args.check_type} requires --target")
        if args.check_type == "resource" and not args.resource:
            raise CheckError("resource checks require --resource")

        character = get_target_character(state, args.target)
        relation_key, relationship = get_relationship(state, args.actor, args.target, args.relation_key)
        rel_mod = relation_modifier(args.check_type, character, relationship)
        roll = args.roll if args.roll is not None else random.SystemRandom().randint(1, 20)
        final_score = args.skill + args.preparation + args.leverage + rel_mod + roll - args.difficulty

        blocked_reasons = hard_constraint_blocks(state, args.target, args.desired_outcome, args.allow_special_state)
        if args.resource or args.check_type == "resource":
            ok, reason, resource_amount = validate_resource(state, args.resource, args.resource_amount)
            if not ok:
                blocked_reasons.append(reason)
        else:
            resource_amount = None

        result_name = outcome_from_score(final_score, roll, bool(blocked_reasons))
        action_hash = hashlib.sha256(args.action_detail.encode("utf-8")).hexdigest()[:16] if args.action_detail else ""
        result = {
            "timestamp": now(),
            "check_id": check_id,
            "check_type": args.check_type,
            "actor": args.actor,
            "target": args.target,
            "result": result_name,
            "roll": roll,
            "difficulty": args.difficulty,
            "modifiers": {
                "skill": args.skill,
                "preparation": args.preparation,
                "leverage": args.leverage,
                "relation": rel_mod,
            },
            "final_score": final_score,
            "desired_outcome": args.desired_outcome,
            "blocked_reasons": blocked_reasons,
            "action_detail_sha256_16": action_hash,
            "consequences": consequences(args.check_type, result_name, args.target, args.resource, blocked_reasons),
            "forbidden_reinterpretations": forbidden_reinterpretations(args.desired_outcome, blocked_reasons),
        }
        result["authoritative_outcome"] = render_outcome_block(result)

        proposal = patch_for_check(state, args, result, resource_amount, relation_key, relationship)
        proposal_path = proposed_dir / f"check-{check_id}.json"
        write_json_atomic(proposal_path, proposal)
        result["proposed_patch"] = str(proposal_path)

        write_json_atomic(last_check, result)
        append_jsonl(
            checks_log,
            {
                "timestamp": result["timestamp"],
                "event": "run_check",
                "check_id": check_id,
                "check_type": args.check_type,
                "result": result_name,
                "roll": roll,
                "difficulty": args.difficulty,
                "modifiers": result["modifiers"],
                "final_score": final_score,
                "blocked_reasons": blocked_reasons,
                "proposed_patch": str(proposal_path),
                "action_detail_sha256_16": action_hash,
            },
        )

    except CheckError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["authoritative_outcome"])
        print(f"\nProposed patch: {proposal_path}")
        print("Preview/apply with scripts/apply-state-patch.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
