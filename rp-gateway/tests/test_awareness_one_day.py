from __future__ import annotations

import json
from pathlib import Path

from app.models.schemas import Intent, WorldPackSummary
from app.services.rule_engine import RuleEngine
from app.services.state_store import StateStore
from app.services.training_runtime import TrainingRuntimeService


WORLD_ROOT = Path(__file__).resolve().parents[2] / "worldpacks" / "awareness-one-day"
FROZEN_CONTRACT_HASH = "facbd786e4e7b2de283afc00eea5ebf6aa4784983afda26db106265806145872"


def runtime_for(tmp_path: Path) -> TrainingRuntimeService:
    manifest_path = WORLD_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack = WorldPackSummary(
        id=str(manifest["id"]),
        title=str(manifest["title"]),
        slug=WORLD_ROOT.name,
        status="playable",
        manifest_path=str(manifest_path),
        state_seed_path=str(WORLD_ROOT / "state-seed.json"),
        manifest=manifest,
    )
    store = StateStore(str(tmp_path / "state.db"), "party-one-day", pack.state_seed_path)
    return TrainingRuntimeService(pack, store)


def patch_values(patch) -> dict[str, object]:
    return {operation.path: operation.value for operation in patch.patch}


def test_one_day_v2_contract_hash_and_schedule_are_unchanged(tmp_path: Path):
    runtime = runtime_for(tmp_path)

    assert runtime.contract["schema_version"] == "rp-training-runtime.v2"
    assert runtime.program["schema_version"] == "rp-training-program.v2"
    assert runtime.program["revision"] == 3
    assert runtime.contract_hash == FROZEN_CONTRACT_HASH
    assert [turn["window"] for turn in runtime.program["turns"]] == [
        "ход 1, понедельник, 09:00-09:30",
        "ход 2, понедельник, 09:30-10:15",
        "ход 3, понедельник, 10:15-11:00",
        "ход 4, понедельник, 11:00-12:00",
        "ход 5, понедельник, 12:00-13:00",
        "ход 6, понедельник, 13:00-14:15",
        "ход 7, понедельник, 14:15-15:15",
        "ход 8, понедельник, 15:15-16:15",
        "ход 9, понедельник, 16:15-17:15",
        "ход 10, понедельник, 17:15-18:00",
    ]


def test_one_day_v2_fallbacks_validate_for_every_turn_and_debrief(tmp_path: Path):
    runtime = runtime_for(tmp_path)
    state = runtime.store.get_state()
    state["player"]["name"] = "Алексей"
    state["player"]["description"] = "Инженер по анализу вредоносного кода и подготовке сигнатур."

    for turn in range(1, 11):
        state["meta"]["turn"] = turn
        interaction_contract = (
            {"site": {"display_url": f"https://training.example.test/turn-{turn}"}}
            if runtime.turn_definition(turn)["surface"]["links"] == "artifact"
            else None
        )
        fallback = runtime.fallback_text(state, interaction_contract)
        assert runtime.validate_narrative(fallback, state, interaction_contract) == []
        assert len(runtime.prompt_contract(state)["surfaces"]) == 1

    state["meta"]["turn"] = 11
    state["player"]["resources"].update(
        {"security-score": 50, "roleplay-score": 24, "communication-score": 8, "total-score": 82}
    )
    debrief = runtime.fallback_text(state)
    assert runtime.validate_narrative(debrief, state) == []
    assert "82 из 100" in debrief
    assert "50 из 60" in debrief
    assert "24 из 30" in debrief
    assert "8 из 10" in debrief


def test_one_day_v2_runtime_preserves_deterministic_scoring(tmp_path: Path):
    runtime = runtime_for(tmp_path)
    state = runtime.store.get_state()
    state["meta"]["turn"] = 3
    state["player"]["description"] = "Инженер по анализу вредоносного кода и подготовке сигнатур."
    _, patch = RuleEngine().resolve(
        state,
        Intent(
            desired_outcome=(
                "Отказываюсь подтверждать оплату без владельца задачи, проверяю запрос по официальному "
                "каналу и сообщаю в SOC адрес отправителя и время."
            )
        ),
        "one-day-v2-score",
        campaign_id="awareness-one-day",
        scenario_type="training",
        training_runtime=runtime,
    )
    values = patch_values(patch)

    assert values["/player/resources/security-score"] == 10
    assert values["/player/resources/roleplay-score"] == 3
    assert values["/player/resources/communication-score"] == 1
    assert values["/player/resources/total-score"] == 14
