from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.relationship_store import RelationshipStore
from app.services.relationships import RelationshipMechanics
from app.services.state_store import StateStore


def relationship_model() -> dict:
    return {
        "schema_version": "rp-relationships.v2",
        "characters": {
            "ivan": {"aliases": ["Иван"]},
            "maria": {"aliases": ["Мария"]},
        },
        "axes": {
            "loyalty": {
                "min": -100,
                "max": 100,
                "per_turn_cap": 30,
                "band_deadband": 5,
                "bands": [
                    {"id": "enmity", "max": -70, "label": "вражда", "opens": "plot", "band_on": "cross"},
                    {"id": "rupture", "max": -40, "label": "разрыв", "opens": "ultimatum", "band_on": "resolution"},
                    {"id": "estranged", "max": -15, "label": "отчуждение", "opens": "crack", "band_on": "cross"},
                    {"id": "neutral", "max": 14, "label": "ровно"},
                    {"id": "favourable", "min": 15, "label": "расположение", "opens": "favour", "band_on": "cross"},
                    {"id": "faithful", "min": 40, "label": "верность"},
                    {"id": "devoted", "min": 70, "label": "преданность"},
                ],
            }
        },
        "events": {
            "loss_10_a": {"axis": "loyalty", "weight": -10, "decay_turns": None},
            "loss_10_b": {"axis": "loyalty", "weight": -10, "decay_turns": None},
            "loss_10_c": {"axis": "loyalty", "weight": -10, "decay_turns": None},
            "loss_15": {"axis": "loyalty", "weight": -15, "decay_turns": None},
            "loss_20_a": {"axis": "loyalty", "weight": -20, "decay_turns": None},
            "loss_20_b": {"axis": "loyalty", "weight": -20, "decay_turns": None},
        },
        "character_weights": {"ivan": {"role": "subordinate", "multipliers": {}}},
        "roles": {"subordinate": {"strike_form": "sabotage"}},
        "wounds": {},
        "clocks": {"crack": 6, "ultimatum": 4, "plot": 7, "favour": 10, "strike": 6},
        "trust_mapping": {"kind": "linear", "in": [-10, 10], "out": [-40, 40]},
        "plot": {"tell_required_every_turn": True, "discovery_chance_per_turn": 0.2},
    }


def make_store(tmp_path: Path, campaign_id: str = "relationship-mechanics") -> StateStore:
    state_path = tmp_path / f"{campaign_id}.json"
    state_path.write_text(
        json.dumps(
            {
                "characters": {
                    "ivan": {"name": "Иван"},
                    "maria": {"name": "Мария"},
                },
                "relationships": {},
            },
            ensure_ascii=False,
        ),
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


def event(character_id: str, event_id: str) -> dict[str, str]:
    return {"character_id": character_id, "event_id": event_id, "evidence": f"evidence-{event_id}"}


def test_chronological_crossings_open_crack_then_ultimatum_without_model(tmp_path: Path) -> None:
    """Proves preset events at -10/-20/-30/-45 open warning stages in turn order."""
    mechanics = RelationshipMechanics(make_store(tmp_path), relationship_model())

    changes = [
        mechanics.apply_events(turn_id=1, party_turn=1, events=[event("ivan", "loss_10_a")]),
        mechanics.apply_events(turn_id=2, party_turn=2, events=[event("ivan", "loss_10_b")]),
        mechanics.apply_events(turn_id=3, party_turn=3, events=[event("ivan", "loss_10_c")]),
        mechanics.apply_events(turn_id=4, party_turn=4, events=[event("ivan", "loss_15")]),
    ]

    assert [[change["event_id"] for change in turn] for turn in changes] == [[], ["crack"], [], ["ultimatum"]]
    assert all(change["event_id"] not in {"plot", "strike"} for turn in changes for change in turn)
    assert mechanics.store.value("ivan", "loyalty", 4) == -45
    assert mechanics.store.axis_state("ivan", "loyalty")["band"] == "estranged"


def test_per_turn_cap_limits_combined_events(tmp_path: Path) -> None:
    """Proves multiple events in one turn contribute no more than the configured cap."""
    mechanics = RelationshipMechanics(make_store(tmp_path), relationship_model())

    mechanics.apply_events(
        turn_id=1,
        party_turn=1,
        events=[event("ivan", "loss_20_a"), event("ivan", "loss_20_b")],
    )

    rows = [row for row in mechanics.store.cause_rows("ivan", "loyalty", 1) if row["source"] != "seed"]
    assert [row["weight"] for row in rows] == [-20, -10]
    assert mechanics.store.value("ivan", "loyalty", 1) == -30


def test_one_band_per_turn_even_when_value_crosses_multiple_boundaries(tmp_path: Path) -> None:
    """Proves one application opens only the adjacent event despite a far-away sum."""
    store = make_store(tmp_path)
    model = relationship_model()
    relationships = RelationshipStore(store, model)
    relationships.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id="historical-loss",
        weight=-60,
        turn_id=0,
        party_turn=0,
        expires_turn=None,
        evidence="historical evidence",
        source="gm",
    )
    relationships.set_axis_state(
        character_id="ivan",
        axis="loyalty",
        band="neutral",
        band_since_turn=0,
    )
    mechanics = RelationshipMechanics(store, model)

    changes = mechanics.apply_events(turn_id=1, party_turn=1, events=[event("ivan", "loss_10_a")])

    assert [change["event_id"] for change in changes] == ["crack"]
    assert mechanics.store.axis_state("ivan", "loyalty")["band"] == "estranged"
    assert mechanics.store.value("ivan", "loyalty", 1) == -70


