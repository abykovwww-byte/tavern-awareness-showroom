from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.relationship_extraction import (
    RelationshipExtractionRejected,
    RelationshipExtractionService,
)
from app.services.relationship_store import RelationshipStore
from app.services.state_store import StateStore


MODEL = {
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
    "events": {"insult_public": {"axis": "loyalty", "weight": -20, "decay_turns": 40}},
    "character_weights": {},
    "roles": {"subordinate": {"strike_form": "sabotage"}},
    "wounds": {},
    "clocks": {"ultimatum": 4, "plot": 7},
    "plot": {"tell_required_every_turn": True, "discovery_chance_per_turn": 0.2},
}


def make_store(tmp_path: Path, campaign_id: str = "relationship-extraction") -> StateStore:
    state_path = tmp_path / f"{campaign_id}.json"
    state_path.write_text(
        json.dumps({"characters": {"ivan": {"name": "Иван"}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    return StateStore(str(tmp_path / "state.db"), campaign_id, str(state_path))


def settings(tmp_path: Path, *, scenario_type: str) -> Settings:
    return Settings(
        campaign_id="relationship-extraction",
        scenario_type=scenario_type,
        database_url=f"sqlite:///{tmp_path / 'state.db'}",
        world_state_path=str(tmp_path / "state.json"),
        nvidia_api_base="mock://relationship-extraction",
    )


def test_process_turn_is_idempotent_for_the_same_recorded_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves rerunning extraction cannot add a second cause or change the sum."""
    store = make_store(tmp_path)
    turn_id = store.record_turn("turn-1", "request-1", "player", "narrative", {}, 1)
    service = RelationshipExtractionService(settings(tmp_path, scenario_type="rp"), store, MODEL)
    calls = 0

    async def fixed_completion(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return {
            "model": "fixture",
            "choices": [{"message": {"content": json.dumps({"events": [{
                "character_id": "ivan",
                "event_id": "insult_public",
                "evidence": "public insult",
            }]})}}],
        }

    monkeypatch.setattr(service, "_complete", fixed_completion)
    first = asyncio.run(service.process_turn(turn_id))
    first_value = RelationshipStore(store, MODEL).value("ivan", "loyalty", turn_id)
    second = asyncio.run(service.process_turn(turn_id))

    with store.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM relationship_causes").fetchone()[0]
    assert calls == 2
    assert first["applied"] is True
    assert second["applied"] is False
    assert count == 1
    assert first_value == -20
    assert RelationshipStore(store, MODEL).value("ivan", "loyalty", turn_id) == first_value


def test_numeric_field_rejects_whole_response_without_partial_accrual(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves any nested number rejects all otherwise valid events and persists no cause."""
    store = make_store(tmp_path)
    turn_id = store.record_turn("turn-1", "request-1", "player", "narrative", {}, 1)
    service = RelationshipExtractionService(settings(tmp_path, scenario_type="rp"), store, MODEL)

    async def numeric_completion(*_args, **_kwargs):
        return {
            "model": "fixture",
            "choices": [{"message": {"content": json.dumps({"events": [
                {"character_id": "ivan", "event_id": "insult_public", "evidence": "valid evidence"},
                {"character_id": "ivan", "event_id": "insult_public", "evidence": "also valid", "score": 1},
            ]})}}],
        }

    monkeypatch.setattr(service, "_complete", numeric_completion)
    result = asyncio.run(service.process_turn(turn_id))

    assert result["rejection_code"] == "numeric_field_present"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM relationship_causes").fetchone()[0] == 0
        audit = connection.execute(
            "SELECT event_json FROM audit_events WHERE event_type = 'relationship_extraction_rejected'"
        ).fetchone()
    assert json.loads(audit["event_json"])["code"] == "numeric_field_present"


def test_parse_response_rejects_numeric_values_before_shape_acceptance(tmp_path: Path) -> None:
    """Proves numeric rejection has the exact frozen audit code even in an unknown field."""
    service = RelationshipExtractionService(
        settings(tmp_path, scenario_type="rp"),
        make_store(tmp_path),
        MODEL,
    )

    with pytest.raises(RelationshipExtractionRejected) as exc_info:
        service.parse_response({"events": [], "diagnostic": {"confidence": 0.9}}, character_ids={"ivan"})

    assert exc_info.value.code == "numeric_field_present"


def test_training_guard_skips_model_and_writes_no_relationship_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves service-level Training isolation runs before lookup/model work and writes nothing."""
    store = make_store(tmp_path)
    service = RelationshipExtractionService(settings(tmp_path, scenario_type="training"), store, MODEL)

    async def forbidden_completion(*_args, **_kwargs):
        raise AssertionError("training must not call the relationship model")

    monkeypatch.setattr(service, "_complete", forbidden_completion)
    result = asyncio.run(service.process_turn(999))

    assert result == {
        "processed": False,
        "applied": False,
        "turn_id": 999,
        "reason": "scenario_not_rp",
        "events": [],
    }
    with store.connect() as connection:
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "relationship_causes",
                "character_badges",
                "narrative_events",
                "character_axis_state",
            )
        }
    assert counts == {table: 0 for table in counts}
