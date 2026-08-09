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


def record_turns(store: StateStore, count: int) -> None:
    for number in range(1, count + 1):
        store.record_turn(
            f"turn-{number}",
            f"request-{number}",
            f"player-{number}",
            f"narrative-{number}",
            {},
            number,
        )


def add_cause(
    relationships: RelationshipStore,
    *,
    event_id: str,
    weight: int,
    turn_id: int,
    expires_turn: int | None = None,
) -> bool:
    return relationships.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id=event_id,
        weight=weight,
        turn_id=turn_id,
        expires_turn=expires_turn,
        evidence=f"evidence-{event_id}",
        source="gm",
    )


def test_value_is_sum_of_live_causes_and_rollback_excludes_its_turn(tmp_path: Path) -> None:
    """Proves strict expiry and Decision 019 rollback exclusion affect the derived sum."""
    store = make_store(tmp_path)
    record_turns(store, 4)
    relationships = RelationshipStore(store, {})

    assert add_cause(relationships, event_id="lasting", weight=-10, turn_id=1)
    assert add_cause(relationships, event_id="temporary", weight=-20, turn_id=2, expires_turn=4)
    assert add_cause(relationships, event_id="rolled-back", weight=-15, turn_id=3)
    assert relationships.value("ivan", "loyalty", 3) == -45

    with store.connect() as connection:
        connection.execute("UPDATE turns SET excluded_from_memory = 1 WHERE id = 3")

    assert relationships.value("ivan", "loyalty", 3) == -30
    assert relationships.value("ivan", "loyalty", 4) == -10


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
