from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

from app.services.state_store import StateStore


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
META_TURN_LOOKUP = re.compile(
    r"(?:\.get\([\"']meta[\"']|\[[\"']meta[\"']\]).*"
    r"(?:\.get\([\"']turn[\"']|\[[\"']turn[\"']\])",
    re.DOTALL,
)


def numeric_meta_turn_comparisons(app_root: Path = APP_ROOT) -> list[str]:
    violations: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            if not any(isinstance(operand, ast.Constant) and isinstance(operand.value, int) for operand in operands):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if META_TURN_LOOKUP.search(segment):
                violations.append(f"{path.relative_to(app_root)}:{node.lineno}: {segment}")
    return violations


def test_gateway_has_no_numeric_literal_predicate_over_meta_turn():
    assert numeric_meta_turn_comparisons() == [], (
        "Gateway predicates must not compare meta.turn with numeric literals; "
        f"found: {numeric_meta_turn_comparisons()}"
    )


def test_meta_turn_numeric_literal_guard_fires_on_injected_predicate(tmp_path: Path):
    injected_app = tmp_path / "app"
    injected_app.mkdir()
    (injected_app / "leak.py").write_text(
        "def leaked_rule(state):\n"
        "    return state.get('meta', {}).get('turn', 0) > 10\n",
        encoding="utf-8",
    )

    assert numeric_meta_turn_comparisons(injected_app) == [
        "leak.py:2: state.get('meta', {}).get('turn', 0) > 10"
    ]


def test_rollback_keeps_turns_append_only_but_excludes_overwritten_turns_from_memory(tmp_path: Path):
    sqlite_path = tmp_path / "state.db"
    state_path = tmp_path / "state.json"
    store = StateStore(str(sqlite_path), "rollback-memory", str(state_path))

    for version in (1, 2, 3):
        if version > 1:
            state = store.get_state()
            state["meta"]["state_version"] = version
            state["meta"]["turn"] = version - 1
            store.insert_state_version(state, f"test:v{version}")
        store.record_turn(
            f"turn-{version}",
            f"request-{version}",
            f"player-{version}",
            f"narrator-{version}",
            {},
            version,
        )

    assert [turn["state_version"] for turn in store.turns_for_memory()] == [1, 2, 3]

    restored = store.rollback(target_version=1)

    assert restored["meta"]["state_version"] == 4
    assert store.current_version() == 4
    assert [turn["state_version"] for turn in store.turns_for_memory()] == [1]
    assert [turn["state_version"] for turn in store.turn_history()] == [1, 2, 3]
    with sqlite3.connect(sqlite_path) as connection:
        flags = connection.execute(
            "SELECT state_version, excluded_from_memory FROM turns "
            "WHERE campaign_id = ? ORDER BY state_version",
            (store.campaign_id,),
        ).fetchall()
    assert flags == [(1, 0), (2, 1), (3, 1)]
