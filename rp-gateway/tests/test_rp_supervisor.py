from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome
from app.services.narrative import NarrativeClient
from app.services.rp_supervisor import (
    RP_SUPERVISOR_RULE_IDS,
    RPSupervisorService,
    apply_rp_supervisor_policy,
    rp_supervisor_service_payload,
    validate_rp_supervisor_contract,
)
from app.services.state_store import StateStore
import app.services.rp_supervisor as supervisor_module


def observe_contract() -> dict[str, object]:
    return {
        "schema_version": "rp-gateway.rp-supervisor.v1",
        "mode": "observe",
        "window_turns": 50,
        "cadence_turns": 8,
        "max_advisories": 2,
        "max_consecutive": 3,
        "confidence_threshold": 0.7,
        "retention_days": 30,
        "rules": [
            {"id": rule_id, "title": rule_id, "rubric": f"Шкала для {rule_id}."}
            for rule_id in RP_SUPERVISOR_RULE_IDS
        ],
    }


def enforce_contract() -> dict[str, object]:
    contract = observe_contract()
    contract["mode"] = "enforce"
    contract["rules"] = [
        {
            **rule,
            "corridor": {"min": 0.4, "max": 0.6},
            "advisory_below": f"Подними {rule['id']} без новых сюжетных фактов.",
            "advisory_above": f"Снизь {rule['id']} без новых сюжетных фактов.",
        }
        for rule in contract["rules"]
    ]
    return contract


def make_store(tmp_path: Path, *, turns: int = 0, campaign_id: str = "supervisor-party") -> StateStore:
    store = StateStore(
        str(tmp_path / "gateway.db"),
        campaign_id,
        str(tmp_path / f"{campaign_id}.json"),
    )
    for number in range(1, turns + 1):
        store.record_turn(
            idempotency_key=f"turn-{number}",
            request_id=f"req-{number}",
            player_message=f"Действие игрока {number}",
            narrative_response=f"Развитие сцены {number}.",
            response_json={"choices": [{"message": {"content": f"Развитие сцены {number}."}}]},
            state_version=2,
            party_turn=number,
            metadata={"turn_kind": "narrative"},
        )
    return store


def model_results(*, evidence_turn_id: int = 56) -> list[dict[str, object]]:
    scores = [0.0, 1.0, 0.1, 0.9, 0.5, 0.5]
    return [
        {
            "rule_id": rule_id,
            "score": scores[index],
            "confidence": 0.9,
            "evidence_turn_ids": [evidence_turn_id],
            "status": "ok",
        }
        for index, rule_id in enumerate(RP_SUPERVISOR_RULE_IDS)
    ]


def test_contract_is_closed_and_observe_has_no_enforcement_policy() -> None:
    normalized = validate_rp_supervisor_contract(observe_contract())
    assert normalized["mode"] == "observe"
    assert [rule["id"] for rule in normalized["rules"]] == list(RP_SUPERVISOR_RULE_IDS)

    invalid = observe_contract()
    invalid["rules"][0]["corridor"] = {"min": 0.4, "max": 0.6}
    with pytest.raises(ValueError, match="invalid shape"):
        validate_rp_supervisor_contract(invalid)

    normalized_enforce = validate_rp_supervisor_contract(enforce_contract())
    assert normalized_enforce["rules"][0]["corridor"] == {"min": 0.4, "max": 0.6}


def test_cadence_starts_at_56_and_exact_window_is_never_truncated(tmp_path: Path) -> None:
    store = make_store(tmp_path, turns=55)
    service = RPSupervisorService(Settings(scenario_type="rp"), store, observe_contract())
    assert service.should_enqueue("req-55") is False

    store.record_turn(
        idempotency_key="turn-56",
        request_id="req-56",
        player_message="Действие игрока 56",
        narrative_response="Развитие сцены 56.",
        response_json={},
        state_version=2,
        party_turn=56,
        metadata={"turn_kind": "narrative"},
    )
    assert service.should_enqueue("req-56") is True
    eligible = store.turns_for_memory()
    payload, _prompt = rp_supervisor_service_payload(observe_contract(), eligible[-50:])
    user_payload = json.loads(payload["messages"][1]["content"])
    assert len(user_payload["window"]) == 50
    assert user_payload["window"][0]["turn_id"] == 7
    assert user_payload["window"][-1]["turn_id"] == 56


