#!/usr/bin/env python3
"""Exercise the iteration-2 state workflow with positive and negative cases."""

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


def assert_ok(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0:
        raise AssertionError(f"{label} failed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def assert_fail(result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode == 0:
        raise AssertionError(f"{label} unexpectedly passed\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")


def main() -> int:
    tmp = ROOT / ".test-workdir"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        shutil.copytree(ROOT / "state", tmp / "state")
        shutil.copytree(ROOT / "scripts", tmp / "scripts")
        shutil.copy2(ROOT / "state" / "campaign.example.json", tmp / "state" / "current.json")
        (tmp / "state" / "audit.log").write_text("", encoding="utf-8")

        assert_ok(run("scripts/validate-state.py", cwd=tmp), "base state validation")

        dead_npc_patch = {
            "turn": 1,
            "patch": [
                {
                    "op": "replace",
                    "path": "/characters/king/status",
                    "value": "alive",
                    "reason": "Игрок сказал, что король жив.",
                    "turn": 1,
                }
            ],
            "uncertain_facts": [],
            "contradictions": ["Игрок объявил мертвого NPC живым без подтвержденного механизма."],
        }
        write_json(tmp / "state/proposed/dead-npc.json", dead_npc_patch)
        assert_fail(
            run("scripts/validate-state.py", "--patch", "state/proposed/dead-npc.json", cwd=tmp),
            "dead NPC resurrected by declaration",
        )

        unavailable_resource_patch = {
            "turn": 1,
            "patch": [
                {
                    "op": "add",
                    "path": "/player/resources/silver_key",
                    "value": 1,
                    "reason": "Игрок заявил, что использует серебряный ключ.",
                    "turn": 1,
                }
            ],
            "uncertain_facts": [],
            "contradictions": ["Ресурс отсутствует в подтвержденном state."],
        }
        write_json(tmp / "state/proposed/unavailable-resource.json", unavailable_resource_patch)
        assert_fail(
            run("scripts/validate-state.py", "--patch", "state/proposed/unavailable-resource.json", cwd=tmp),
            "unavailable resource use",
        )

        no_reason_patch = {
            "turn": 1,
            "patch": [
                {"op": "replace", "path": "/relationships/player_king/trust", "value": 5, "reason": "", "turn": 1}
            ],
            "uncertain_facts": [],
            "contradictions": [],
        }
        write_json(tmp / "state/proposed/no-reason.json", no_reason_patch)
        assert_fail(run("scripts/validate-state.py", "--patch", "state/proposed/no-reason.json", cwd=tmp), "no reason")

        invalid_json = tmp / "state/proposed/invalid-json.json"
        invalid_json.write_text("{ nope", encoding="utf-8")
        assert_fail(run("scripts/validate-state.py", "--patch", str(invalid_json), cwd=tmp), "invalid JSON")

        empty_patch = {"turn": 1, "patch": [], "uncertain_facts": [], "contradictions": []}
        write_json(tmp / "state/proposed/narrative-only.json", empty_patch)
        before = (tmp / "state/current.json").read_text(encoding="utf-8")
        assert_ok(run("scripts/apply-state-patch.py", "--patch", "state/proposed/narrative-only.json", cwd=tmp), "dry-run rejection")
        after = (tmp / "state/current.json").read_text(encoding="utf-8")
        if before != after:
            raise AssertionError("dry-run changed state")

        corrected_patch = {
            "turn": 1,
            "patch": [
                {
                    "op": "add",
                    "path": "/timeline/-",
                    "value": {"turn": 1, "event": "Игрок попытался использовать серебряный ключ, но state не подтвердил владение.", "confirmed": True, "participants": ["player"]},
                    "reason": "Фиксируется попытка, а не успешное использование ресурса.",
                    "turn": 1,
                }
            ],
            "uncertain_facts": [],
            "contradictions": [],
        }
        write_json(tmp / "state/proposed/corrected.json", corrected_patch)
        assert_ok(
            run("scripts/apply-state-patch.py", "--patch", "state/proposed/corrected.json", "--confirm", cwd=tmp),
            "corrected apply",
        )
        reloaded = json.loads((tmp / "state/current.json").read_text(encoding="utf-8"))
        if not reloaded["timeline"] or reloaded["meta"]["state_version"] != 2:
            raise AssertionError("applied state did not persist after reload")

        assert_ok(run("scripts/apply-state-patch.py", "--rollback", "latest", cwd=tmp), "rollback dry-run")
        assert_ok(run("scripts/apply-state-patch.py", "--rollback", "latest", "--confirm", cwd=tmp), "rollback apply")
        rolled_back = json.loads((tmp / "state/current.json").read_text(encoding="utf-8"))
        if rolled_back["meta"]["state_version"] != 3:
            raise AssertionError("rollback did not create a new state version")

        rendered = run("scripts/render-state-block.py", cwd=tmp)
        assert_ok(rendered, "render state block")
        if "<AUTHORITATIVE_WORLD_STATE>" not in rendered.stdout:
            raise AssertionError("state injection block was not rendered")

        print("PASS iteration-2 state workflow tests")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