def test_advance_turn_twice_changes_at_most_one_band_for_the_party_turn(tmp_path: Path) -> None:
    """Proves a failed/retried narrator request cannot advance the same party turn twice."""
    store = make_store(tmp_path)
    model = relationship_model()
    relationships = RelationshipStore(store, model)
    relationships.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id="historical-loss",
        weight=-100,
        turn_id=99,
        party_turn=0,
        expires_turn=None,
        evidence="historical evidence",
        source="gm",
    )
    relationships.set_axis_state(
        character_id="ivan",
        axis="loyalty",
        band="neutral",
        band_since_turn=0,
    )
    mechanics = RelationshipMechanics(store, model)

    first = mechanics.advance_turn(1)
    second = mechanics.advance_turn(1)

    assert [change["event_id"] for change in first] == ["crack"]
    assert second == []
    assert relationships.axis_state("ivan", "loyalty")["band"] == "estranged"
    assert relationships.event_rows("ivan", "ultimatum") == []


def test_deadband_requires_reserve_beyond_band_boundary(tmp_path: Path) -> None:
    """Proves reaching -15 is insufficient and crossing -20 opens the crack exactly once."""
    mechanics = RelationshipMechanics(make_store(tmp_path), relationship_model())

    first = mechanics.apply_events(turn_id=1, party_turn=1, events=[event("ivan", "loss_15")])
    second = mechanics.apply_events(turn_id=2, party_turn=2, events=[event("ivan", "loss_10_a")])

    assert first == []
    assert [change["event_id"] for change in second] == ["crack"]


def test_pressure_block_exposes_only_name_band_and_qualitative_pressure(tmp_path: Path) -> None:
    """Proves internal values, IDs, clocks, strike forms, and payload stay prompt-private."""
    model = copy.deepcopy(relationship_model())
    store = make_store(tmp_path)
    relationships = RelationshipStore(store, model)
    relationships.set_axis_state(character_id="ivan", axis="loyalty", band="enmity", band_since_turn=1)
    relationships.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="plot",
        opened_turn=1,
        due_turn=43,
        payload={
            "accomplice_id": "maria-secret-id",
            "target_id": "target-secret-id",
            "strike_form": "sabotage",
            "weight": -42,
        },
    )

    block = RelationshipMechanics(store, model).pressure_block(1, {"ivan": "Иван"})

    assert block is not None
    assert block.startswith("RELATIONSHIP_PRESSURE\n")
    assert "Иван" in block and "вражда" in block
    for forbidden in ("ivan", "maria-secret-id", "target-secret-id", "sabotage", "42", "-42", "plot"):
        assert forbidden not in block

    later_block = RelationshipMechanics(store, model).pressure_block(2, {"ivan": "Иван"})
    assert later_block is not None and later_block != block


