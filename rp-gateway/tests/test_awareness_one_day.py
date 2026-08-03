from app.models.schemas import Intent, Outcome
from app.main import party_start_state_patch
from app.services.intent_parser import IntentParser
from app.services.rule_engine import (
    AWARENESS_ONE_DAY_ID,
    AWARENESS_ONE_DAY_TURN_WINDOWS,
    RuleEngine,
    awareness_turn_window,
    is_awareness_one_day_campaign,
)
from app.services.validator import (
    AWARENESS_ONE_DAY_SITE_TURNS,
    OutputValidator,
    awareness_debrief_fallback,
    awareness_one_day_safe_fallback,
    safe_fallback,
)


def state_for(turn: int) -> dict:
    return {
        "meta": {"campaign_id": AWARENESS_ONE_DAY_ID, "turn": turn},
        "player": {
            "name": "Алексей",
            "description": "Инженер по анализу вредоносного кода и подготовке сигнатур.",
            "resources": {
                "current-turn-window": AWARENESS_ONE_DAY_TURN_WINDOWS.get(
                    turn, "итоговый разбор после хода 10"
                ),
                "security-score": 0,
                "roleplay-score": 0,
                "communication-score": 0,
                "total-score": 0,
                "safe-security-responses": 0,
                "role-aligned-responses": 0,
                "professional-responses": 0,
                "security-score-evidence": "",
                "roleplay-score-evidence": "",
                "communication-score-evidence": "",
                "unsafe-actions": 0,
                "credential-exposure": 0,
                "suspicious-artifacts-opened": 0,
                "confidential-disclosures": 0,
                "unnecessary-forwarding": 0,
            }
        },
    }


def values_for(patch) -> dict:
    return {operation.path: operation.value for operation in patch.patch}


def outcome() -> Outcome:
    return Outcome(
        check_id="training-test",
        action_type="feasibility",
        actor="player",
        result="deterministic_resolution",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        authoritative_block="",
    )


def test_one_day_schedule_uses_ten_windows_from_one_monday():
    state = state_for(1)
    assert is_awareness_one_day_campaign(state)
    assert awareness_turn_window(1, state, AWARENESS_ONE_DAY_ID) == "ход 1, понедельник, 09:00-09:30"
    assert awareness_turn_window(10, state, AWARENESS_ONE_DAY_ID) == "ход 10, понедельник, 17:15-18:00"


def test_one_day_party_start_uses_one_day_first_window():
    state = state_for(0)
    patch = party_start_state_patch(state, "party-test", AWARENESS_ONE_DAY_ID, "training")
    assert patch is not None
    values = values_for(patch)
    assert values["/player/resources/current-turn-window"] == "ход 1, понедельник, 09:00-09:30"


def test_one_day_safe_security_response_scores_all_three_components():
    state = state_for(3)
    intent = Intent(
        desired_outcome=(
            "Отвечаю, что не подтверждаю оплату без владельца задачи, "
            "проверяю запрос по официальному каналу и сообщаю в SOC адрес отправителя и время."
        )
    )
    _, patch = RuleEngine().resolve(
        state,
        intent,
        "safe-turn",
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
    )
    values = values_for(patch)
    assert values["/player/resources/security-score"] == 10
    assert values["/player/resources/roleplay-score"] == 3
    assert values["/player/resources/communication-score"] == 1
    assert values["/player/resources/total-score"] == 14
    assert "SOC/ДИБ" in values["/player/resources/security-score-evidence"]


def test_one_day_scores_noun_form_refusal_and_service_desk_role_boundary():
    state = state_for(3)
    state["player"]["description"] = "Специалист первой линии технической поддержки."
    intent = Intent(
        desired_outcome=(
            "Фиксирую отказ в выдаче прав без согласованной заявки в Service Desk и направляю запрос "
            "ответственному владельцу доступа."
        )
    )

    _, patch = RuleEngine().resolve(
        state,
        intent,
        "noun-refusal",
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
    )
    values = values_for(patch)

    assert values["/player/resources/security-score"] == 10
    assert values["/player/resources/roleplay-score"] == 3
    assert values["/player/resources/communication-score"] == 1
    assert "явный отказ" in values["/player/resources/security-score-evidence"]


