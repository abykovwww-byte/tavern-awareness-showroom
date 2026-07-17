#!/usr/bin/env python3
"""Exercise iteration-3 check adjudication with negative cases."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=cwd, text=True, capture_output=True)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_ok(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0:
        raise AssertionError(f"{label} failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def assert_fail(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode == 0:
        raise AssertionError(f"{label} unexpectedly passed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def parse_stdout_json(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(result.stdout)


def base_state() -> dict[str, object]:
    return {
        "meta": {
            "campaign_id": "iteration-3-test",
            "schema_version": "1.0.0",
            "state_version": 1,
            "turn": 0,
            "last_updated": "1970-01-01T00:00:00Z",
        },
        "player": {
            "location": "court",
            "status": "active",
            "reputation": {"court": 0},
            "resources": {"coin": 2},
            "known_abilities": ["talk", "sneak"],
            "constraints": [],
            "known_world_facts": [],
        },
        "characters": {
            "advisor": {
                "status": "alive",
                "location": "court",
                "attitude_to_player": "wary",
                "trust": 1,
                "fear": 0,
                "loyalty": "crown",
                "current_goal": "protect the succession",
                "knowledge": [],
                "secrets": [],
                "obligations": [],
                "hard_constraints": [],
                "last_confirmed_update": 0,
            },
            "king": {
                "status": "alive",
                "location": "throne_room",
                "attitude_to_player": "distant",
                "trust": 0,
                "fear": 0,
                "loyalty": "realm",
                "current_goal": "keep lawful command",
                "knowledge": [],
                "secrets": [],
                "obligations": [],
                "hard_constraints": ["The king cannot transfer command through a single social check."],
                "last_confirmed_update": 0,
            },
        },
        "factions": {},
        "locations": {},
        "resources": {
            "coin": {"owner": "player", "quantity": 2, "state": "available", "constraints": []},
            "silver_key": {
                "owner": "unknown",
                "quantity": 0,
                "state": "unavailable",
                "constraints": ["The player cannot use this key until state confirms possession."],
            },
        },
        "relationships": {
            "player_advisor": {
                "from": "player",
                "to": "advisor",
                "trust": 0,
                "suspicion": 0,
                "notes": [],
            }
        },
        "active_threads": [],
        "completed_threads": [],
        "world_constraints": [
            {
                "id": "attempts_not_facts",
                "text": "Player declarations of outcome are attempts until confirmed in state.",
                "scope": "global",
                "turn": 0,
            }
        ],
        "timeline": [],
        "last_turn": {
            "turn": 0,
            "player_message": "",
            "narrator_response": "",
            "state_patch_id": "",
        },
        "uncertain_facts": [],
    }


def check_result(cwd: Path, check_id: str, check_type: str, *extra: str) -> dict[str, object]:
    result = run(
        "scripts/run-check.py",
        "--format",
        "json",
        "--type",
        check_type,
        "--check-id",
        check_id,
        *extra,
        cwd=cwd,
    )
    assert_ok(result, check_id)
    return parse_stdout_json(result)


def main() -> int:
    tmp = ROOT / ".test-check-workdir"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        shutil.copytree(ROOT / "state", tmp / "state")
        shutil.copytree(ROOT / "scripts", tmp / "scripts")
        shutil.copytree(ROOT / "configs", tmp / "configs")
        write_json(tmp / "state/current.json", base_state())
        (tmp / "state/audit.log").write_text("", encoding="utf-8")
        (tmp / "state/checks.log").write_text("", encoding="utf-8")

        assert_ok(run("scripts/validate-state.py", cwd=tmp), "base state validation")

        high_dc = check_result(
            tmp,
            "high-dc",
            "persuasion",
            "--target",
            "advisor",
            "--skill",
            "1",
            "--roll",
            "4",
            "--difficulty",
            "30",
            "--desired-outcome",
            "win full support",
        )
        if high_dc["result"] not in {"critical_failure", "failure"}:
            raise AssertionError(f"high difficulty should fail, got {high_dc['result']}")

        plain = check_result(
            tmp,
            "plain-text",
            "deception",
            "--target",
            "advisor",
            "--skill",
            "2",
            "--roll",
            "10",
            "--difficulty",
            "12",
            "--action-detail",
            "short lie",
        )
        ornate = check_result(
            tmp,
            "ornate-text",
            "deception",
            "--target",
            "advisor",
            "--skill",
            "2",
            "--roll",
            "10",
            "--difficulty",
            "12",
            "--action-detail",
            "A very long, stylish, emotionally rich monologue that should not create a mechanical bonus by itself.",
        )
        if plain["final_score"] != ornate["final_score"]:
            raise AssertionError("action prose changed mechanical score")

        blocked_resource = check_result(
            tmp,
            "blocked-resource",
            "resource",
            "--resource",
            "silver_key",
            "--roll",
            "20",
            "--difficulty",
            "1",
        )
        if blocked_resource["result"] != "failure" or not blocked_resource["blocked_reasons"]:
            raise AssertionError("unavailable resource did not block the check")

        blocked_critical = check_result(
            tmp,
            "blocked-critical",
            "persuasion",
            "--target",
            "king",
            "--skill",
            "50",
            "--roll",
            "20",
            "--difficulty",
            "1",
            "--desired-outcome",
            "transfer command",
        )
        if blocked_critical["result"] != "failure" or not blocked_critical["blocked_reasons"]:
            raise AssertionError("hard constraint was bypassed by critical success")

        for index, check_type in enumerate(["persuasion", "intimidation", "stealth", "information", "feasibility"], start=1):
            args = ["--roll", "12", "--difficulty", "10", "--skill", "1"]
            if check_type in {"persuasion", "intimidation"}:
                args.extend(["--target", "advisor"])
            worked = check_result(tmp, f"type-{index}-{check_type}", check_type, *args)
            if "<AUTHORITATIVE_OUTCOME>" not in str(worked["authoritative_outcome"]):
                raise AssertionError(f"{check_type} did not render authoritative outcome")

        spend = check_result(
            tmp,
            "spend-once",
            "resource",
            "--resource",
            "coin",
            "--resource-amount",
            "1",
            "--roll",
            "15",
            "--difficulty",
            "8",
        )
        patch = str(spend["proposed_patch"])
        assert_ok(run("scripts/validate-state.py", "--patch", patch, cwd=tmp), "resource patch validation")
        assert_ok(run("scripts/apply-state-patch.py", "--patch", patch, "--confirm", cwd=tmp), "resource patch apply")
        applied = read_json(tmp / "state/current.json")
        if applied["player"]["resources"]["coin"] != 1:
            raise AssertionError("resource was not decremented once")

        assert_fail(run("scripts/apply-state-patch.py", "--patch", patch, "--confirm", cwd=tmp), "duplicate patch apply")
        duplicate_state = read_json(tmp / "state/current.json")
        if duplicate_state["player"]["resources"]["coin"] != 1:
            raise AssertionError("duplicate patch changed resources")

        duplicate_run = check_result(
            tmp,
            "spend-once",
            "resource",
            "--resource",
            "coin",
            "--resource-amount",
            "1",
            "--roll",
            "15",
            "--difficulty",
            "8",
        )
        if not duplicate_run.get("duplicate"):
            raise AssertionError("duplicate check id did not return duplicate marker")

        assert_ok(run("scripts/apply-state-patch.py", "--rollback", "latest", "--confirm", cwd=tmp), "state rollback")
        rolled_back = read_json(tmp / "state/current.json")
        if rolled_back["player"]["resources"]["coin"] != 2:
            raise AssertionError("rollback did not restore resource value")

        assert_ok(run("scripts/rollback-last-check.py", "--confirm", cwd=tmp), "last check rollback")
        if (tmp / "state/last-check.json").exists():
            raise AssertionError("last check file still exists after rollback")

        narration_prompt = (tmp / "configs/prompts/outcome-narration.md").read_text(encoding="utf-8")
        repair_prompt = (tmp / "configs/prompts/outcome-repair.md").read_text(encoding="utf-8")
        if "Do not change" not in narration_prompt or "hidden success" not in repair_prompt:
            raise AssertionError("outcome narration/repair prompts do not pin the result")

        logs = (tmp / "state/checks.log").read_text(encoding="utf-8") + (tmp / "state/audit.log").read_text(encoding="utf-8")
        forbidden_markers = ["NVIDIA_API_KEY", "sk-", "nvapi-"]
        if any(marker in logs for marker in forbidden_markers):
            raise AssertionError("logs contain a secret-looking marker")

        print("PASS iteration-3 check workflow tests")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