def test_trust_seed_maps_once_without_mutating_canonical_state(tmp_path: Path) -> None:
    store = make_store(tmp_path, "trust-seed")
    state = store.get_state()
    state["characters"]["ivan"]["trust"] = 5
    state.setdefault("meta", {})["state_version"] = int(store.current_version() or 1) + 1
    store.insert_state_version(state, "test:trust-seed")
    store.write_state_file(state)
    mechanics = RelationshipMechanics(store, relationship_model())

    mechanics.advance_turn(0)

    seed_rows = [row for row in mechanics.store.cause_rows("ivan", "loyalty", 0) if row["source"] == "seed"]
    assert len(seed_rows) == 1
    assert seed_rows[0]["party_turn"] == 0
    assert seed_rows[0]["weight"] == 20
    assert mechanics.store.cause_rows("ivan", "loyalty", 0)[0]["event_id"] == "seed_trust"
    assert store.get_state()["characters"]["ivan"]["trust"] == 5
    pressure = mechanics.pressure_block(1, {"ivan": "Иван"})
    assert pressure is not None
    assert "Иван" in pressure and "Исходное доверие" in pressure
    assert "seed_trust" not in pressure and "20" not in pressure


def test_starosta_positive_seed_requires_scene_evidence_to_deliver_favour(tmp_path: Path) -> None:
    stack_root = Path(__file__).resolve().parents[2]
    model = json.loads(
        (stack_root / "worldpacks" / "starosta" / "relationships" / "model.json").read_text(encoding="utf-8")
    )
    state = json.loads((stack_root / "worldpacks" / "starosta" / "state-seed.json").read_text(encoding="utf-8"))
    state_path = tmp_path / "starosta.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    store = StateStore(str(tmp_path / "starosta.db"), "starosta-candidate", str(state_path))
    mechanics = RelationshipMechanics(store, model, rp_contract_revision=4)

    mechanics.advance_turn(0)

    seed_rows = [
        row
        for row in mechanics.store.cause_rows("bazhena", "loyalty", 0)
        if row["source"] == "seed"
    ]
    favour = mechanics.store.event_rows("bazhena", "favour")
    assert len(seed_rows) == 1
    assert seed_rows[0]["weight"] == 20
    assert len(favour) == 1
    assert favour[0]["opened_turn"] == 0
    assert favour[0]["due_turn"] == model["clocks"]["favour"] == 10
    assert favour[0]["status"] == "active"

    staged_block = mechanics.due_event_block(10, {"bazhena": "Бажена"})
    assert staged_block is not None
    assert mechanics.store.event_rows("bazhena", "favour")[0]["status"] == "active"

    assert mechanics.advance_turn(10) == []
    assert mechanics.store.event_rows("bazhena", "favour")[0]["status"] == "active"

    turn_id = store.record_turn(
        "favour-delivery",
        "favour-delivery-request",
        "Я наблюдаю.",
        "Бажена открыто заступилась за старосту.",
        {},
        store.current_version() or 1,
        party_turn=10,
    )
    mechanics.apply_events(
        turn_id=turn_id,
        party_turn=10,
        events=[
            {
                "character_id": "bazhena",
                "event_id": "defended_publicly",
                "evidence": "Бажена открыто заступилась за старосту.",
            }
        ],
    )
    changes = mechanics.resolve_delivered_favours(
        turn_id=turn_id,
        party_turn=10,
        narrative_response="Бажена открыто заступилась за старосту.",
    )
    resolved = mechanics.store.event_rows("bazhena", "favour")[0]
    resolution_block = mechanics.resolved_event_block(changes, {"bazhena": "Бажена"})
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "delivered"
    assert resolution_block is not None
    assert "Бажена" in resolution_block and "конкретную добровольную услугу" in resolution_block
    assert "favour" not in resolution_block and "10" not in resolution_block


