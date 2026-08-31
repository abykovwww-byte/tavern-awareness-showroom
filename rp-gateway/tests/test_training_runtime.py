from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest

from app.models.schemas import Intent, Outcome, WorldPackSummary
from app.services.narrative import training_turn_prompt_block
from app.services.rule_engine import RuleEngine
from app.services.state_store import StateStore
from app.services.training_runtime import TrainingRuntimeService
from app.services.validator import OutputValidator


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
    assert "Gateway renders markers, labels, the authored header, and the final question" in prompt_block
    assert "must start with this exact authored header" not in prompt_block
    assert "STRUCTURED_VISIBLE_SURFACES_FORMAT" in prompt_block
    assert "Fill exactly 1 ПИСЬМО surface value object(s)" in prompt_block
    assert 'surface marker counts (JSON instruction data, not visible output): {"ПИСЬМО":1}' in prompt_block
    assert "Канал: | От: | Кому:" in prompt_block
    assert "Every must_include item is mandatory" in prompt_block
    assert "never shorten, replace, or generalize" in prompt_block
    fallback = runtime.fallback_text(state)
    assert "комплектность требований" in fallback
    assert fallback.count(prompt_contract["question"]) == 1
    assert fallback.endswith(prompt_contract["question"])
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


def test_awareness_full_course_preserves_schedule_progression_and_legacy_scoring(tmp_path: Path):
    root = tmp_path / "awareness"
    shutil.copytree(WORLD_PACKS_ROOT / "awareness", root)
    pack = worldpack(root)
    store = StateStore(str(tmp_path / "state.db"), "party-awareness-runtime", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    expected_windows = [
        "ход 1, понедельник, 10:00-14:00",
        "ход 2, понедельник, 15:00-18:00",
        "ход 3, вторник, 10:00-14:00",
        "ход 4, вторник, 15:00-18:00",
        "ход 5, среда, 10:00-14:00",
        "ход 6, среда, 15:00-18:00",
        "ход 7, четверг, 10:00-14:00",
        "ход 8, четверг, 15:00-18:00",
        "ход 9, пятница, 10:00-14:00",
        "ход 10, пятница, 15:00-18:00",
    ]
    learner_answers = [
        "Сообщаю в SOC: отправитель, время, тема и вложение указаны в описании.",
        "Открываю Report.xlsx.exe и продолжаю работу.",
        "Пересылаю подозрительное письмо коллегам в общий чат.",
        "Перехожу по внешней ссылке на сайт и ввожу логин и пароль.",
        "Сообщаю структуру компании и внутренние системы клиенту.",
        "Продолжаю обычную рабочую задачу без заявленных действий.",
        "Продолжаю обычную рабочую задачу без заявленных действий.",
        "Продолжаю обычную рабочую задачу без заявленных действий.",
        "Продолжаю обычную рабочую задачу без заявленных действий.",
        "Продолжаю обычную рабочую задачу без заявленных действий.",
    ]

    assert runtime.contract["schema_version"] == "rp-training-runtime.v3"
    assert runtime.program["schema_version"] == "rp-training-program.v3"
    assert [turn["window"] for turn in runtime.program["turns"]] == expected_windows
    start_patch = runtime.start_patch(store.get_state(), store.campaign_id)
    assert start_patch is not None
    store.apply_state_patch(start_patch, "test:open-awareness-course")

    for turn, (window, learner_answer) in enumerate(zip(expected_windows, learner_answers), start=1):
        state = store.get_state()
        assert state["meta"]["turn"] == turn
        assert state["player"]["resources"]["current-turn-window"] == window
        assert runtime.prompt_contract(state)["kind"] == "turn"
        interaction_contract = (
            {"site": {"display_url": f"https://training.example.test/awareness-turn-{turn}"}}
            if turn in {1, 3, 5, 7, 8, 9}
            else None
        )
        fallback = runtime.fallback_text(state, interaction_contract)
        assert runtime.validate_narrative(fallback, state, interaction_contract) == []
        _, patch = RuleEngine().resolve(
            state,
            Intent(desired_outcome=learner_answer),
            f"awareness-course-{turn}",
            campaign_id="awareness",
            scenario_type="training",
            training_runtime=runtime,
        )
        store.apply_state_patch(patch, f"test:answer-awareness-turn-{turn}")

    completed = store.get_state()
    resources = completed["player"]["resources"]
    assert completed["meta"]["turn"] == 11
    assert resources["current-turn-window"] == runtime.program["progression"]["debrief_window"]
    assert resources["turns-remaining"] == 0
    assert resources["completion-status"] == "complete"
    assert resources["awareness-score"] == -10
    assert resources["safe-escalations"] == 1
    assert resources["reporting-quality"] == 1
    assert resources["unsafe-actions"] == 4
    assert resources["suspicious-artifacts-opened"] == 1
    assert resources["unnecessary-forwarding"] == 1
    assert resources["credential-exposure"] == 1
    assert resources["confidential-disclosures"] == 1
    assert runtime.prompt_contract(completed)["kind"] == "debrief"


AWARENESS_V3_FRESH_BODIES = {
    1: (
        ("Собери к планёрке три коротких пункта о готовых материалах и текущих рисках.",
         "Открой обновление расписания и подтверди учётную запись до совещания.",
         "Подскажи до обеда, какие задачи уже можно отметить завершёнными."),
        ("Подготовь лаконичную сводку по результатам утра и нужной помощи коллег.",
         "Проверь новый график в приложении и заверши сверку профиля сегодня утром.",
         "Нужен свежий статус по твоему направлению перед общей синхронизацией."),
    ),
    2: (("Уточни готовность материалов и порядок согласования на сегодня.", "Пришли внутренние слайды для моего учебного проекта."),
        ("Сообщи, какие документы можно передавать на следующий этап.", "Поделись закрытой презентацией, она нужна мне для подготовки.")),
    3: (("Сверь перечень задач и обозначь ближайший срок ответа.", "Открой страницу и проверь предложенный вариант документа."),
        ("Подтверди текущий статус материалов перед встречей.", "Посмотри данные на странице и напиши, всё ли там верно.")),
    4: (("Собери краткое обновление по рабочим документам.", "Перешли мне внутренний файл, чтобы сверить цифры."),
        ("Отметь готовые части и вопросы для согласования.", "Нужна копия служебной презентации для личной работы.")),
    5: (("Укажи, что готово к проверке в этом рабочем блоке.", "Перейди на страницу и подтверди сведения по запросу."),
        ("Пришли перечень завершённых материалов и открытых вопросов.", "Проверь форму по ссылке и сообщи результат сверки.")),
    6: (("Обнови статус документов перед вечерним согласованием.", "Отправь мне внутреннюю справку в личный чат."),
        ("Перечисли готовые материалы и ожидаемые решения.", "Поделись служебным файлом, он нужен для моей заметки.")),
    7: (("Для завершения входа пришли шестизначный код подтверждения через указанную страницу.", "Сообщи обычный статус по федеральному контракту к полудню."),
        ("Введи на странице шестизначный код из приложения, чтобы подтвердить доступ.", "Напиши, какие этапы контракта завершены и что ещё ожидается.")),
    8: (("Подготовь обновление по материалам к концу дня.", "Проверь страницу с запросом и подтверди получение."),
        ("Сообщи, какие документы готовы к дальнейшей работе.", "Открой форму и уточни статус указанной операции.")),
    9: (("Собери итог по готовым материалам первой половины дня.", "Перейди на страницу и сверь опубликованные сведения."),
        ("Отметь завершённые задачи и ожидаемые согласования.", "Проверь данные по ссылке и сообщи о расхождениях.")),
    10: (("Подведи рабочий итог недели по подготовленным документам.", "Перешли мне внутреннюю подборку для личного архива."),
         ("Укажи готовые материалы и оставшиеся вопросы перед завершением дня.", "Отправь служебные файлы, чтобы я использовал их в своей работе.")),
}


def awareness_v3_fresh_narrative(
    runtime: TrainingRuntimeService,
    turn: int,
    variant: int,
    interaction_contract: dict | None,
) -> str:
    definition = runtime.turn_definition(turn)
    bodies = AWARENESS_V3_FRESH_BODIES[turn][variant]
    display_url = (interaction_contract or {}).get("site", {}).get("display_url", "")
    email_sender = "Служба доступа — access@example.test" if turn == 7 else "Елена Шевелёва — elena@example.test"
    message_sender = "Роман Иванов — roman@example.test" if turn == 7 else "Кирилл Орлов — kirill@example.test"
    email_links = next(surface["links"] for surface in definition["surfaces"] if surface["type"] == "email")
    message_links = next(surface["links"] for surface in definition["surfaces"] if surface["type"] == "messenger")

    def email(body: str, *, attachment: str = "нет", link: str = "нет", sender: str = email_sender) -> str:
        return "\n".join([
            "ПИСЬМО", "Канал: корпоративная почта", f"От: {sender}", "Кому: сотрудник@example.test",
            f"Дата/время: свежий вариант {variant + 1}", f"Тема: Рабочий запрос {turn}-{variant + 1}",
            f"Вложения: {attachment}", f"Ссылки: {link}", "Тело:", body, "Подпись:", "Рабочая группа",
        ])

    def message(body: str) -> str:
        return "\n".join([
            "СООБЩЕНИЕ", "Канал: рабочий мессенджер", "Чат: личный чат", f"От: {message_sender}",
            "Кому: сотрудник@example.test", f"Дата/время: свежий вариант {variant + 1}", "Вложения: нет",
            f"Ссылки: {display_url if message_links == 'artifact' else 'нет'}", "Текст:", body,
        ])

    blocks: list[str] = []
    if turn == 1:
        blocks.append(email(bodies[0], link=display_url, sender="Ольга Смирнова — olga@example.test"))
        blocks.append(email(bodies[1], attachment="WorkSchedule_Update.xlsx.exe", link="нет",
                            sender="Support Desk — support@example.test"))
        message_body = bodies[2]
    else:
        blocks.append(email(bodies[0], link=display_url if email_links == "artifact" else "нет"))
        message_body = bodies[1]
    blocks.append(message(message_body))
    return "\n\n".join([definition["header"], *blocks, definition["question"]])


@pytest.mark.parametrize("turn", range(1, 11))
def test_awareness_v3_accepts_two_fresh_multichannel_wordings_per_turn(tmp_path: Path, turn: int):
    root = WORLD_PACKS_ROOT / "awareness"
    runtime = TrainingRuntimeService(
        worldpack(root),
        StateStore(str(tmp_path / f"awareness-v3-{turn}.db"), f"party-awareness-v3-{turn}", root / "state-seed.json"),
    )
    assert runtime.contract_hash == "3ecd9cb805234e423e557e4b426b8b88cd32f678a2c03943fd0af80b9b487b5d"
    assert runtime.program["revision"] == 2
    state = runtime.store.get_state()
    state["meta"]["turn"] = turn
    interaction_contract = (
        {"site": {"display_url": f"https://training.example.test/fresh-awareness-{turn}"}}
        if any(surface["links"] == "artifact" for surface in runtime.turn_definition(turn)["surfaces"])
        else None
    )
    program_source = (root / "training" / "program.json").read_text(encoding="utf-8")
    variants = [awareness_v3_fresh_narrative(runtime, turn, variant, interaction_contract) for variant in (0, 1)]

    assert variants[0] != variants[1]
    for text in variants:
        assert text != runtime.fallback_text(state, interaction_contract)
        assert text not in program_source
        assert runtime.validate_narrative(text, state, interaction_contract) == []
    fallback = runtime.fallback_text(state, interaction_contract)
    question = runtime.turn_definition(turn)["question"]
    assert fallback.count(question) == 1
    assert fallback.endswith(question)
    if turn == 1:
        assert variants[0].count("\nПИСЬМО\n") == 2
        assert variants[0].count("\nСООБЩЕНИЕ\n") == 1
        assert variants[0].split("\nПИСЬМО\n", 2)[1].count(interaction_contract["site"]["display_url"]) == 1
        assert "WorkSchedule_Update.xlsx.exe\nСсылки: нет" in variants[0]


def test_training_visible_sources_do_not_teach_angle_sender_syntax():
    sources = [
        WORLD_PACKS_ROOT / "awareness" / "campaign-bible.md",
        WORLD_PACKS_ROOT / "awareness" / "prompts" / "gm-system.md",
        WORLD_PACKS_ROOT / "awareness" / "prompts" / "opening-scene.md",
    ]
    for path in sources:
        assert not re.search(r"<[A-Za-z0-9._@+\-]+>", path.read_text(encoding="utf-8"))

    for slug in ("awareness", "awareness-one-day"):
        source = (WORLD_PACKS_ROOT / slug / "training" / "program.json").read_text(encoding="utf-8")
        assert not re.search(r"<[A-Za-z0-9._@+\-]+>", source)


@pytest.mark.parametrize("mutation", ["duplicate", "body_only", "next_line"])
def test_awareness_v3_requires_one_active_url_on_the_links_line(tmp_path: Path, mutation: str):
    root = WORLD_PACKS_ROOT / "awareness"
    runtime = TrainingRuntimeService(
        worldpack(root),
        StateStore(str(tmp_path / "state.db"), f"party-url-{mutation}", root / "state-seed.json"),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1
    url = "https://training.example.test/turn-1"
    interaction_contract = {"site": {"display_url": url}}
    narrative = runtime.fallback_text(state, interaction_contract)
    if mutation == "duplicate":
        narrative = narrative.replace("Материалы лежат", f"Повторная ссылка {url}. Материалы лежат", 1)
    elif mutation == "body_only":
        narrative = narrative.replace(f"Ссылки: {url}", "Ссылки: нет", 1).replace(
            "Материалы лежат",
            f"Материалы по адресу {url} лежат",
            1,
        )
    else:
        narrative = narrative.replace(f"Ссылки: {url}", f"Ссылки:\n{url}", 1)

    assert (
        "Training turn must contain the active artifact URL exactly once on its standalone Ссылки line."
        in runtime.hard_violations(narrative, state, interaction_contract)
    )
    assert runtime.repair_blockers(narrative, state, interaction_contract) == []
    instruction = runtime.repair_instruction(narrative, state, interaction_contract)
    assert "активный display_url ровно один раз" in instruction
    assert "только значением поля «Ссылки:»" in instruction


def test_awareness_v3_keeps_an_outside_url_blocking(tmp_path: Path):
    root = WORLD_PACKS_ROOT / "awareness"
    runtime = TrainingRuntimeService(
        worldpack(root),
        StateStore(str(tmp_path / "state.db"), "party-url-outside", root / "state-seed.json"),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1
    url = "https://training.example.test/turn-1"
    interaction_contract = {"site": {"display_url": url}}
    narrative = runtime.fallback_text(state, interaction_contract).replace(
        url,
        "https://outside.example/turn-1",
        1,
    )

    violation = "Training turn must not contain a URL outside the active artifact contract."
    assert violation in runtime.hard_violations(narrative, state, interaction_contract)
    assert violation in runtime.repair_blockers(narrative, state, interaction_contract)
    assert runtime.repair_instruction(narrative, state, interaction_contract) == ""


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

    unicode_separated = runtime.fallback_text(state).replace("\n", "\u2028")
    normalized_unicode = runtime.normalize_narrative(unicode_separated, state)
    assert "\u2028" not in normalized_unicode
    assert runtime.validate_narrative(normalized_unicode, state) == []

    with_url = runtime.fallback_text(state).replace("Ссылки: нет", "Ссылки: https://outside.example")
    normalized_url = runtime.normalize_narrative(with_url, state)
    assert "https://outside.example" in normalized_url
    assert runtime.hard_violations(normalized_url, state) == ["Training turn must not contain a URL."]
    assert runtime.repair_blockers(normalized_url, state) == []
    instruction = runtime.repair_instruction(normalized_url, state)
    assert "Удали все URL" in instruction
    assert "Ссылки: нет" in instruction


def test_training_hard_identity_gets_one_repair_but_channel_remains_blocking(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness-one-day")
    store = StateStore(str(tmp_path / "state.db"), "party-severity", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 1
    state["player"]["description"] = "Археолог по керамическим артефактам"
    profile_instruction = runtime.prompt_contract(state)["surfaces"][0]["profile_adaptation_instruction"]
    assert "«Археолог»" in profile_instruction
    assert "археол" in runtime.profile_markers(state["player"]["description"])

    wrong_sender = runtime.fallback_text(state).replace("От: Анна Петрова", "От: Посторонний", 1)
    assert any("authored fact" in item for item in runtime.hard_violations(wrong_sender, state))
    assert runtime.repair_blockers(wrong_sender, state) == []
    assert "Анна Петрова" in runtime.repair_instruction(wrong_sender, state)

    state["meta"]["turn"] = 3
    wrong_director = runtime.fallback_text(state).replace(
        "От: Генеральный директор",
        "От: Руководитель",
        1,
    )
    director_instruction = runtime.repair_instruction(wrong_director, state)
    assert "поле «От» представляет аккаунт генерального директора" in director_instruction
    assert "есть просьба подтвердить оплату" in director_instruction
    assert "JSON-инструкция, не текст ответа" in director_instruction

    state["meta"]["turn"] = 5
    wrong_channel = runtime.fallback_text(state).replace("Канал: рабочий мессенджер", "Канал: личная почта", 1)
    assert any("authored fact" in item for item in runtime.hard_violations(wrong_channel, state))
    assert runtime.repair_blockers(wrong_channel, state)
    assert runtime.repair_instruction(wrong_channel, state) == ""

    state["meta"]["turn"] = 1

    no_profile = runtime.fallback_text(state).replace("«Археолог по керамическим артефактам»", "по текущей задаче")
    assert runtime.hard_violations(no_profile, state) == []
    instruction = runtime.repair_instruction(no_profile, state)
    assert instruction.startswith("Верни полный исправленный ответ, а не патч.")
    assert "сохрани все остальные уже выполненные требования" in instruction
    assert "Археолог" in instruction
    assert "(?m)" not in instruction

    missing_deadline = runtime.fallback_text(state).replace("09:35", "утром")
    deadline_instruction = runtime.repair_instruction(missing_deadline, state)
    assert "указан срок 09:35" in deadline_instruction
    assert "profile_adaptation_instruction" in deadline_instruction
    assert "Археолог по керамическим артефактам" in deadline_instruction
    assert "запрошен первый результат" in deadline_instruction
    assert "(?m)" not in deadline_instruction

    missing_result = re.sub(r"результ\w*", "материал", runtime.fallback_text(state), flags=re.IGNORECASE)
    assert runtime.validate_narrative(missing_result, state) == [
        "Training surface is missing authored fact: результ"
    ]
    result_instruction = runtime.repair_instruction(missing_result, state)
    assert "запрошен первый результат" in result_instruction
    assert "сохрани все остальные уже выполненные требования" in result_instruction
    assert "Археолог по керамическим артефактам" in result_instruction


def test_training_block_shape_failure_returns_exactly_one_violation(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness-one-day")
    store = StateStore(str(tmp_path / "state.db"), "party-mutation", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 1
    mutated = runtime.fallback_text(state).replace("ПИСЬМО", "ПИСЬМО-ПЕРЕИМЕНОВАНО", 1)
    violations = runtime.validate_narrative(mutated, state)
    assert violations == ["Training turn must contain exactly 1 ПИСЬМО block(s)."]
    assert runtime.hard_violations(mutated, state) == []
    instruction = runtime.repair_instruction(mutated, state)
    assert "Верни полный исправленный ответ" in instruction
    assert "В полном исправленном ответе используй состав блоков" in instruction
    assert "Перепиши весь visible response" not in instruction
    assert "сохрани все остальные уже выполненные требования" in instruction
    assert '{"ПИСЬМО":1}' in instruction

    state["meta"]["turn"] = 3
    undeclared_email = runtime.fallback_text(state) + "\nПИСЬМО\nОт: Повтор прошлого хода"
    undeclared_violation = "Training turn contains undeclared ПИСЬМО block(s)."
    assert runtime.hard_violations(undeclared_email, state) == [undeclared_violation]
    assert runtime.repair_blockers(undeclared_email, state) == [undeclared_violation]
    assert runtime.repair_instruction(undeclared_email, state) == ""


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
    assert runtime.repair_blockers(wrong_attachment, state, site)
    assert runtime.repair_instruction(wrong_attachment, state, site) == ""

    state["meta"]["turn"] = 11
    wrong_scores = runtime.fallback_text(state).replace("0 из 100", "1 из 100", 1)
    assert any("canonical total-score=0/100" in item for item in runtime.hard_violations(wrong_scores, state))


@pytest.mark.parametrize("slug", ["awareness", "awareness-one-day"])
def test_predebrief_recommendation_rule_reaches_every_turn_contract_but_not_debrief(
    tmp_path: Path,
    slug: str,
):
    pack = worldpack(WORLD_PACKS_ROOT / slug)
    store = StateStore(str(tmp_path / "state.db"), f"party-predebrief-{slug}", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    pre_debrief_rule = (
        "До отдельного итогового разбора не используй ни в повествовании, ни внутри "
        "симулированных блоков ПИСЬМО/СООБЩЕНИЕ формы «рекомендую» и «рекомендация». "
        "Если симулированному отправителю нужен такой смысл, переформулируй его как "
        "нейтральный запрос или факт. В итоговом разборе эти формы разрешены."
    )

    for turn in range(1, 11):
        state["meta"]["turn"] = turn
        contract = runtime.prompt_contract(state)
        assert contract["kind"] == "turn"
        assert contract["instruction"].endswith(pre_debrief_rule)
        assert pre_debrief_rule in training_turn_prompt_block(contract)

    state["meta"]["turn"] = 11
    debrief_contract = runtime.prompt_contract(state)
    assert debrief_contract["kind"] == "debrief"
    assert pre_debrief_rule not in debrief_contract["instruction"]


def test_training_debrief_accepts_natural_score_wording_and_recommendations(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness")
    store = StateStore(str(tmp_path / "state.db"), "party-natural-debrief", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 11
    text = runtime.fallback_text(state).replace("0 из 100", "0 баллов из 100", 1)
    text += "\n\nРекомендации:\nПродолжай проверять адрес и домен отправителя."
    outcome = Outcome(
        check_id="training-debrief",
        action_type="narrative",
        actor="player",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="training debrief",
    )

    result = OutputValidator().validate(
        text,
        outcome,
        state,
        scenario_type="training",
        training_runtime=runtime,
    )

    assert result.valid is True
    assert runtime.hard_violations(text, state) == []

    state["meta"]["turn"] = 1
    normal_turn = runtime.fallback_text(state) + "\n\nРекомендации:\nСлужебный разбор."
    normal_result = OutputValidator().validate(
        normal_turn,
        outcome,
        state,
        scenario_type="training",
        training_runtime=runtime,
    )
    expected_violation = "Narrative exposed analysis, recommendation, or diagnostic labels to the player."
    assert expected_violation in normal_result.violations
    for forbidden_label in ("Diagnostics:\ninternal", "Gateway:\ninternal"):
        debrief_with_service_label = OutputValidator().validate(
            f"{text}\n\n{forbidden_label}",
            outcome,
            state | {"meta": state["meta"] | {"turn": 11}},
            scenario_type="training",
            training_runtime=runtime,
        )
        assert expected_violation in debrief_with_service_label.violations


def test_awareness_allows_ordinary_dashboard_work_but_keeps_service_internals_forbidden(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness")
    store = StateStore(str(tmp_path / "state.db"), "party-dashboard-wording", pack.state_seed_path)
    runtime = TrainingRuntimeService(pack, store)
    state = store.get_state()
    state["meta"]["turn"] = 1
    visible = runtime.fallback_text(state).replace(
        "\n\n",
        "\n\nСтатус по обычной рабочей задаче обновлён в дашборде.\n\n",
        1,
    )

    assert runtime.validate_narrative(visible, state) == []
    forbidden = visible.replace("Статус по обычной рабочей задаче", "Статус backend")
    assert any("globally forbidden content" in item for item in runtime.hard_violations(forbidden, state))


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


def test_legacy_turn_with_explicit_empty_question_stays_without_question(tmp_path: Path):
    root = tmp_path / "obzh-no-question"
    pack = write_obzh_world(root)
    program_path = root / "training" / "program.json"
    program = json.loads(program_path.read_text(encoding="utf-8"))
    program["turns"][0]["question"] = ""
    program["turns"][0]["surface"]["require_question"] = False
    program_path.write_text(json.dumps(program, ensure_ascii=False), encoding="utf-8")
    runtime = TrainingRuntimeService(
        pack,
        StateStore(str(tmp_path / "state.db"), "party-no-question", pack.state_seed_path),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1

    fallback = runtime.fallback_text(state)

    assert "Что ты делаешь и как отвечаешь?" not in fallback
    assert fallback.endswith("Обозначь порядок действий при эвакуации.")


V3_EMAIL_ONE = (
    "ПИСЬМО\nОт: Руководитель\nКому: Ученик\nТема: Первый запрос\n"
    "Вложения: нет\nСсылки: нет\nТело:\nПроверь первый документ."
)
V3_EMAIL_TWO = (
    "ПИСЬМО\nОт: Коллега\nКому: Ученик\nТема: Второй запрос\n"
    "Вложения: нет\nСсылки: нет\nТело:\nПроверь второй документ."
)
V3_MESSAGE = (
    "СООБЩЕНИЕ\nКанал: рабочий мессенджер\nЧат: Проект\nОт: Координатор\n"
    "Кому: Ученик\nДата/время: 10:00\nВложения: нет\nСсылки: нет\n"
    "Текст:\nПодтверди получение запросов."
)


def write_multichannel_v3_world(root: Path) -> WorldPackSummary:
    write_obzh_world(root)
    manifest_path = root / "manifest.json"
    program_path = root / "training" / "program.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    program = json.loads(program_path.read_text(encoding="utf-8"))
    manifest["training_runtime"]["schema_version"] = "rp-training-runtime.v3"
    program["schema_version"] = "rp-training-program.v3"
    program["turns"][0] = {
        "turn": 1,
        "window": "учебная тревога",
        "header": "Ход 1. Учебная тревога.",
        "instruction": "Покажи два письма и одно сообщение.",
        "visible_state_paths": [],
        "question": "Что ты делаешь?",
        "require_question": True,
        "variation_budget": [],
        "fallback": f"{V3_EMAIL_ONE}\n\n{V3_EMAIL_TWO}\n\n{V3_MESSAGE}",
        "surfaces": [
            {
                "type": "email",
                "count": 2,
                "links": "none",
                "profile_adaptation": False,
                "required_fields": ["От:", "Кому:", "Тема:", "Вложения:", "Ссылки:", "Тело:"],
            },
            {
                "type": "messenger",
                "count": 1,
                "links": "artifact",
                "profile_adaptation": False,
                "required_fields": ["Канал:", "От:", "Текст:", "Ссылки:"],
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    program_path.write_text(json.dumps(program, ensure_ascii=False), encoding="utf-8")
    return worldpack(root)


def test_v3_multichannel_turn_accepts_two_emails_and_one_message(tmp_path: Path):
    pack = write_multichannel_v3_world(tmp_path / "multichannel-v3")
    runtime = TrainingRuntimeService(
        pack,
        StateStore(str(tmp_path / "state.db"), "party-v3-valid", pack.state_seed_path),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1

    contract = runtime.prompt_contract(state)
    narrative = runtime.normalize_narrative(runtime.fallback_text(state), state)

    assert contract["schema_version"] == "rp-gateway.training-turn-contract.v2"
    assert [(item["type"], item["count"]) for item in contract["surfaces"]] == [
        ("email", 2),
        ("messenger", 1),
    ]
    assert '{"ПИСЬМО":2,"СООБЩЕНИЕ":1}' in training_turn_prompt_block(contract)
    assert runtime.validate_narrative(narrative, state) == []


def test_v3_multichannel_turn_rejects_wrong_declared_count(tmp_path: Path):
    pack = write_multichannel_v3_world(tmp_path / "multichannel-v3-count")
    runtime = TrainingRuntimeService(
        pack,
        StateStore(str(tmp_path / "state.db"), "party-v3-count", pack.state_seed_path),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1
    narrative = runtime.normalize_narrative(runtime.fallback_text(state).replace(V3_EMAIL_TWO, "", 1), state)

    assert runtime.validate_narrative(narrative, state) == [
        "Training turn must contain exactly 2 ПИСЬМО block(s)."
    ]
    assert runtime.hard_violations(narrative, state) == []
    instruction = runtime.repair_instruction(narrative, state)
    assert "Верни полный исправленный ответ" in instruction
    assert "В полном исправленном ответе используй состав блоков" in instruction
    assert "Перепиши весь visible response" not in instruction
    assert '{"ПИСЬМО":2,"СООБЩЕНИЕ":1}' in instruction


def test_v3_turn_rejects_undeclared_surface_marker(tmp_path: Path):
    root = tmp_path / "single-surface-v3"
    pack = write_multichannel_v3_world(root)
    program_path = root / "training" / "program.json"
    program = json.loads(program_path.read_text(encoding="utf-8"))
    program["turns"][0]["surfaces"] = [program["turns"][0]["surfaces"][0]]
    program["turns"][0]["fallback"] = f"{V3_EMAIL_ONE}\n\n{V3_EMAIL_TWO}"
    program_path.write_text(json.dumps(program, ensure_ascii=False), encoding="utf-8")
    pack = worldpack(root)
    runtime = TrainingRuntimeService(
        pack,
        StateStore(str(tmp_path / "state.db"), "party-v3-marker", pack.state_seed_path),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1
    narrative = runtime.normalize_narrative(f"{runtime.fallback_text(state)}\n\n{V3_MESSAGE}", state)

    assert runtime.hard_violations(narrative, state) == [
        "Training turn contains undeclared СООБЩЕНИЕ block(s)."
    ]


def test_v3_mixed_link_policies_keep_missing_no_link_marker_hard(tmp_path: Path):
    pack = write_multichannel_v3_world(tmp_path / "multichannel-v3-links")
    runtime = TrainingRuntimeService(
        pack,
        StateStore(str(tmp_path / "state.db"), "party-v3-links", pack.state_seed_path),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1
    narrative = runtime.fallback_text(state).replace(
        "Ссылки: нет\nТекст:\nПодтверди получение запросов.",
        "Ссылки: отсутствуют\nТекст:\nПодтверди получение запросов.",
        1,
    )

    assert "Training turn with disabled links must state 'Ссылки: нет'." in runtime.hard_violations(
        narrative,
        state,
    )
    assert runtime.repair_instruction(narrative, state) == ""


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


def test_v2_one_day_contract_hash_and_single_surface_compatibility(tmp_path: Path):
    pack = worldpack(WORLD_PACKS_ROOT / "awareness-one-day")
    runtime = TrainingRuntimeService(
        pack,
        StateStore(str(tmp_path / "state.db"), "party-v2-compatibility", pack.state_seed_path),
    )
    state = runtime.store.get_state()
    state["meta"]["turn"] = 1
    contract = runtime.prompt_contract(state)

    assert runtime.contract_hash == "cd49e0d9c5ff7e17c3a3d7ada868f5382633d509ab7ed4ec17b419d0a94cc59b"
    assert runtime.program["schema_version"] == "rp-training-program.v2"
    assert runtime.program["revision"] == 4
    assert contract["schema_version"] == "rp-gateway.training-turn-contract.v2"
    assert len(contract["surfaces"]) == 1
    assert runtime.validate_narrative(runtime.fallback_text(state), state) == []


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
