from __future__ import annotations

import copy
import json
from pathlib import Path

from app.models.schemas import Intent, WorldPackSummary
from app.services.rule_engine import RuleEngine
from app.services.state_store import StateStore
from app.services.training_runtime import TrainingRuntimeService


WORLD_PACKS_ROOT = Path(__file__).resolve().parents[2] / "worldpacks"


def worldpack(root: Path) -> WorldPackSummary:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return WorldPackSummary(
        id=str(manifest["id"]),
        title=str(manifest["title"]),
        slug=root.name,
        status="playable",
        manifest_path=str(manifest_path),
        state_seed_path=str(root / "state-seed.json"),
        manifest=manifest,
    )


def patch_values(patch) -> dict[str, object]:
    return {operation.path: operation.value for operation in patch.patch}


def test_one_day_runtime_owns_turns_fallbacks_and_scoring(tmp_path: Path):
    root = WORLD_PACKS_ROOT / "awareness-one-day"
    pack = worldpack(root)
    store = StateStore(str(tmp_path / "state.db"), "party-ellina", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)

    assert runtime.enabled is True
    assert runtime.program["turns"][0]["instruction"].startswith("Сгенерируй письмо от руководителя")
    assert runtime.assessment["rules"][0]["id"] == "count-link-open"

    state = store.get_state()
    state["meta"]["turn"] = 1
    state["player"]["name"] = "Эллина"
    state["player"]["description"] = "Тендерный специалист по закупкам и документам"
    prompt_contract = runtime.prompt_contract(state)

    assert prompt_contract["kind"] == "turn"
    assert prompt_contract["turn"] == 1
    assert "комплектность требований" in runtime.fallback_text(state)
    serialized = json.dumps(prompt_contract, ensure_ascii=False)
    assert "security-score" not in serialized
    assert "assessment" not in serialized

    state["meta"]["turn"] = 3
    _, patch = RuleEngine().resolve(
        state,
        Intent(
            desired_outcome=(
                "Не открываю ссылку, сообщу Сергею Литвинову, проверю запрос по корпоративной "
                "почте и обновлю статус задачи."
            )
        ),
        "runtime-score",
        campaign_id="awareness-one-day",
        scenario_type="training",
        training_runtime=runtime,
    )
    values = patch_values(patch)

    assert values["/player/resources/security-score"] == 10
    assert values["/player/resources/roleplay-score"] == 3
    assert values["/player/resources/communication-score"] == 1
    assert values["/player/resources/total-score"] == 14
    assert "SOC/ДИБ" in str(values["/player/resources/security-score-evidence"])


