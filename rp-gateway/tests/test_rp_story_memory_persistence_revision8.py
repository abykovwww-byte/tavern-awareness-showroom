from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.services.state_store import StateStore


def legacy_story_memory_database(path: Path, campaign_id: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE campaigns (
                id TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE rp_story_memory_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                from_turn_id INTEGER NOT NULL,
                to_turn_id INTEGER NOT NULL,
                state_version INTEGER NOT NULL,
                memory_json TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                model TEXT NOT NULL,
                invalidated INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
                UNIQUE(campaign_id, revision),
                UNIQUE(campaign_id, to_turn_id)
            );
            CREATE INDEX idx_rp_story_memory_campaign_to
                ON rp_story_memory_snapshots(campaign_id, to_turn_id DESC, revision DESC);
            """
        )
        connection.execute(
            "INSERT INTO campaigns(id, created_at) VALUES(?, 1)",
            (campaign_id,),
        )
        connection.execute(
            """
            INSERT INTO rp_story_memory_snapshots(
                id, campaign_id, revision, from_turn_id, to_turn_id,
                state_version, memory_json, created_at, model, invalidated
            ) VALUES(5, ?, 3, 1, 50, 1, ?, 1, 'legacy-model', 0)
            """,
            (campaign_id, json.dumps({"current_situation": "legacy"})),
        )


def unique_index_columns(connection: sqlite3.Connection) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for index_row in connection.execute("PRAGMA index_list(rp_story_memory_snapshots)"):
        if not bool(index_row["unique"]):
            continue
        index_name = str(index_row["name"]).replace("'", "''")
        result.add(
            tuple(
                str(row["name"])
                for row in connection.execute(f"PRAGMA index_info('{index_name}')")
            )
        )
    return result


def test_rev8_story_memory_migration_preserves_rows_and_allows_idempotent_same_coverage(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    state_path = tmp_path / "state.json"
    campaign_id = "legacy-rev8"
    legacy_story_memory_database(database_path, campaign_id)

    store = StateStore(str(database_path), campaign_id, str(state_path))
    migrated = store.latest_rp_story_memory()

    assert migrated is not None
    assert migrated["id"] == 5
    assert migrated["revision"] == 3
    assert migrated["memory"] == {"current_situation": "legacy"}
    assert migrated["base_snapshot_id"] is None
    assert migrated["update_id"] is None
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(rp_story_memory_snapshots)")
        }
        unique_columns = unique_index_columns(connection)
    assert {"base_snapshot_id", "update_id"} <= columns
    assert ("campaign_id", "to_turn_id") not in unique_columns
    assert ("campaign_id", "update_id") in unique_columns

    legacy_deduplicated = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=50,
        state_version=1,
        memory={"current_situation": "must not replace legacy"},
        model="legacy-model",
    )
    assert legacy_deduplicated is not None
    assert legacy_deduplicated["id"] == migrated["id"]
    with pytest.raises(ValueError, match="requires update_id"):
        store.record_rp_story_memory(
            from_turn_id=1,
            to_turn_id=50,
            state_version=1,
            memory={"current_situation": "invalid retry contract"},
            model="rev8-model",
            allow_same_coverage=True,
        )

    situation_update = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=50,
        state_version=1,
        memory={"current_situation": "fresh situation"},
        model="rev8-model",
        base_snapshot_id=int(migrated["id"]),
        update_id="story:situation:through-50",
        allow_same_coverage=True,
    )
    assert situation_update is not None
    assert situation_update["id"] != migrated["id"]
    assert situation_update["base_snapshot_id"] == migrated["id"]
    assert situation_update["update_id"] == "story:situation:through-50"

    idempotent_retry = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=50,
        state_version=1,
        memory={"current_situation": "different retry payload"},
        model="rev8-model",
        base_snapshot_id=int(migrated["id"]),
        update_id="story:situation:through-50",
        allow_same_coverage=True,
    )
    assert idempotent_retry is not None
    assert idempotent_retry["id"] == situation_update["id"]
    assert idempotent_retry["memory"] == {"current_situation": "fresh situation"}

    stale_plan = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=50,
        state_version=1,
        memory={"active_threads": ["stale"]},
        model="rev8-model",
        base_snapshot_id=int(migrated["id"]),
        update_id="story:threads:stale",
        allow_same_coverage=True,
    )
    assert stale_plan is None

    threads_update = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=50,
        state_version=1,
        memory={"active_threads": ["fresh"]},
        model="rev8-model",
        base_snapshot_id=int(situation_update["id"]),
        update_id="story:threads:through-50",
        allow_same_coverage=True,
    )
    assert threads_update is not None
    assert store.effective_rp_story_memory()["id"] == threads_update["id"]  # type: ignore[index]

    reopened = StateStore(str(database_path), campaign_id, str(state_path))
    assert reopened.effective_rp_story_memory()["id"] == threads_update["id"]  # type: ignore[index]
    assert len(reopened.rp_story_memories()) == 3
