from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.main import world_clock_contract_for_party
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome, StatePatch
from app.services.adjudicator import Adjudicator
from app.services.narrative import NarrativeClient
from app.services.state_store import StateStore
from app.services.world_clock import (
    WORLD_CLOCK_PROMPT_MAX_CHARS,
    WORLD_CLOCK_SERVICE_PROMPT_MAX_CHARS,
    WorldClockBusy,
    confirm_world_clock_marker_state,
    initial_world_clock_state,
    validate_world_clock_contract,
    world_clock_prompt_projection,
    world_clock_service_payload,
)


def contract() -> dict[str, object]:
    return {
        "schema_version": "rp-gateway.world-clock.v1",
        "initial_date": "0964-04-18T09:00:00Z",
        "step_unit": "iso8601_duration",
        "max_step": "P2D",
        "markers": [
            {
                "id": "test.deadline-resolved",
                "label": "Игрок явно решил вопрос срока",
                "predicate": {
                    "type": "state_equals",
                    "path": "/player/resources/deadline-resolved",
                    "value": True,
                },
            }
        ],
        "events": [
            {
                "id": "test.deadline-closes",
                "condition": {"type": "date_gte", "date": "0964-04-18T12:00:00Z"},
                "summary": "Срок истёк.",
                "superseded_by": ["test.deadline-resolved"],
                "consequences": [
                    {
                        "type": "world_fact",
                        "id": "test.fact.deadline-closed",
                        "text": "Прежний срок истёк и больше не считается открытым.",
                    },
                    {
                        "type": "lore_card",
                        "key": "pressure:test-deadline",
                        "enabled": False,
                    },
                ],
            },
            {
                "id": "test.follow-up",
                "condition": {"type": "after_event", "event_id": "test.deadline-closes"},
                "summary": "Рынок отреагировал на истёкший срок.",
                "superseded_by": ["test.deadline-resolved"],
                "consequences": [
                    {
                        "type": "world_fact",
                        "id": "test.fact.market-reacted",
                        "text": "Рынок уже учитывает последствия истёкшего срока.",
                    }
                ],
            },
        ],
    }


def make_store(tmp_path: Path, campaign_id: str = "world-clock-party") -> StateStore:
    store = StateStore(
        str(tmp_path / "gateway.db"),
        campaign_id,
        str(tmp_path / f"{campaign_id}.json"),
    )
    state = store.get_state()
    state["meta"]["state_version"] = 2
    state["meta"]["turn"] = 1
    state["last_turn"]["turn"] = 1
    state["world_clock"] = initial_world_clock_state(contract())
    store.insert_state_version(state, "world_clock_fixture")
    store.create_lore_card(
        title="Test deadline",
        content="Authored pressure card.",
        keywords=["deadline"],
        always_on=False,
        enabled=True,
        source_turn_ids=[],
        authored_key="pressure:test-deadline",
    )
    return store


def test_world_clock_contract_is_closed_and_rejects_unknown_consequences() -> None:
    validated = validate_world_clock_contract(
        contract(),
        lore_card_keys={"pressure:test-deadline"},
    )
    assert validated["schema_version"] == "rp-gateway.world-clock.v1"

    invalid = contract()
    invalid["events"][0]["consequences"].append({"type": "move_character", "id": "npc"})
    with pytest.raises(ValueError, match="unsupported world clock consequence"):
        validate_world_clock_contract(invalid)

    unavoidable = contract()
    unavoidable["events"][0]["superseded_by"] = []
    with pytest.raises(ValueError, match="superseded_by"):
        validate_world_clock_contract(unavoidable)

    truncated_duration = contract()
    truncated_duration["max_step"] = "P2DT"
    with pytest.raises(ValueError, match="invalid world clock elapsed duration"):
        validate_world_clock_contract(truncated_duration)


