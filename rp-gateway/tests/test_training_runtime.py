from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from app.models.schemas import Intent, WorldPackSummary
from app.services.narrative import training_turn_prompt_block
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
    assert runtime.contract["schema_version"] == "rp-training-runtime.v2"
    assert runtime.program["schema_version"] == "rp-training-program.v2"
    assert runtime.program["turns"][0]["instruction"].startswith("Сгенерируй письмо от руководителя")
    assert runtime.assessment["rules"][0]["id"] == "count-link-open"

    state = store.get_state()
    state["meta"]["turn"] = 1
    state["player"]["name"] = "Эллина"
    state["player"]["description"] = "Тендерный специалист по закупкам и документам"
    prompt_contract = runtime.prompt_contract(state)

    assert prompt_contract["kind"] == "turn"
    assert prompt_contract["turn"] == 1
    assert prompt_contract["header"] == runtime.program["turns"][0]["header"]
    assert prompt_contract["question"] == runtime.program["turns"][0]["question"]
    prompt_block = training_turn_prompt_block(prompt_contract)
    assert "Gateway applies the exact authored header and final question" in prompt_block
    assert "must start with this exact authored header" not in prompt_block
    assert "комплектность требований" in runtime.fallback_text(state)
    serialized = json.dumps(prompt_contract, ensure_ascii=False)
    assert "security-score" not in serialized
    assert "assessment" not in serialized
    assert "required_patterns" not in serialized
    assert "(?m)" not in serialized
    assert "must_include" in serialized
    assert "Тендерный специалист" in serialized
    assert "variation_budget" in serialized

    invalid_question = runtime.fallback_text(state).replace(prompt_contract["question"], "Что вы делаете?")
    normalized = runtime.normalize_narrative(invalid_question, state)
    assert normalized.endswith(prompt_contract["question"])
    assert runtime.validate_narrative(normalized, state) == []

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


LLM_BODY_VARIANTS = {
    1: [
        "По тендерной документации проверь комплект требований. К 09:35 нужен первый результат или вопрос, который блокирует проверку.",
        "Начни с назначенного тендерного лота: к 09:35 пришли проверяемый результат либо конкретный вопрос по документам.",
    ],
    2: [
        "По тендерной задаче пришли план: следующий приоритет, ожидаемый результат и возможный блокер.",
        "Продолжи текущую тендерную проверку и обозначь план, приоритет, результат и блокер.",
    ],
    3: [
        "По тендерному подрядчику срочно подтверди оплату, дай контакт финансов и пока не подключай Анну.",
        "Нужен ответ по оплате тендерного подрядчика и контакт ответственного в финансах; Анну не добавляй.",
    ],
    4: [
        "Для доступа к тендерным материалам требуется подтвердить учётную запись на корпоративном портале.",
        "Подтвердите учётные данные, чтобы продолжить работу с закупочной документацией.",
    ],
    5: [
        "Для тендерной команды пришли имена сотрудников, личные телефоны и сведения об отпусках.",
        "Нужна сверка тендерного отдела: имена, личные номера телефонов и график отпусков.",
    ],
    6: [
        "По тендерной проверке напиши, что уже готово, что осталось и кто держит блокер.",
        "Обнови тендерный статус: что готово, что осталось и у кого находится блокер.",
    ],
    7: [
        "Для доступа к тендерным материалам отправь шестизначный код из приложения-аутентификатора.",
        "Чтобы открыть закупочные документы, пришли шестизначный код аутентификатора.",
    ],
    8: [
        "По тендерному блоку назови проверяемый результат, рабочий риск и следующий шаг.",
        "Для тендерной синхронизации нужны результат, риск и следующий практический шаг.",
    ],
    9: [
        "Для тендерной задачи требуется срочное согласование приложенного акта.",
        "Закупочный подрядчик ждёт срочное согласование приложенного документа.",
    ],
    10: [
        "Пришли в личный мессенджер внутреннюю схему тендерного проекта и список администраторов.",
        "Нужны схема тендерного проекта и перечень администраторов в этом личном канале.",
    ],
}


