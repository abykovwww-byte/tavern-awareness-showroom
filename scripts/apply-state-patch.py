#!/usr/bin/env python3
"""Preview, apply, or rollback RP Stack state patches."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def load_validator():
    validator_path = Path(__file__).with_name("validate-state.py")
    spec = importlib.util.spec_from_file_location("state_validator", validator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load validate-state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = load_validator()


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return validator.load_json(path)


def write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    tmp.replace(path)


def append_audit(audit_path: Path, event: dict[str, Any]) -> None:
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def audit_has_check_id(audit_path: Path, check_id: str) -> bool:
    if not audit_path.exists():
        return False
    with audit_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "apply_patch" and event.get("check_id") == check_id:
                return True
    return False


def backup_state(state_path: Path, history_dir: Path, label: str) -> Path:
    state = load_json(state_path)
    meta = state.get("meta", {})
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_name = f"{label}-v{meta.get('state_version', 'unknown')}-turn{meta.get('turn', 'unknown')}-{timestamp}.json"
    history_dir.mkdir(parents=True, exist_ok=True)
    target = history_dir / backup_name
    shutil.copy2(state_path, target)
    return target


def apply_patch(state: dict[str, Any], proposal: dict[str, Any]) -> dict[str, Any]:
    for operation in proposal["patch"]:
        validator.apply_operation(state, operation)

    if proposal.get("uncertain_facts"):
        state.setdefault("uncertain_facts", [])
        for item in proposal["uncertain_facts"]:
            state["uncertain_facts"].append(item)

    meta = state["meta"]
    meta["state_version"] += 1
    meta["turn"] = max(meta["turn"] + 1, proposal["turn"])
    meta["last_updated"] = now()

    state["last_turn"]["turn"] = meta["turn"]
    state["last_turn"]["state_patch_id"] = f"state-v{meta['state_version']}"
    return state


def rollback_state(state_path: Path, history_dir: Path, audit_path: Path, rollback: str, confirm: bool) -> int:
    candidates = sorted(history_dir.glob("*.json"), key=lambda path: path.stat().st_mtime)
    if not candidates:
        print("No history files found for rollback.", file=sys.stderr)
        return 1

    source = candidates[-1] if rollback == "latest" else Path(rollback)
    if not source.exists():
        print(f"Rollback source not found: {source}", file=sys.stderr)
        return 1

    current = load_json(state_path)
    restored = load_json(source)
    errors = validator.validate_state(restored)
    if errors:
        print("Rollback source is invalid:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    restored["meta"]["state_version"] = current["meta"]["state_version"] + 1
    restored["meta"]["turn"] = current["meta"]["turn"] + 1
    restored["meta"]["last_updated"] = now()
    restored["last_turn"]["turn"] = restored["meta"]["turn"]
    restored["last_turn"]["state_patch_id"] = f"rollback:{source.name}"

    if not confirm:
        print(json.dumps({"rollback_source": str(source), "would_restore": restored["meta"]}, ensure_ascii=False, indent=2))
        print("DRY RUN: add --confirm to perform rollback.")
        return 0

    pre_backup = backup_state(state_path, history_dir, "pre-rollback")
    write_json_atomic(state_path, restored)
    append_audit(
        audit_path,
        {
            "timestamp": now(),
            "event": "rollback",
            "source": str(source),
            "pre_rollback_backup": str(pre_backup),
            "new_state_version": restored["meta"]["state_version"],
            "new_turn": restored["meta"]["turn"],
        },
    )
    print(f"Rolled back from {source}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="state/current.json")
    parser.add_argument("--schema", default="state/schema.json")
    parser.add_argument("--patch", help="Proposed patch JSON.")
    parser.add_argument("--history", default="state/history")
    parser.add_argument("--audit", default="state/audit.log")
    parser.add_argument("--confirm", action="store_true", help="Apply changes. Without this flag, only preview.")
    parser.add_argument("--rollback", help="Rollback from 'latest' or a history file path.")
    args = parser.parse_args()

    state_path = Path(args.state)
    schema_path = Path(args.schema)
    history_dir = Path(args.history)
    audit_path = Path(args.audit)

    if args.rollback:
        return rollback_state(state_path, history_dir, audit_path, args.rollback, args.confirm)

    if not args.patch:
        print("--patch is required unless --rollback is used", file=sys.stderr)
        return 2

    try:
        state = load_json(state_path)
        _schema = load_json(schema_path)
        proposal = load_json(Path(args.patch))
    except Exception as exc:  # noqa: BLE001
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    errors = validator.validate_state(state)
    patch_errors, candidate = validator.validate_patch_document(proposal, state)
    errors.extend(patch_errors)
    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 1

    if candidate is None:
        print("Patch did not produce a candidate state.", file=sys.stderr)
        return 1

    check_id = proposal.get("check_id")
    if isinstance(check_id, str) and check_id.strip() and audit_has_check_id(audit_path, check_id):
        print(f"Patch for check_id {check_id} has already been applied.", file=sys.stderr)
        return 1

    if not args.confirm:
        print(json.dumps({"turn": proposal["turn"], "operations": proposal["patch"]}, ensure_ascii=False, indent=2))
        print("DRY RUN: state not changed. Add --confirm to apply.")
        return 0

    backup = backup_state(state_path, history_dir, "pre-patch")
    candidate = apply_patch(candidate, proposal)
    post_errors = validator.validate_state(candidate)
    if post_errors:
        print("Candidate state failed validation after metadata update:", file=sys.stderr)
        for error in post_errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    write_json_atomic(state_path, candidate)
    append_audit(
        audit_path,
        {
            "timestamp": now(),
            "event": "apply_patch",
            "patch_file": args.patch,
            "check_id": check_id,
            "backup": str(backup),
            "ops": len(proposal["patch"]),
            "uncertain_facts": len(proposal.get("uncertain_facts", [])),
            "contradictions": len(proposal.get("contradictions", [])),
            "new_state_version": candidate["meta"]["state_version"],
            "new_turn": candidate["meta"]["turn"],
        },
    )
    print(f"Applied patch. Backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