def test_due_favour_stays_active_after_negative_scene_evidence(tmp_path: Path) -> None:
    mechanics = RelationshipMechanics(
        make_store(tmp_path, "negative-favour-evidence"),
        relationship_model(),
        rp_contract_revision=4,
    )
    mechanics.advance_turn(0)
    mechanics.store.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="favour",
        opened_turn=0,
        due_turn=1,
    )
    mechanics.apply_events(
        turn_id=1,
        party_turn=1,
        events=[{"character_id": "ivan", "event_id": "loss_10_a", "evidence": "Иван отказал."}],
    )

    assert mechanics.resolve_delivered_favours(
        turn_id=1,
        party_turn=1,
        narrative_response="Иван отказал.",
    ) == []
    assert mechanics.store.event_rows("ivan", "favour")[0]["status"] == "active"


def test_due_favour_does_not_resolve_from_player_claim_only(tmp_path: Path) -> None:
    model = relationship_model()
    model["events"]["defended_publicly"] = {
        "axis": "loyalty",
        "weight": 10,
        "decay_turns": 40,
        "resolves": ["favour"],
    }
    mechanics = RelationshipMechanics(
        make_store(tmp_path, "player-claim-favour-evidence"),
        model,
        rp_contract_revision=4,
    )
    mechanics.advance_turn(0)
    mechanics.store.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="favour",
        opened_turn=0,
        due_turn=1,
    )
    mechanics.apply_events(
        turn_id=1,
        party_turn=1,
        events=[{"character_id": "ivan", "event_id": "defended_publicly", "evidence": "Иван мне помог."}],
    )

    assert mechanics.resolve_delivered_favours(
        turn_id=1,
        party_turn=1,
        narrative_response="Иван молча смотрит в сторону.",
    ) == []
    assert mechanics.store.event_rows("ivan", "favour")[0]["status"] == "active"


@pytest.mark.parametrize("event_id", ["kept_promise", "shared_risk"])
def test_due_favour_ignores_positive_unmarked_scene_evidence(tmp_path: Path, event_id: str) -> None:
    model = relationship_model()
    model["events"][event_id] = {"axis": "loyalty", "weight": 10, "decay_turns": 40}
    mechanics = RelationshipMechanics(
        make_store(tmp_path, f"unmarked-{event_id}"),
        model,
        rp_contract_revision=4,
    )
    mechanics.advance_turn(0)
    mechanics.store.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="favour",
        opened_turn=0,
        due_turn=1,
    )
    evidence = f"Иван совершил событие {event_id}."
    mechanics.apply_events(
        turn_id=1,
        party_turn=1,
        events=[{"character_id": "ivan", "event_id": event_id, "evidence": evidence}],
    )

    assert mechanics.resolve_delivered_favours(
        turn_id=1,
        party_turn=1,
        narrative_response=evidence,
    ) == []
    assert mechanics.store.event_rows("ivan", "favour")[0]["status"] == "active"