def test_one_day_roleplay_uses_terms_from_stored_position_description():
    state = state_for(1)
    state["player"]["description"] = "Инженер по анализу вредоносного кода и подготовке сигнатур."
    intent = Intent(desired_outcome="Готовлю сигнатуру для обнаруженного образца и вернусь с итогом к 11:00.")

    _, patch = RuleEngine().resolve(
        state,
        intent,
        "role-description",
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
    )
    values = values_for(patch)

    assert values["/player/resources/roleplay-score"] == 3
    assert values["/player/resources/communication-score"] == 1
    assert "описанием должности" in values["/player/resources/roleplay-score-evidence"]


def test_one_day_scoring_reads_safe_action_after_first_500_characters():
    state = state_for(4)
    message = ("Контекст обращения без принятия решения. " * 20) + (
        "По внешней ссылке не перехожу, учетные данные не ввожу и направляю письмо в ДИБ."
    )
    intent = IntentParser().parse(message)

    assert len(intent.desired_outcome) > 500
    _, patch = RuleEngine().resolve(
        state,
        intent,
        "long-safe-action",
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
    )
    values = values_for(patch)
    assert values["/player/resources/security-score"] == 10


def test_one_day_credential_disclosure_is_recorded_without_security_points():
    state = state_for(7)
    intent = Intent(desired_outcome="Отвечаю службе поддержки и сообщаю им пароль и проверочный код.")
    _, patch = RuleEngine().resolve(
        state,
        intent,
        "unsafe-turn",
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
    )
    values = values_for(patch)
    assert "/player/resources/security-score" not in values
    assert values["/player/resources/unsafe-actions"] == 1
    assert values["/player/resources/credential-exposure"] == 1


def test_one_day_fallback_has_exactly_one_valid_surface_on_every_turn():
    validator = OutputValidator()
    expected_email_signatures = {
        1: "Анна Петрова\nРуководитель команды\nPT Security\nEmail: petrova@ptsecurity.com",
        2: "Анна Петрова\nРуководитель команды\nPT Security\nEmail: petrova@ptsecurity.com",
        4: "Служба уведомлений\nКорпоративный портал\nEmail: notice@ptsecurity-office.com",
        6: "Роман Иванов\nРуководитель проекта\nPT Security\nEmail: ivanov@ptsecurity.com",
        9: "Отдел закупок\nPT Security\nEmail: procurement@ptsecurity-billing.com",
    }
    for turn in range(1, 11):
        state = state_for(turn)
        text = awareness_one_day_safe_fallback(state)
        assert text.count("\nПИСЬМО\n") + int(text.startswith("ПИСЬМО\n")) + text.count("\nСООБЩЕНИЕ\n") == 1
        assert "Отправитель указан в поле «От»" not in text
        assert ("https://" in text) == (turn in AWARENESS_ONE_DAY_SITE_TURNS)
        if turn not in AWARENESS_ONE_DAY_SITE_TURNS:
            assert "Ссылки: нет" in text
        assert "анализу вредоносного кода" in text
        if turn in expected_email_signatures:
            assert f"Подпись:\n{expected_email_signatures[turn]}" in text
        result = validator.validate(
            text,
            outcome(),
            state,
            campaign_id=AWARENESS_ONE_DAY_ID,
            latest_user_message="Отвечаю по рабочей задаче.",
            scenario_type="training",
        )
        assert result.valid, result.violations


def test_one_day_fallback_orients_before_requesting_a_plan_and_uses_player_profile():
    first = awareness_one_day_safe_fallback(state_for(1))
    second = awareness_one_day_safe_fallback(state_for(2))

    assert "Алексей" in first
    assert "разобрать назначенный образец" in first
    assert "План на сегодня" not in first
    assert "план по этой задаче" in second


def test_one_day_fallback_changes_the_work_item_for_different_professions():
    analyst = state_for(1)
    analyst["player"]["description"] = "Аналитик данных и продуктовых метрик."
    tester = state_for(1)
    tester["player"]["description"] = "QA-тестировщик веб-приложений."

    analyst_text = awareness_one_day_safe_fallback(analyst)
    tester_text = awareness_one_day_safe_fallback(tester)

    assert "аналитическому блоку" in analyst_text
    assert "воспроизвести назначенный дефект" in tester_text
    assert analyst_text != tester_text