def test_context_capacity_records_unchecked_without_calling_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, turns=56)
    monkeypatch.setattr(
        supervisor_module,
        "service_model_choice",
        lambda _settings: {
            "id": "too-small",
            "title": "Too small",
            "provider": "openrouter",
            "model": "service/model",
            "context_tokens": 1,
            "available": True,
        },
    )

    class ForbiddenClient:
        def __init__(self, *_args: object, **_kwargs: object):
            raise AssertionError("provider must not be called when the exact window does not fit")

    monkeypatch.setattr(supervisor_module, "ServiceModelClient", ForbiddenClient)
    result = asyncio.run(
        RPSupervisorService(Settings(scenario_type="rp"), store, observe_contract()).process_turn(
            {"request_id": "req-56", "attempts": 1}
        )
    )

    assert result["status"] == "unchecked"
    assert result["status_reason"] == "context_capacity"
    assert result["results"] == []
    assert result["advisories"] == []


def test_observe_call_persists_only_typed_result_and_never_builds_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, turns=56)
    captured: dict[str, object] = {}
    response = {
        "model": "service/model",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": json.dumps({"rules": model_results()}, ensure_ascii=False)},
            }
        ],
    }

    class FakeClient:
        def __init__(self, _settings: Settings):
            pass

        async def complete(self, **kwargs: object) -> SimpleNamespace:
            captured.update(kwargs)
            return SimpleNamespace(data=response)

    monkeypatch.setattr(supervisor_module, "ServiceModelClient", FakeClient)
    monkeypatch.setattr(
        supervisor_module,
        "service_model_choice",
        lambda _settings: {
            "id": "or-test",
            "title": "OpenRouter test",
            "provider": "openrouter",
            "model": "service/model",
            "context_tokens": 1_000_000,
            "available": True,
        },
    )
    settings = Settings(
        scenario_type="rp",
        service_model_choice="or-qwen-3.5-flash",
        service_openrouter_api_key="test-key",
    )
    service = RPSupervisorService(settings, store, observe_contract())
    result = asyncio.run(service.process_turn({"request_id": "req-56", "attempts": 1}))

    assert captured["trace"] is False
    assert captured["role"] == "rp_supervisor"
    sent_window = json.loads(captured["payload"]["messages"][1]["content"])["window"]
    assert len(sent_window) == 50
    assert result["status"] == "checked"
    assert len(result["results"]) == 6
    assert all(item["suppressed_reason"] == "observe_mode" for item in result["results"])
    assert result["advisories"] == []
    assert service.prompt_advisory() is None
    with sqlite3.connect(store.sqlite_path) as connection:
        row = connection.execute(
            "SELECT results_json, advisories_json, status_reason FROM rp_supervisor_evaluations"
        ).fetchone()
        assert row is not None
        assert len(json.loads(row[0])) == 6
        assert json.loads(row[1]) == []
        assert row[2] is None
        trace_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_call_log'"
        ).fetchone()
        assert trace_table is None