def test_one_day_negated_dangerous_actions_do_not_create_unsafe_evidence(tmp_path: Path):
    root = WORLD_PACKS_ROOT / "awareness-one-day"
    pack = worldpack(root)
    store = StateStore(str(tmp_path / "state.db"), "party-negation", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 9
    state["player"]["description"] = "Тендерный специалист по закупкам и документам"

    _, patch = RuleEngine().resolve(
        state,
        Intent(
            desired_outcome=(
                "Не открываю Act_July.pdf.exe, не передаю проверочный код, сообщу Сергею "
                "Литвинову и обновлю статус задачи."
            )
        ),
        "runtime-negation",
        campaign_id="awareness-one-day",
        scenario_type="training",
        training_runtime=runtime,
    )
    values = patch_values(patch)

    assert values["/player/resources/security-score"] == 10
    assert values["/player/resources/roleplay-score"] == 3
    assert values["/player/resources/communication-score"] == 1
    assert "/player/resources/unsafe-actions" not in values
    assert "/player/resources/credential-exposure" not in values
    assert "/player/resources/suspicious-artifacts-opened" not in values


def test_one_day_world_fallback_is_valid_for_every_capability_state(tmp_path: Path):
    root = WORLD_PACKS_ROOT / "awareness-one-day"
    pack = worldpack(root)
    store = StateStore(str(tmp_path / "state.db"), "party-fallbacks", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["player"]["name"] = "Эллина"
    state["player"]["description"] = "Тендерный специалист по закупкам и документам"

    for turn in range(1, 11):
        state["meta"]["turn"] = turn
        surface = runtime.turn_definition(turn)["surface"]
        contracts = [None]
        if surface["links"] == "artifact":
            contracts.append({"site": {"display_url": f"https://training.example.test/turn-{turn}"}})
        for interaction_contract in contracts:
            text = runtime.fallback_text(state, interaction_contract)
            assert runtime.validate_narrative(text, state, interaction_contract) == []


def write_obzh_world(root: Path) -> WorldPackSummary:
    training = root / "training"
    training.mkdir(parents=True)
    manifest = {
        "id": "obzh-evacuation",
        "title": "ОБЖ: эвакуация",
        "training_runtime": {
            "schema_version": "rp-training-runtime.v1",
            "program": "training/program.json",
            "assessment": "training/assessment.json",
            "fallbacks": "training/fallbacks.json",
        },
    }
    program = {
        "schema_version": "rp-training-program.v1",
        "revision": 1,
        "progression": {
            "total_turns": 1,
            "current_window_resource": "current-step",
            "turns_remaining_resource": "steps-remaining",
            "completion_status_resource": "completion-status",
            "complete_value": "complete",
            "debrief_window": "разбор эвакуации",
        },
        "default_role_task": "покинуть помещение по безопасному маршруту",
        "role_adapters": [],
        "global_validation": {"forbidden_patterns": ["правильный ответ"]},
        "turns": [
            {
                "turn": 1,
                "window": "учебная тревога",
                "header": "Ход 1. Учебная тревога.",
                "instruction": "Сгенерируй сообщение преподавателя ОБЖ о дыме и необходимости выбрать действие при эвакуации.",
                "visible_state_paths": [],
                "question": "Что ты делаешь?",
                "surface": {
                    "type": "messenger",
                    "count": 1,
                    "links": "none",
                    "profile_adaptation": False,
                    "require_question": True,
                    "required_fields": ["Канал:", "От:", "Текст:", "Ссылки:"],
                    "required_patterns": ["дым", "эваку", "(?m)^Ссылки:\\s*нет\\s*$"],
                    "fallback": (
                        "СООБЩЕНИЕ\nКанал: учебный чат\nЧат: класс\nОт: Преподаватель ОБЖ\n"
                        "Кому: {{player.name}}\nДата/время: 10:00\nВложения: нет\nСсылки: нет\n"
                        "Текст:\nВ коридоре появился дым. Обозначь порядок действий при эвакуации."
                    ),
                },
            }
        ],
        "debrief": {
            "header": "Разбор эвакуации.",
            "instruction": "Разбери только канонический балл из контракта.",
            "scores": [{"resource": "total-points", "max": 5, "label": "Безопасные действия"}],
            "evidence_resources": ["safety-evidence"],
            "fallback": "Разбор эвакуации.\n\nБезопасные действия: {{resource.total-points}} из 5.",
        },
    }
    assessment = {
        "schema_version": "rp-training-assessment.v1",
        "revision": 1,
        "detectors": {
            "safe-evacuation": {
                "type": "text_regex",
                "patterns": ["(?:эвакуир|покидаю|выхожу).{0,80}(?:лестниц|выход)|(?:звоню|позвоню).{0,40}112"],
            }
        },
        "rules": [
            {
                "id": "safe-evacuation-action",
                "turns": [1],
                "when": "safe-evacuation",
                "effects": [
                    {"increment": {"resource": "safety-points", "value": 5}},
                    {"append_evidence": {"resource": "safety-evidence", "fallback": "выбран безопасный маршрут"}},
                ],
            }
        ],
        "aggregates": {
            "total-points": {"bounded_sum": ["safety-points"], "min": 0, "max": 5}
        },
    }
    state = {
        "meta": {"campaign_id": "obzh-evacuation", "schema_version": "1.0.0", "state_version": 1, "turn": 0},
        "player": {
            "name": "Ученик",
            "description": "ученик восьмого класса",
            "resources": {
                "current-step": "учебная тревога",
                "steps-remaining": 1,
                "completion-status": "active",
                "safety-points": 0,
                "total-points": 0,
                "safety-evidence": "",
            },
        },
        "timeline": [],
        "world_constraints": [],
    }
    for path, payload in [
        (root / "manifest.json", manifest),
        (training / "program.json", program),
        (training / "assessment.json", assessment),
        (training / "fallbacks.json", {"schema_version": "rp-training-fallbacks.v1"}),
        (root / "state-seed.json", state),
    ]:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return worldpack(root)


def test_gateway_runtime_executes_non_awareness_obzh_world_without_domain_code(tmp_path: Path):
    pack = write_obzh_world(tmp_path / "obzh-evacuation")
    store = StateStore(str(tmp_path / "state.db"), "party-obzh", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 1

    contract = runtime.prompt_contract(state)
    serialized = json.dumps(contract, ensure_ascii=False).casefold()
    assert "преподавателя обж" in serialized
    assert "soc" not in serialized
    assert "фишинг" not in serialized
    fallback = runtime.fallback_text(state)
    assert runtime.validate_narrative(fallback, state) == []

    _, patch = RuleEngine().resolve(
        state,
        Intent(desired_outcome="Покидаю помещение через лестницу к аварийному выходу и звоню 112."),
        "obzh-score",
        campaign_id="obzh-evacuation",
        scenario_type="training",
        training_runtime=runtime,
    )
    values = patch_values(patch)
    assert values["/player/resources/safety-points"] == 5
    assert values["/player/resources/total-points"] == 5
    assert "/player/resources/security-score" not in values


def test_training_contract_snapshot_is_immutable_for_an_active_party(tmp_path: Path):
    root = tmp_path / "obzh-evacuation"
    pack = write_obzh_world(root)
    store = StateStore(str(tmp_path / "state.db"), "party-obzh", pack.state_seed_path)
    first = TrainingRuntimeService(pack, store)
    original_hash = first.contract_hash
    original_instruction = first.program["turns"][0]["instruction"]
    checkpoint = store.create_memory_checkpoint("runtime snapshot")
    store.fork_from_checkpoint(
        checkpoint_id=checkpoint["id"],
        target_campaign_id="party-obzh-branch",
        target_state_path=str(tmp_path / "branch.json"),
    )
    branch_store = StateStore(str(tmp_path / "state.db"), "party-obzh-branch", str(tmp_path / "branch.json"))
    branch_runtime = TrainingRuntimeService(pack, branch_store)
    assert branch_runtime.contract_hash == original_hash

    program_path = root / "training" / "program.json"
    edited = json.loads(program_path.read_text(encoding="utf-8"))
    edited["turns"][0]["instruction"] = "Новая редакция для будущих партий."
    program_path.write_text(json.dumps(edited, ensure_ascii=False), encoding="utf-8")

    second = TrainingRuntimeService(pack, store)
    assert second.contract_hash == original_hash
    assert second.program["turns"][0]["instruction"] == original_instruction
    assert second.program["turns"][0]["instruction"] != edited["turns"][0]["instruction"]
    assert TrainingRuntimeService(pack, branch_store).contract_hash == original_hash