def test_rollback_reopens_favour_delivered_by_excluded_turn(tmp_path: Path) -> None:
    model = relationship_model()
    model["events"]["defended_publicly"] = {
        "axis": "loyalty",
        "weight": 15,
        "decay_turns": 40,
        "resolves": ["favour"],
    }
    store = StateStore(
        str(tmp_path / "rollback-delivered-favour.db"),
        "rollback-delivered-favour",
        str(tmp_path / "rollback-delivered-favour.json"),
    )
    mechanics = RelationshipMechanics(store, model, rp_contract_revision=4)
    mechanics.store.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id="seed_trust",
        weight=20,
        turn_id=0,
        party_turn=0,
        expires_turn=None,
        evidence="seed",
        source="seed",
    )
    mechanics.store.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="favour",
        opened_turn=0,
        due_turn=1,
    )
    state_v2 = store.get_state()
    state_v2["meta"]["state_version"] = 2
    state_v2["meta"]["turn"] = 1
    store.insert_state_version(state_v2, "test:delivery")
    turn_id = store.record_turn(
        "delivery-turn",
        "delivery-request",
        "Я жду.",
        "Иван открыто заступился за меня.",
        {},
        2,
        party_turn=1,
    )
    mechanics.apply_events(
        turn_id=turn_id,
        party_turn=1,
        events=[
            {
                "character_id": "ivan",
                "event_id": "defended_publicly",
                "evidence": "Иван открыто заступился за меня.",
            }
        ],
    )

    assert mechanics.resolve_delivered_favours(
        turn_id=turn_id,
        party_turn=1,
        narrative_response="Иван открыто заступился за меня.",
    )
    delivered = mechanics.store.event_rows("ivan", "favour")[0]
    assert delivered["status"] == "resolved"
    assert delivered["resolved_turn_id"] == turn_id

    store.rollback(target_version=1)

    reopened = mechanics.store.event_rows("ivan", "favour")[0]
    assert reopened["status"] == "active"
    assert reopened["resolution"] is None
    assert reopened["resolved_turn"] is None
    assert reopened["resolved_turn_id"] is None
    assert mechanics.due_event_block(1, {"ivan": "Иван"}) is not None


def test_non_boundary_cause_reaches_qualitative_pressure(tmp_path: Path) -> None:
    """Proves a durable cause affects the next prompt without a threshold event."""
    mechanics = RelationshipMechanics(make_store(tmp_path, "cause-pressure"), relationship_model())

    changes = mechanics.apply_events(
        turn_id=1,
        party_turn=1,
        events=[event("ivan", "loss_10_a")],
    )
    pressure = mechanics.pressure_block(2, {"ivan": "Иван"})

    assert changes == []
    assert pressure is not None
    assert "Иван" in pressure and "Недавние поступки ослабили доверие" in pressure
    assert "loss_10_a" not in pressure and "-10" not in pressure


def test_legacy_active_event_deadline_is_repaired_before_advance(tmp_path: Path) -> None:
    model = relationship_model()
    store = make_store(tmp_path, "legacy-deadline")
    relationships = RelationshipStore(store, model)
    relationships.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id="crack",
        weight=-30,
        turn_id=1,
        party_turn=1,
        expires_turn=None,
        evidence="legacy basis",
        source="fixture",
    )
    relationships.set_axis_state(
        character_id="ivan",
        axis="loyalty",
        band="estranged",
        band_since_turn=1,
    )
    relationships.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="crack",
        opened_turn=1,
        due_turn=7,
        payload={"source": "legacy"},
    )
    make_legacy_deadline_nullable(store)
    with store.connect() as connection:
        connection.execute(
            "UPDATE narrative_events SET due_turn = NULL WHERE campaign_id = ?",
            (store.campaign_id,),
        )

    mechanics = RelationshipMechanics(store, model)
    assert mechanics.advance_turn(2) == []
    row = relationships.active_events(2)[0]
    assert row["due_turn"] == 7
    assert row["status"] == "active"


def test_crack_cascades_the_trigger_cause_to_connected_witness(tmp_path: Path) -> None:
    """Proves a boundary event creates a party-scoped cascade cause for a connected witness."""
    store = make_store(tmp_path)
    state = store.get_state()
    state["relationships"] = {
        "ivan-maria": {"from": "ivan", "to": "maria", "trust": 0, "suspicion": 0, "notes": []}
    }
    state.setdefault("meta", {})["state_version"] = int(store.current_version() or 1) + 1
    store.insert_state_version(state, "test:witness-link")
    store.write_state_file(state)
    mechanics = RelationshipMechanics(store, relationship_model())

    mechanics.apply_events(turn_id=1, party_turn=1, events=[event("ivan", "loss_10_a")])
    changes = mechanics.apply_events(turn_id=2, party_turn=2, events=[event("ivan", "loss_10_b")])

    assert [change["event_id"] for change in changes] == ["crack"]
    witness_rows = [row for row in mechanics.store.cause_rows("maria", "loyalty", 2) if row["source"] != "seed"]
    assert [(row["event_id"], row["source"], row["weight"]) for row in witness_rows] == [
        ("loss_10_b", "cascade", -10)
    ]