def test_enforcement_selects_two_and_suppresses_disagreement_and_fourth_repeat() -> None:
    contract = enforce_contract()
    results, advisories, _flags = apply_rp_supervisor_policy(
        contract,
        model_results(),
        previous_results=None,
        sentinel={"status": "unknown", "score": None},
    )
    assert [item["rule_id"] for item in advisories] == [
        "world_resistance",
        "turn_return_variety",
    ]
    assert len(advisories) == 2
    assert next(item for item in results if item["rule_id"] == "consequence_pressure")[
        "suppressed_reason"
    ] == "max_advisories"

    previous = [dict(item) for item in results]
    previous[0].update(
        {
            "direction": "below",
            "advisory_active": True,
            "consecutive_reassertions": 3,
        }
    )
    repeated, _advisories, flags = apply_rp_supervisor_policy(
        contract,
        model_results(),
        previous_results=previous,
        sentinel={"status": "unknown", "score": None},
    )
    world = next(item for item in repeated if item["rule_id"] == "world_resistance")
    assert world["suppressed_reason"] == "max_consecutive"
    assert world["reassertion_exhausted"] is True
    assert any(flag["code"] == "max_consecutive_reassertions" for flag in flags)

    disagreed, _advisories, flags = apply_rp_supervisor_policy(
        contract,
        model_results(),
        previous_results=None,
        sentinel={"status": "ok", "score": 0.0, "sample_size": 16},
    )
    variety = next(item for item in disagreed if item["rule_id"] == "turn_return_variety")
    assert variety["suppressed_reason"] == "sentinel_disagreement"
    assert any(flag["code"] == "turn_return_sentinel_disagreement" for flag in flags)


def test_advisory_follows_world_events_and_precedes_author_note() -> None:
    messages = NarrativeClient(
        Settings(
            scenario_type="rp",
            rp_contract_revision=11,
            world_authors_note="Авторская заметка.",
        )
    ).narrative_messages(
        request=ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="Действую.")],
            max_tokens=100,
        ),
        state={"meta": {}, "player": {}, "world_constraints": []},
        outcome=Outcome(
            check_id="supervisor-prompt",
            action_type="feasibility",
            actor="player",
            result="success",
            roll=10,
            difficulty=10,
            modifiers={},
            final_score=10,
            authoritative_block="",
        ),
        repair_instruction=None,
        relationship_pressure="RELATIONSHIP_PRESSURE\nДавление.",
        world_events="СОБЫТИЯ МИРА\nБлижайший горизонт:\n- Событие.",
        supervisor_advisory="RP_SUPERVISOR_ADVISORY\n- Измени только манеру возврата хода.",
    )
    contents = [message["content"] for message in messages]
    assert contents.index("СОБЫТИЯ МИРА\nБлижайший горизонт:\n- Событие.") < contents.index(
        "RP_SUPERVISOR_ADVISORY\n- Измени только манеру возврата хода."
    ) < contents.index("WORLD_AUTHORS_NOTE\nАвторская заметка.")
    assert contents[-1] == "Действую."


def test_rollback_invalidates_a_retrospective_whose_window_contains_rolled_back_turns(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path, turns=56)
    contract_hash = RPSupervisorService(
        Settings(scenario_type="rp"), store, observe_contract()
    ).contract_hash
    store.save_rp_supervisor_evaluation(
        {
            "request_id": "req-56",
            "source_turn_id": 56,
            "source_party_turn": 56,
            "story_turn_count": 56,
            "window_start_turn_id": 7,
            "window_end_turn_id": 56,
            "window_hash": "window-hash",
            "contract_hash": contract_hash,
            "mode": "observe",
            "provider": "openrouter",
            "model": "service/model",
            "status": "checked",
            "status_reason": None,
            "results": [],
            "advisories": [],
            "diagnostic_flags": [],
            "latency_ms": 1.0,
            "context_tokens": 100_000,
            "estimated_input_tokens": 1_000,
            "retention_days": 30,
        }
    )
    assert store.latest_rp_supervisor_evaluation(contract_hash=contract_hash) is not None

    store.rollback(1)

    assert store.latest_rp_supervisor_evaluation(contract_hash=contract_hash) is None
    with sqlite3.connect(store.sqlite_path) as connection:
        assert connection.execute(
            "SELECT invalidated FROM rp_supervisor_evaluations"
        ).fetchone()[0] == 1