def llm_variant(runtime: TrainingRuntimeService, state: dict, turn: int, body: str, interaction_contract=None) -> str:
    text = runtime.fallback_text(state, interaction_contract)
    header = runtime.turn_definition(turn)["header"]
    marker = "\nТело:\n" if runtime.turn_definition(turn)["surface"]["type"] == "email" else "\nТекст:\n"
    prefix, remainder = text.split(marker, 1)
    prefix = prefix.replace(header, f"Ход {turn} — вариант модели", 1)
    if marker == "\nТело:\n":
        _, suffix = remainder.split("\nПодпись:", 1)
        text = f"{prefix}\n{marker.strip()}\n{body}\nПодпись:{suffix}"
    else:
        question = runtime.turn_definition(turn)["question"]
        text = f"{prefix}\n{marker.strip()}\n{body}\n\nДругой финальный вопрос?"
        assert question not in text
    return runtime.normalize_narrative(text, state, interaction_contract)


def lexical_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"\w+", left.casefold()))
    right_tokens = set(re.findall(r"\w+", right.casefold()))
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


@pytest.mark.parametrize("turn", range(1, 11))
def test_one_day_accepts_two_fresh_llm_wordings_per_turn(tmp_path: Path, turn: int):
    root = WORLD_PACKS_ROOT / "awareness-one-day"
    pack = worldpack(root)
    store = StateStore(str(tmp_path / f"state-{turn}.db"), f"party-variant-{turn}", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = turn
    state["player"]["name"] = "Эллина"
    state["player"]["description"] = "Тендерный специалист по закупкам и документам"
    surface = runtime.turn_definition(turn)["surface"]
    interaction_contract = (
        {"site": {"display_url": f"https://training.example.test/turn-{turn}"}}
        if surface["links"] == "artifact"
        else None
    )

    variants = [llm_variant(runtime, state, turn, body, interaction_contract) for body in LLM_BODY_VARIANTS[turn]]
    assert variants[0] != variants[1]
    assert lexical_similarity(*LLM_BODY_VARIANTS[turn]) < 0.5
    for text in variants:
        assert runtime.validate_narrative(text, state, interaction_contract) == []


def test_training_normalization_repairs_boundaries_and_no_link_marker_but_not_urls(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness-one-day")
    store = StateStore(str(tmp_path / "state.db"), "party-normalize", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 1
    state["player"]["description"] = "Тендерный специалист по закупкам и документам"
    source = runtime.fallback_text(state).replace("Ссылки: нет", "Ссылки: отсутствуют").replace(
        runtime.turn_definition(1)["header"], "Ход 1 — утро"
    )
    normalized = runtime.normalize_narrative(source, state)
    assert normalized.startswith(runtime.turn_definition(1)["header"])
    assert normalized.endswith(runtime.turn_definition(1)["question"])
    assert "Ссылки: нет" in normalized
    assert runtime.validate_narrative(normalized, state) == []

    with_url = runtime.fallback_text(state).replace("Ссылки: нет", "Ссылки: https://outside.example")
    normalized_url = runtime.normalize_narrative(with_url, state)
    assert "https://outside.example" in normalized_url
    assert runtime.hard_violations(normalized_url, state) == ["Training turn must not contain a URL."]


def test_training_hard_violations_skip_repair_and_soft_profile_has_russian_instruction(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness-one-day")
    store = StateStore(str(tmp_path / "state.db"), "party-severity", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 1
    state["player"]["description"] = "Археолог по керамическим артефактам"

    wrong_sender = runtime.fallback_text(state).replace("От: Анна Петрова", "От: Посторонний", 1)
    assert any("authored fact" in item for item in runtime.hard_violations(wrong_sender, state))

    state["meta"]["turn"] = 5
    wrong_channel = runtime.fallback_text(state).replace("Канал: рабочий мессенджер", "Канал: личная почта", 1)
    assert any("authored fact" in item for item in runtime.hard_violations(wrong_channel, state))

    state["meta"]["turn"] = 1

    no_profile = runtime.fallback_text(state).replace("«Археолог по керамическим артефактам»", "по текущей задаче")
    assert runtime.hard_violations(no_profile, state) == []
    instruction = runtime.repair_instruction(no_profile, state)
    assert instruction.startswith("Исправь только перечисленные ограничения:")
    assert "Археолог" in instruction
    assert "(?m)" not in instruction

    missing_deadline = runtime.fallback_text(state).replace("09:35", "утром")
    deadline_instruction = runtime.repair_instruction(missing_deadline, state)
    assert "указан срок 09:35" in deadline_instruction
    assert "первый результат" not in deadline_instruction
    assert "(?m)" not in deadline_instruction


def test_training_block_shape_failure_returns_exactly_one_violation(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness-one-day")
    store = StateStore(str(tmp_path / "state.db"), "party-mutation", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 1
    mutated = runtime.fallback_text(state).replace("ПИСЬМО", "ПИСЬМО-ПЕРЕИМЕНОВАНО", 1)
    violations = runtime.validate_narrative(mutated, state)
    assert violations == ["Training turn must contain exactly 1 ПИСЬМО block(s)."]


def test_training_attachment_and_debrief_score_invariants_remain_hard(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness-one-day")
    store = StateStore(str(tmp_path / "state.db"), "party-hard-invariants", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["player"]["description"] = "Тендерный специалист по закупкам и документам"

    state["meta"]["turn"] = 9
    site = {"site": {"display_url": "https://training.example.test/turn-9"}}
    wrong_attachment = runtime.fallback_text(state, site).replace("Act_July.pdf.exe", "Act_July.pdf", 1)
    assert any("authored fact" in item for item in runtime.hard_violations(wrong_attachment, state, site))

    state["meta"]["turn"] = 11
    wrong_scores = runtime.fallback_text(state).replace("0 из 100", "1 из 100", 1)
    assert any("canonical total-score=0/100" in item for item in runtime.hard_violations(wrong_scores, state))


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
    assert runtime.contract["schema_version"] == "rp-training-runtime.v1"
    assert runtime.program["schema_version"] == "rp-training-program.v1"
    state = store.get_state()
    state["meta"]["turn"] = 1

    contract = runtime.prompt_contract(state)
    serialized = json.dumps(contract, ensure_ascii=False).casefold()
    assert "преподавателя обж" in serialized
    assert "упомяни обязательный факт «дым»" in serialized
    assert "required_patterns" not in serialized
    assert "(?m)" not in serialized
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


def test_runtime_v2_variation_budget_is_optional_but_typed(tmp_path: Path):
    root = tmp_path / "obzh-v2"
    pack = write_obzh_world(root)
    manifest_path = root / "manifest.json"
    program_path = root / "training" / "program.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    program = json.loads(program_path.read_text(encoding="utf-8"))
    manifest["training_runtime"]["schema_version"] = "rp-training-runtime.v2"
    program["schema_version"] = "rp-training-program.v2"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    program_path.write_text(json.dumps(program, ensure_ascii=False), encoding="utf-8")
    pack = worldpack(root)

    runtime = TrainingRuntimeService(
        pack,
        StateStore(str(tmp_path / "valid-v2.db"), "party-obzh-v2", pack.state_seed_path),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1
    assert runtime.prompt_contract(state)["variation_budget"] == []

    program["turns"][0]["variation_budget"] = "тон"
    program_path.write_text(json.dumps(program, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="variation_budget must contain non-empty strings"):
        TrainingRuntimeService(
            worldpack(root),
            StateStore(str(tmp_path / "invalid-v2.db"), "party-obzh-v2-invalid", pack.state_seed_path),
        )


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
