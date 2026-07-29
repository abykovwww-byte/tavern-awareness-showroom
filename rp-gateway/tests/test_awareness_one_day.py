from app.models.schemas import Intent, Outcome
from app.main import party_start_state_patch
from app.services.rule_engine import (
    AWARENESS_ONE_DAY_ID,
    AWARENESS_ONE_DAY_TURN_WINDOWS,
    RuleEngine,
    awareness_turn_window,
    is_awareness_one_day_campaign,
)
from app.services.validator import OutputValidator, awareness_debrief_fallback, awareness_one_day_safe_fallback, safe_fallback


def state_for(turn: int) -> dict:
    return {
        "meta": {"campaign_id": AWARENESS_ONE_DAY_ID, "turn": turn},
        "player": {
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
    state = state_for(2)
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
    for turn in range(1, 11):
        state = state_for(turn)
        text = awareness_one_day_safe_fallback(state)
        assert text.count("\nПИСЬМО\n") + int(text.startswith("ПИСЬМО\n")) + text.count("\nСООБЩЕНИЕ\n") == 1
        result = validator.validate(
            text,
            outcome(),
            state,
            campaign_id=AWARENESS_ONE_DAY_ID,
            latest_user_message="Отвечаю по рабочей задаче.",
            scenario_type="training",
        )
        assert result.valid, result.violations


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
