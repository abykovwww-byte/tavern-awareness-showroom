from __future__ import annotations

import copy
import json
from pathlib import Path

from app.services.relationship_store import RelationshipStore
from app.services.relationships import RelationshipMechanics
from app.services.state_store import StateStore


def relationship_model() -> dict:
    return {
        "schema_version": "rp-relationships.v1",
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
        "clocks": {"ultimatum": 4, "plot": 7},
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

    rows = mechanics.store.cause_rows("ivan", "loyalty", 1)
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
    witness_rows = mechanics.store.cause_rows("maria", "loyalty", 2)
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
    assert missed.store.event_rows("ivan", "strike")[0]["status"] == "active"
