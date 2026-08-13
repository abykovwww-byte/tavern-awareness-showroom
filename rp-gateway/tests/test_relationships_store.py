from __future__ import annotations

import json
from pathlib import Path

from app.services.relationship_store import RelationshipStore
from app.services.state_store import StateStore


def make_store(tmp_path: Path, campaign_id: str = "relationship-store") -> StateStore:
    state_path = tmp_path / f"{campaign_id}.json"
    state_path.write_text(
        json.dumps({"characters": {"ivan": {"name": "Иван"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return StateStore(str(tmp_path / "state.db"), campaign_id, str(state_path))


def make_legacy_deadline_nullable(store: StateStore) -> None:
    with store.connect() as connection:
        connection.execute("DROP INDEX idx_narrative_events_active")
        connection.execute("ALTER TABLE narrative_events RENAME TO narrative_events_current")
        connection.execute(
            """
            CREATE TABLE narrative_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                axis TEXT NOT NULL,
                event_id TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_turn INTEGER NOT NULL,
                due_turn INTEGER,
                payload_json TEXT NOT NULL,
                resolution TEXT,
                resolved_turn INTEGER,
                resolved_turn_id INTEGER,
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO narrative_events(
                id, campaign_id, character_id, axis, event_id, status, opened_turn,
                due_turn, payload_json, resolution, resolved_turn, resolved_turn_id, created_at
            )
            SELECT id, campaign_id, character_id, axis, event_id, status, opened_turn,
                   due_turn, payload_json, resolution, resolved_turn, resolved_turn_id, created_at
            FROM narrative_events_current
            """
        )
        connection.execute("DROP TABLE narrative_events_current")
        connection.execute(
            "CREATE INDEX idx_narrative_events_active "
            "ON narrative_events(campaign_id, status, due_turn)"
        )


def record_turns(store: StateStore, count: int) -> list[int]:
    turn_ids: list[int] = []
    for number in range(1, count + 1):
        turn_ids.append(store.record_turn(
            f"turn-{number}",
            f"request-{number}",
            f"player-{number}",
            f"narrative-{number}",
            {},
            number,
            party_turn=number,
        ))
    return turn_ids


def add_cause(
    relationships: RelationshipStore,
    *,
    event_id: str,
    weight: int,
    turn_id: int,
    party_turn: int | None = None,
    expires_turn: int | None = None,
) -> bool:
    return relationships.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id=event_id,
        weight=weight,
        turn_id=turn_id,
        party_turn=turn_id if party_turn is None else party_turn,
        expires_turn=expires_turn,
        evidence=f"evidence-{event_id}",
        source="gm",
    )


def test_value_is_sum_of_live_causes_and_rollback_excludes_its_turn(tmp_path: Path) -> None:
    """Proves strict expiry and Decision 019 rollback exclusion affect the derived sum."""
    offset = make_store(tmp_path, "relationship-offset")
    record_turns(offset, 3)
    store = make_store(tmp_path)
    turn_ids = record_turns(store, 4)
    relationships = RelationshipStore(store, {})

    assert add_cause(relationships, event_id="lasting", weight=-10, turn_id=turn_ids[0], party_turn=1)
    assert add_cause(
        relationships,
        event_id="temporary",
        weight=-20,
        turn_id=turn_ids[1],
        party_turn=2,
        expires_turn=4,
    )
    assert add_cause(
        relationships,
        event_id="rolled-back",
        weight=-15,
        turn_id=turn_ids[2],
        party_turn=3,
    )
    assert relationships.value("ivan", "loyalty", 3) == -45

    with store.connect() as connection:
        connection.execute("UPDATE turns SET excluded_from_memory = 1 WHERE id = ?", (turn_ids[2],))

    assert relationships.value("ivan", "loyalty", 3) == -30
    assert relationships.value("ivan", "loyalty", 4) == -10


def test_turn_party_turn_backfill_is_monotonic_per_campaign(tmp_path: Path) -> None:
    first = make_store(tmp_path, "first-campaign")
    second = make_store(tmp_path, "second-campaign")
    record_turns(first, 3)
    record_turns(second, 2)
    with first.connect() as connection:
        connection.execute("UPDATE turns SET party_turn = NULL")

    StateStore(str(tmp_path / "state.db"), "migration-trigger", str(tmp_path / "migration.json"))

    with first.connect() as connection:
        rows = connection.execute(
            "SELECT campaign_id, party_turn FROM turns ORDER BY id"
        ).fetchall()
    assert [(row["campaign_id"], row["party_turn"]) for row in rows] == [
        ("first-campaign", 1),
        ("first-campaign", 2),
        ("first-campaign", 3),
        ("second-campaign", 1),
        ("second-campaign", 2),
    ]


def test_empty_legacy_relationship_tables_migrate_to_two_clock_ddl(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    with store.connect() as connection:
        connection.execute("DROP INDEX idx_relationship_causes_lookup")
        connection.execute("DROP INDEX idx_narrative_events_active")
        for table in (
            "relationship_causes",
            "character_badges",
            "narrative_events",
            "character_axis_state",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.executescript(
            """
            CREATE TABLE relationship_causes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
                character_id TEXT NOT NULL, axis TEXT NOT NULL, event_id TEXT NOT NULL,
                weight INTEGER NOT NULL, turn_id INTEGER NOT NULL, expires_turn INTEGER,
                evidence TEXT NOT NULL, source TEXT NOT NULL, created_at INTEGER NOT NULL,
                UNIQUE(campaign_id, character_id, axis, event_id, turn_id)
            );
            CREATE TABLE character_badges (
                id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
                character_id TEXT NOT NULL, badge_kind TEXT NOT NULL, badge_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT, created_at INTEGER NOT NULL,
                UNIQUE(campaign_id, character_id, badge_kind, badge_id)
            );
            CREATE TABLE narrative_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
                character_id TEXT NOT NULL, axis TEXT NOT NULL, event_id TEXT NOT NULL,
                status TEXT NOT NULL, opened_turn INTEGER NOT NULL, due_turn INTEGER,
                payload_json TEXT NOT NULL, resolution TEXT, resolved_turn INTEGER,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE character_axis_state (
                campaign_id TEXT NOT NULL, character_id TEXT NOT NULL, axis TEXT NOT NULL,
                band TEXT NOT NULL, band_since_turn INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                PRIMARY KEY(campaign_id, character_id, axis)
            );
            CREATE INDEX idx_relationship_causes_lookup
                ON relationship_causes(campaign_id, character_id, axis, turn_id);
            CREATE INDEX idx_narrative_events_active
                ON narrative_events(campaign_id, status, due_turn);
            """
        )

    migrated = make_store(tmp_path)
    with migrated.connect() as connection:
        cause_columns = {row["name"] for row in connection.execute("PRAGMA table_info(relationship_causes)")}
        badge_columns = {row["name"] for row in connection.execute("PRAGMA table_info(character_badges)")}
        event_columns = {
            row["name"]: row["notnull"] for row in connection.execute("PRAGMA table_info(narrative_events)")
        }
        index_columns = [
            row["name"] for row in connection.execute("PRAGMA index_info(idx_relationship_causes_lookup)")
        ]
    assert {"turn_id", "party_turn"} <= cause_columns
    assert "party_turn" in badge_columns and "turn_id" not in badge_columns
    assert event_columns["due_turn"] == 1
    assert event_columns["resolved_turn_id"] == 0
    assert index_columns == ["campaign_id", "character_id", "axis", "party_turn"]


def test_active_event_deadline_backfill_is_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path, "deadline-backfill")
    relationships = RelationshipStore(store, {})
    relationships.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="crack",
        opened_turn=8,
        due_turn=14,
        payload={"source": "legacy"},
    )
    make_legacy_deadline_nullable(store)

    with store.connect() as connection:
        connection.execute(
            "UPDATE narrative_events SET due_turn = NULL WHERE campaign_id = ?",
            (store.campaign_id,),
        )

    assert relationships.backfill_active_event_deadlines({"crack": 6}) == 1
    assert relationships.active_events(8)[0]["due_turn"] == 14
    assert relationships.backfill_active_event_deadlines({"crack": 6}) == 0


def test_resolve_event_rejects_excluded_provenance_turn_but_preserves_unscoped_resolution(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path, "excluded-resolution-provenance")
    relationships = RelationshipStore(store, {})
    turn_id = store.record_turn(
        "excluded-turn",
        "excluded-request",
        "player",
        "narrative",
        {},
        1,
        party_turn=1,
    )
    event_row_id = relationships.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="favour",
        opened_turn=0,
        due_turn=1,
    )
    assert event_row_id is not None
    with store.connect() as connection:
        connection.execute(
            "UPDATE turns SET excluded_from_memory = 1 WHERE campaign_id = ? AND id = ?",
            (store.campaign_id, turn_id),
        )

    assert not relationships.resolve_event(
        event_row_id,
        status="resolved",
        resolution="delivered",
        resolved_turn=1,
        resolved_turn_id=turn_id,
    )
    assert relationships.event_rows("ivan", "favour")[0]["status"] == "active"

    assert relationships.resolve_event(
        event_row_id,
        status="expired",
        resolution="deadline_missed",
        resolved_turn=1,
    )
    assert relationships.event_rows("ivan", "favour")[0]["status"] == "expired"


def test_add_cause_is_idempotent_and_value_is_clamped(tmp_path: Path) -> None:
    """Proves the frozen uniqueness key suppresses duplicates and diagnostics clamp."""
    store = make_store(tmp_path)
    relationships = RelationshipStore(store, {})

    assert add_cause(relationships, event_id="same-event", weight=-80, turn_id=1)
    assert not add_cause(relationships, event_id="same-event", weight=-80, turn_id=1)
    assert add_cause(relationships, event_id="other-event", weight=-80, turn_id=2)

    with store.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM relationship_causes WHERE campaign_id = ?",
            (store.campaign_id,),
        ).fetchone()[0]
    assert count == 2
    assert relationships.value("ivan", "loyalty", 2) == -100