def test_ultimatum_deadline_resolves_and_moves_band_on_unfavourable_outcome(tmp_path: Path) -> None:
    """Proves band_on resolution holds rupture until the ultimatum deadline is missed."""
    mechanics = RelationshipMechanics(make_store(tmp_path), relationship_model())
    for turn_id, event_id in enumerate(("loss_10_a", "loss_10_b", "loss_10_c", "loss_15"), start=1):
        mechanics.apply_events(turn_id=turn_id, party_turn=turn_id, events=[event("ivan", event_id)])
    ultimatum = mechanics.store.event_rows("ivan", "ultimatum")[0]
    assert ultimatum["due_turn"] == 8
    assert mechanics.store.axis_state("ivan", "loyalty")["band"] == "estranged"

    changes = mechanics.advance_turn(8)

    resolved = mechanics.store.event_rows("ivan", "ultimatum")[0]
    assert resolved["status"] == "expired" and resolved["resolution"] == "deadline_missed"
    assert mechanics.store.axis_state("ivan", "loyalty")["band"] == "rupture"
    assert any(change.get("band") == "rupture" for change in changes)


def test_plot_discovery_and_missed_deadline_have_distinct_deterministic_outcomes(tmp_path: Path) -> None:
    """Proves discovery prevents a strike while a missed plot deadline opens one deterministically."""
    discovered_model = copy.deepcopy(relationship_model())
    discovered_model["plot"]["discovery_chance_per_turn"] = 1
    discovered = RelationshipMechanics(make_store(tmp_path, "plot-discovered"), discovered_model)
    discovered.store.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id="plot-basis",
        weight=-80,
        turn_id=0,
        party_turn=0,
        expires_turn=None,
        evidence="plot basis",
        source="fixture",
    )
    discovered.store.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="plot",
        opened_turn=0,
        due_turn=7,
        payload={"accomplice_id": "maria", "target_id": "player"},
    )
    discovered.advance_turn(1)
    assert discovered.store.event_rows("ivan", "plot")[0]["resolution"] == "discovered_early"
    assert discovered.store.event_rows("ivan", "strike") == []

    missed_model = copy.deepcopy(relationship_model())
    missed_model["plot"]["discovery_chance_per_turn"] = 0
    missed = RelationshipMechanics(make_store(tmp_path, "plot-missed"), missed_model)
    missed.store.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id="plot-basis",
        weight=-80,
        turn_id=0,
        party_turn=0,
        expires_turn=None,
        evidence="plot basis",
        source="fixture",
    )
    missed.store.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="plot",
        opened_turn=0,
        due_turn=1,
        payload={"accomplice_id": "maria", "target_id": "player"},
    )
    missed.advance_turn(1)
    assert missed.store.event_rows("ivan", "plot")[0]["resolution"] == "not_discovered"
    strike = missed.store.event_rows("ivan", "strike")[0]
    assert strike["status"] == "active"
    assert strike["due_turn"] == 7


def test_active_event_closes_when_its_basis_is_gone(tmp_path: Path) -> None:
    model = relationship_model()
    store = make_store(tmp_path, "basis-gone")
    relationships = RelationshipStore(store, model)
    relationships.add_cause(
        character_id="ivan",
        axis="loyalty",
        event_id="temporary-crack-basis",
        weight=-30,
        turn_id=1,
        party_turn=1,
        expires_turn=2,
        evidence="temporary basis",
        source="fixture",
    )
    relationships.set_axis_state(character_id="ivan", axis="loyalty", band="estranged", band_since_turn=1)
    relationships.open_event(
        character_id="ivan",
        axis="loyalty",
        event_id="crack",
        opened_turn=1,
        due_turn=7,
        payload={},
    )

    changes = RelationshipMechanics(store, model).advance_turn(2)

    event_row = relationships.event_rows("ivan", "crack")[0]
    assert event_row["status"] == "resolved"
    assert event_row["resolution"] == "basis_gone"
    assert any(change["resolution"] == "basis_gone" for change in changes)