def test_one_day_validator_rejects_link_on_unscheduled_turn():
    state = state_for(1)
    text = awareness_one_day_safe_fallback(state).replace(
        "Ссылки: нет", "Ссылки: https://unexpected.example.test/open"
    )

    result = OutputValidator().validate(
        text,
        outcome(),
        state,
        campaign_id=AWARENESS_ONE_DAY_ID,
        latest_user_message="Приступаю к задаче.",
        scenario_type="training",
    )

    assert not result.valid
    assert "unscheduled turn must not contain a link" in " ".join(result.violations)


def test_one_day_validator_rejects_generic_message_that_ignores_player_role():
    state = state_for(1)
    text = """Ход 1. Понедельник, 09:00-09:30.

ПИСЬМО
Канал: корпоративная почта
От: Анна Петрова <petrova@ptsecurity.com>
Кому: Алексей
Дата/время: понедельник, 09:08
Тема: Общая задача
Вложения: нет
Ссылки: нет
Тело:
Доброе утро. Возьми любую текущую корпоративную задачу и вернись со статусом к 09:35.
Подпись:
Анна Петрова
Руководитель команды
PT Security
Email: petrova@ptsecurity.com

Что ты делаешь и как отвечаешь в рамках своей должности?"""

    result = OutputValidator().validate(
        text,
        outcome(),
        state,
        campaign_id=AWARENESS_ONE_DAY_ID,
        latest_user_message="Приступаю к задаче.",
        scenario_type="training",
    )

    assert not result.valid
    assert "stored player profession" in " ".join(result.violations)


def test_public_fallback_uses_explicit_one_day_worldpack_for_party_scoped_state():
    state = state_for(1)
    state["meta"]["campaign_id"] = "party_test"

    text = safe_fallback(
        outcome(),
        state,
        latest_user_message="Отвечаю по рабочей задаче.",
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
    )

    surface_count = (
        text.count("\nПИСЬМО\n")
        + int(text.startswith("ПИСЬМО\n"))
        + text.count("\nСООБЩЕНИЕ\n")
    )
    assert surface_count == 1
    result = OutputValidator().validate(
        text,
        outcome(),
        state,
        campaign_id=AWARENESS_ONE_DAY_ID,
        latest_user_message="Отвечаю по рабочей задаче.",
        scenario_type="training",
    )
    assert result.valid, result.violations


def test_one_day_debrief_reports_60_30_10_components():
    state = state_for(11)
    state["player"]["resources"].update(
        {
            "completion-status": "complete",
            "security-score": 50,
            "roleplay-score": 24,
            "communication-score": 8,
            "total-score": 82,
        }
    )
    text = awareness_debrief_fallback(state)
    assert text.startswith("Итоговый разбор.")
    assert "82 из 100" in text
    assert "50 из 60" in text
    assert "24 из 30" in text
    assert "8 из 10" in text


def test_one_day_party_scoped_debrief_uses_explicit_worldpack_id():
    state = state_for(11)
    state["meta"]["campaign_id"] = "party_ellina"
    state["player"]["resources"].update(
        {
            "completion-status": "complete",
            "security-score": 50,
            "roleplay-score": 24,
            "communication-score": 8,
            "total-score": 82,
        }
    )

    text = safe_fallback(
        outcome(),
        state,
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
    )

    assert "82 из 100" in text
    assert "50 из 60" in text
    assert "24 из 30" in text
    assert "8 из 10" in text
    assert "корректных эскалаций" not in text


def test_one_day_validator_rejects_debrief_scores_that_disagree_with_state():
    state = state_for(11)
    state["player"]["resources"].update(
        {
            "completion-status": "complete",
            "security-score": 50,
            "roleplay-score": 24,
            "communication-score": 8,
            "total-score": 82,
        }
    )
    wrong = """Итоговый разбор.

Итоговый результат: 8 из 100.
Информационная безопасность: 0 из 60.
Соблюдение роли и регламентов: 6 из 30.
Деловая коммуникация: 2 из 10.
"""

    validation = OutputValidator().validate(
        wrong,
        outcome(),
        state,
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
    )

    assert not validation.valid
    assert sum("canonical score" in violation for violation in validation.violations) == 4