def test_tick_caps_elapsed_applies_chained_events_and_lore_in_one_version(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    before_version = store.current_version()
    before_turn = store.get_state()["meta"]["turn"]

    result = store.apply_world_clock_tick(
        contract(),
        party_turn=1,
        elapsed="P5D",
        reason="service_model",
        request_id="clock-turn-1",
    )

    state = store.get_state()
    assert result["applied"] is True
    assert store.current_version() == before_version + 1
    assert state["meta"]["turn"] == before_turn
    assert state["world_clock"]["date"] == "0964-04-20T09:00:00Z"
    assert state["world_clock"]["last_elapsed"] == {
        "party_turn": 1,
        "elapsed": "P2D",
        "reason": "service_model",
    }
    assert state["world_clock"]["fired_event_ids"] == [
        "test.deadline-closes",
        "test.follow-up",
    ]
    assert [item["id"] for item in state["world_clock"]["world_facts"]] == [
        "test.fact.deadline-closed",
        "test.fact.market-reacted",
    ]
    assert store.lore_cards()[0]["enabled"] is False

    duplicate = store.apply_world_clock_tick(
        contract(),
        party_turn=1,
        elapsed="PT1H",
        reason="service_model",
        request_id="clock-turn-1",
    )
    assert duplicate["idempotent"] is True
    assert store.current_version() == before_version + 1


def test_confirmed_marker_supersedes_due_event_without_free_text(tmp_path: Path) -> None:
    store = make_store(tmp_path, "world-clock-marker")
    state = store.get_state()
    state["world_clock"]["date"] = "0964-04-19T09:00:00Z"
    candidate, lore_updates, occurred, duplicate = confirm_world_clock_marker_state(
        state,
        contract(),
        marker_id="test.deadline-resolved",
    )

    assert duplicate is False
    assert occurred == []
    assert lore_updates == []
    assert candidate["world_clock"]["event_statuses"] == {
        "test.deadline-closes": {
            "status": "superseded",
            "party_turn": 1,
            "date": "0964-04-19T09:00:00Z",
        },
        "test.follow-up": {
            "status": "superseded",
            "party_turn": 1,
            "date": "0964-04-19T09:00:00Z",
        },
    }


def test_world_events_are_consumed_only_by_successful_turn_commit(tmp_path: Path) -> None:
    store = make_store(tmp_path, "world-clock-announcement")
    store.apply_world_clock_tick(
        contract(),
        party_turn=1,
        elapsed="PT3H",
        reason="service_model",
        request_id="clock-source",
    )
    projection = world_clock_prompt_projection(store.get_state(), contract())
    assert projection is not None
    assert len(projection["block"]) <= WORLD_CLOCK_PROMPT_MAX_CHARS
    assert projection["event_ids"] == ["test.deadline-closes", "test.follow-up"]

    store.begin_turn_request("turn-2", "request-2")
    committed, _turn_id = store.commit_turn(
        StatePatch(turn=2, check_id="clock-consume-2", patch=[]),
        reason="turn:request-2",
        idempotency_key="turn-2",
        request_id="request-2",
        player_message="Продолжаю.",
        narrative_response="Сцена учитывает события мира.",
        response_json={"choices": []},
        expected_state_version=store.current_version(),
        metadata={"turn_kind": "narrative", "world_clock_events": projection["metadata"]},
        consumed_world_clock_event_ids=projection["event_ids"],
        post_commit_service_jobs=[("world_clock", 5)],
        party_turn=2,
    )

    assert committed["world_clock"]["pending_announcements"] == []
    assert committed["world_clock"]["event_statuses"]["test.deadline-closes"]["announced_party_turn"] == 2
    assert world_clock_prompt_projection(committed, contract())["metadata"]["occurred"] == []
    history = store.turn_history()
    assert history[-1]["metadata"]["world_clock_events"] == projection["metadata"]
    jobs = store.service_jobs()
    assert [(item["job_type"], item["party_turn"], item["max_attempts"]) for item in jobs] == [
        ("world_clock", 2, 5)
    ]


def test_world_clock_block_follows_relationships_and_precedes_author_note() -> None:
    settings = Settings(
        scenario_type="rp",
        rp_contract_revision=10,
        world_authors_note="Авторская заметка.",
    )
    messages = NarrativeClient(settings).narrative_messages(
        request=ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="Действую.")],
            max_tokens=100,
        ),
        state={"meta": {}, "player": {}, "world_constraints": []},
        outcome=Outcome(
            check_id="clock-prompt",
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
    )
    contents = [message["content"] for message in messages]
    assert contents.index("RELATIONSHIP_PRESSURE\nДавление.") < contents.index(
        "СОБЫТИЯ МИРА\nБлижайший горизонт:\n- Событие."
    ) < contents.index("WORLD_AUTHORS_NOTE\nАвторская заметка.")
    assert contents[-1] == "Действую."


def test_world_clock_service_prompt_is_bounded_to_last_turn_text() -> None:
    payload, prompt = world_clock_service_payload("secret-turn-text " * 1_000)
    assert len(prompt) <= WORLD_CLOCK_SERVICE_PROMPT_MAX_CHARS
    assert payload["max_tokens"] == 50
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert len(payload["messages"]) == 2
    assert "world_clock" not in payload["messages"][1]["content"]


def test_local_clock_outage_finishes_with_visible_pt0s_noop(tmp_path: Path) -> None:
    store = make_store(tmp_path, "world-clock-outage")
    store.record_turn(
        "turn-1",
        "clock-outage-request",
        "Жду у лавки.",
        "Проходит немного времени.",
        {"choices": []},
        store.current_version() or 2,
        metadata={"turn_kind": "narrative"},
        party_turn=1,
    )
    job = store.enqueue_service_job(
        "world_clock",
        "clock-outage-request",
        max_attempts=5,
        party_turn=1,
    )
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'gateway.db'}",
        scenario_type="rp",
        rp_contract_revision=10,
        llm_provider="nvidia",
        local_llm_enabled=False,
        local_llm_base_url="https://local-service.invalid/v1",
        service_job_retry_base_seconds=1,
        service_job_retry_max_seconds=1,
    )
    adjudicator = Adjudicator(
        settings,
        store,
        world_clock_contract=contract(),
    )

    for _ in range(5):
        with store.connect() as connection:
            connection.execute(
                "UPDATE service_jobs SET next_attempt_at = 0 WHERE id = ?",
                (job["id"],),
            )
        asyncio.run(adjudicator.drain_service_jobs(None, wait_for_retries=False))

    final_job = next(item for item in store.service_jobs() if item["id"] == job["id"])
    assert final_job["status"] == "succeeded"
    assert store.get_state()["world_clock"]["last_elapsed"] == {
        "party_turn": 1,
        "elapsed": "PT0S",
        "reason": "service_unavailable",
    }
    with sqlite3.connect(settings.sqlite_path) as connection:
        calls = connection.execute(
            "SELECT role, status, provider FROM service_call_log ORDER BY id"
        ).fetchall()
    assert calls == [("world_clock_elapsed", "error", "local")] * 5
    assert all(provider != "nvidia" for _, _, provider in calls)


def test_world_clock_jobs_are_selected_in_party_turn_order(tmp_path: Path) -> None:
    store = make_store(tmp_path, "world-clock-order")
    later = store.enqueue_service_job("world_clock", "turn-2", party_turn=2)
    earlier = store.enqueue_service_job("world_clock", "turn-1", party_turn=1)

    assert later["id"] < earlier["id"]
    assert store.due_service_job()["party_turn"] == 1


def test_world_clock_defers_while_a_gameplay_request_owns_state(tmp_path: Path) -> None:
    store = make_store(tmp_path, "world-clock-busy")
    store.begin_turn_request("main-turn-2", "main-request-2")

    with pytest.raises(WorldClockBusy, match="main gameplay turn"):
        store.apply_world_clock_tick(
            contract(),
            party_turn=1,
            elapsed="PT1H",
            reason="service_model",
            request_id="clock-busy",
        )

    job = store.enqueue_service_job("world_clock", "clock-busy", party_turn=1)
    running = store.mark_service_job_running(job["id"])
    assert running["attempts"] == 1
    store.defer_service_job(running["id"], retry_delay=1, error="main turn running")
    deferred = next(item for item in store.service_jobs() if item["id"] == job["id"])
    assert deferred["status"] == "pending"
    assert deferred["attempts"] == 0


def test_candidate_branch_revision_loads_declared_clock_while_base_revision_stays_dormant(
    tmp_path: Path,
) -> None:
    pack_root = tmp_path / "pack"
    pack_root.mkdir()
    manifest_path = pack_root / "manifest.json"
    clock_path = pack_root / "world-clock.json"
    manifest = {
        "rp_contract": {"schema_version": "rp-core.v2", "revision": 10},
        "files": {"world_clock": "world-clock.json"},
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    clock_path.write_text(json.dumps(contract()), encoding="utf-8")
    party = SimpleNamespace(
        scenario_type="rp",
        rp_contract_revision=8,
        worldpack=SimpleNamespace(manifest=manifest, manifest_path=str(manifest_path)),
    )

    assert world_clock_contract_for_party(party) is None
    assert world_clock_contract_for_party(party, effective_revision=10) == contract()
