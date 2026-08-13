from __future__ import annotations

import json
import sqlite3
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome
from app.services.adjudicator import Adjudicator
from app.services.narrative import NarrativeClient
from app.services.service_model_client import ServiceModelClient
from app.services.state_store import StateStore
from app.services.turn_trace import TurnTraceAssembler, json_changes


def trace_store(tmp_path: Path, campaign_id: str = "party-trace") -> StateStore:
    return StateStore(
        str(tmp_path / "rp_gateway.db"),
        campaign_id,
        str(tmp_path / "state" / campaign_id / "current.json"),
    )


def party(
    revision: int = 0,
    scenario_type: str = "rp",
    contract_version: str = "rp-core.v1",
) -> SimpleNamespace:
    return SimpleNamespace(
        id="party_1",
        title="Trace Party",
        scenario_type=scenario_type,
        rp_contract_version=contract_version,
        rp_contract_revision=revision,
    )


def record_request_and_turn(store: StateStore, request_id: str = "req_trace_1") -> int:
    acquired = store.begin_turn_request("idem-trace-1", request_id)
    assert acquired["acquired"] is True
    state = store.get_state()
    state.setdefault("meta", {})["state_version"] = 1
    state["meta"]["turn"] = 1
    state["player"] = {"status": "ready"}
    store.insert_state_version(state, reason=f"turn:{request_id}")
    response = {
        "id": "completion-1",
        "choices": [{"message": {"role": "assistant", "content": "Ответ"}}],
    }
    turn_id = store.record_turn(
        "idem-trace-1",
        request_id,
        "Действую",
        "Ответ",
        response,
        1,
        prompt_messages=[{"role": "system", "content": "Rules"}, {"role": "user", "content": "Действую"}],
        metadata={"turn_kind": "narrative", "rp_contract_revision": 3},
        party_turn=1,
    )
    store.complete_turn_request("idem-trace-1", response)
    return turn_id


def test_json_changes_reports_exact_before_and_after() -> None:
    assert json_changes(
        {"player": {"status": "ready", "items": ["key"]}},
        {"player": {"status": "wounded", "items": ["key", "map"]}},
    ) == [
        {"path": "$.player.items[1]", "operation": "add", "before": None, "after": "map"},
        {"path": "$.player.status", "operation": "replace", "before": "ready", "after": "wounded"},
    ]


def test_narrative_trace_captures_exact_policy_payload_and_raw_response(tmp_path: Path) -> None:
    events: list[dict[str, object]] = []
    settings = Settings(
        app_env="test",
        database_url=f"sqlite:///{tmp_path / 'trace.db'}",
        world_state_path=str(tmp_path / "state.json"),
        party_state_root=str(tmp_path / "parties"),
        worldpacks_path=str(tmp_path / "worldpacks"),
        showroom_cover_dir=str(tmp_path / "covers"),
        nvidia_api_base="mock://success",
        nvidia_api_key="trace-secret",
        narrative_model="deepseek/deepseek-v4-flash",
        llm_provider="openrouter",
        scenario_type="rp",
    )
    request = ChatCompletionRequest(
        model=settings.narrative_model,
        messages=[ChatMessage(role="user", content="Открываю дверь. Authorization: Bearer trace-secret")],
        stream=False,
    )
    outcome = Outcome(
        check_id="trace-check",
        action_type="narrative",
        actor="player",
        result="narrative_continuation",
        roll=0,
        difficulty=0,
        final_score=0,
        modifiers={},
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: continue",
    )

    pressure = "RELATIONSHIP_PRESSURE\nRELATIONSHIP_EVENT_RESOLUTION required"
    result = asyncio.run(
        NarrativeClient(settings, trace_recorder=events.append).complete(
            request,
            {"meta": {"campaign_id": "trace"}, "player": {}, "characters": {}},
            outcome,
            None,
            request_id="req_exact_payload",
            relationship_pressure=pressure,
        )
    )

    assert result["choices"]
    assert len(events) == 1
    event = events[0]
    payload = event["input"]["payload"]
    assert payload["stream"] is False
    assert payload["model"] == settings.narrative_model
    assert payload["provider"]["sort"] == "throughput"
    assert any(message["content"] == pressure for message in payload["messages"])
    assert "trace-secret" not in json.dumps(event, ensure_ascii=False)
    assert "[REDACTED]" in json.dumps(event, ensure_ascii=False)
    assert event["output"]["raw_response"].startswith("{")
    assert event["status"] == "completed"

    asyncio.run(
        NarrativeClient(settings, trace_recorder=events.append).complete(
            request,
            {"meta": {"campaign_id": "trace"}, "player": {}, "characters": {}},
            outcome,
            None,
            repair_instruction="Repair the response",
            failed_response_text="bad response",
            request_id="req_exact_repair_payload",
            relationship_pressure=pressure,
        )
    )
    repair_payload = events[1]["input"]["payload"]
    assert any(message["content"] == pressure for message in repair_payload["messages"])


def test_trace_recorder_failure_does_not_change_turn_result(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    settings = Settings(
        app_env="test",
        campaign_id="party-trace",
        scenario_type="novel",
        database_url=f"sqlite:///{tmp_path / 'rp_gateway.db'}",
        world_state_path=str(tmp_path / "state" / "party-trace" / "current.json"),
        party_state_root=str(tmp_path / "state"),
        worldpacks_path=str(tmp_path / "worldpacks"),
        showroom_cover_dir=str(tmp_path / "covers"),
        nvidia_api_base="mock://success",
        nvidia_api_key="test-key",
        service_nvidia_api_base="mock://success",
        service_nvidia_api_key="test-key",
        post_turn_helpers_inline=False,
    )

    def broken_trace(**_event: object) -> None:
        raise sqlite3.OperationalError("trace unavailable")

    store.record_trace_event = broken_trace  # type: ignore[method-assign]
    response = asyncio.run(
        Adjudicator(settings, store).handle_chat(
            ChatCompletionRequest(
                model=settings.narrative_model,
                messages=[ChatMessage(role="user", content="Продолжаю сцену")],
            ),
            authorization=None,
            idempotency_key="idem-trace-fail-open",
            request_id="req_trace_fail_open",
        )
    )

    assert response["choices"][0]["message"]["content"]
    assert store.get_turn_by_request_id("req_trace_fail_open") is not None


def test_adr026_post_commit_relationship_mutation_is_captured(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    settings = Settings(
        app_env="test",
        campaign_id="party-trace",
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=4,
        database_url=f"sqlite:///{tmp_path / 'rp_gateway.db'}",
        world_state_path=str(tmp_path / "state" / "party-trace" / "current.json"),
        party_state_root=str(tmp_path / "state"),
        worldpacks_path=str(tmp_path / "worldpacks"),
        showroom_cover_dir=str(tmp_path / "covers"),
        nvidia_api_base="mock://success",
        nvidia_api_key="test-key",
        service_nvidia_api_base="mock://success",
        service_nvidia_api_key="test-key",
        post_turn_helpers_inline=False,
    )

    class FakeRelationshipMechanics:
        @staticmethod
        def pressure_block(_party_turn: int, _names: dict[str, str]) -> None:
            return None

        @staticmethod
        def due_event_block(_party_turn: int, _names: dict[str, str]) -> None:
            return None

        @staticmethod
        def advance_turn(party_turn: int) -> list[dict[str, object]]:
            with store.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO character_badges(
                        campaign_id, character_id, badge_kind, badge_id,
                        party_turn, active, payload_json, created_at
                    ) VALUES(?, 'npc', 'status', 'adr026-post-commit', ?, 1, '{}', 1)
                    """,
                    (store.campaign_id, party_turn),
                )
            return []

    adjudicator = Adjudicator(settings, store)
    adjudicator.relationship_mechanics = FakeRelationshipMechanics()  # type: ignore[assignment]
    asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model=settings.narrative_model,
                messages=[ChatMessage(role="user", content="Продолжаю сцену")],
            ),
            authorization=None,
            idempotency_key="idem-adr026-post-commit",
            request_id="req_adr026_post_commit",
        )
    )

    with store.connect() as connection:
        mutation = connection.execute(
            """
            SELECT * FROM turn_state_mutations
            WHERE campaign_id = ? AND request_id = ? AND store_name = 'character_badges'
            """,
            (store.campaign_id, "req_adr026_post_commit"),
        ).fetchone()
    assert mutation is not None
    assert mutation["turn_id"] is not None
    assert mutation["lane"] == "main"
    assert mutation["reason"] == "post_commit_relationship_advance"
    assert mutation["before_json"] is None
    assert json.loads(mutation["after_json"])["badge_id"] == "adr026-post-commit"
    mutation_phase = next(
        phase
        for phase in TurnTraceAssembler(store, party(4)).trace("req_adr026_post_commit")["trace"]["phases"]
        if phase["phase_key"] == "projection_mutations:main"
    )
    assert mutation_phase["lane"] == "main"


def test_general_trace_events_redact_credentials_recursively(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    store.record_trace_event(
        request_id="req_redaction",
        phase_key="player_input",
        alignment_key="player_input",
        lane="main",
        event_type="player_input",
        status="completed",
        payload={
            "input": {
                "content": (
                    "Authorization: Bearer bearer-value; access_token=access-value; "
                    "secret: hidden passage; token=ancient-rune"
                ),
                "client_secret": "client-value",
                "nested": [{"x-api-key": "api-value", "token": "structured-token"}],
            }
        },
    )

    with store.connect() as connection:
        persisted = connection.execute("SELECT payload_json FROM turn_trace_events").fetchone()[0]
    for secret in ("bearer-value", "access-value", "client-value", "api-value", "structured-token"):
        assert secret not in persisted
    assert "[REDACTED]" in persisted
    assert "secret: hidden passage" in persisted
    assert "token=ancient-rune" in persisted


def test_adjudicator_redacts_bare_runtime_secret_from_input_and_assembly(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    secret = "bare-active-provider-key"
    settings = Settings(
        app_env="test",
        campaign_id="party-trace",
        scenario_type="novel",
        database_url=f"sqlite:///{tmp_path / 'rp_gateway.db'}",
        world_state_path=str(tmp_path / "state" / "party-trace" / "current.json"),
        party_state_root=str(tmp_path / "state"),
        worldpacks_path=str(tmp_path / "worldpacks"),
        showroom_cover_dir=str(tmp_path / "covers"),
        nvidia_api_base="mock://success",
        nvidia_api_key=secret,
        service_nvidia_api_base="mock://success",
        service_nvidia_api_key=secret,
        post_turn_helpers_inline=False,
    )

    asyncio.run(
        Adjudicator(settings, store).handle_chat(
            ChatCompletionRequest(
                model=settings.narrative_model,
                messages=[ChatMessage(role="user", content=f"Продолжаю сцену {secret}")],
            ),
            authorization=None,
            idempotency_key="idem-bare-secret",
            request_id="req_bare_secret",
        )
    )

    with store.connect() as connection:
        rows = connection.execute(
            """
            SELECT phase_key, payload_json FROM turn_trace_events
            WHERE campaign_id = ? AND request_id = ?
              AND phase_key IN ('player_input', 'gateway_assembly')
            ORDER BY phase_key
            """,
            (store.campaign_id, "req_bare_secret"),
        ).fetchall()
    assert {row["phase_key"] for row in rows} == {"player_input", "gateway_assembly"}
    for row in rows:
        assert secret not in row["payload_json"]
        assert "[REDACTED]" in row["payload_json"]


def test_request_identity_is_safe_unique_and_reused_for_failed_retry(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    with pytest.raises(ValueError, match="request_id"):
        store.begin_turn_request("idem-unsafe", "unsafe/request")
    first = store.begin_turn_request("idem-one", "req_one")
    assert first["acquired"] is True
    with pytest.raises(ValueError, match="different idempotency_key"):
        store.begin_turn_request("idem-two", "req_one")
    store.fail_turn_request("idem-one", "first attempt failed")
    retry = store.begin_turn_request("idem-one", "req_new_client_value")
    assert retry["acquired"] is True
    assert retry["retried"] is True
    assert retry["request_id"] == "req_one"
    store.record_turn(
        "idem-legacy",
        "req_legacy_root",
        "act",
        "ok",
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        store.current_version(),
        party_turn=1,
    )
    with pytest.raises(ValueError, match="different idempotency_key"):
        store.begin_turn_request("idem-after-legacy", "req_legacy_root")


def test_missing_diagnostic_table_cannot_roll_back_turn_commit(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    with store.connect() as connection:
        connection.execute("DROP TABLE turn_trace_events")
    response = {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    turn_id = store.record_turn(
        "idem-no-trace-table",
        "req_no_trace_table",
        "act",
        "ok",
        response,
        store.current_version() or 1,
        party_turn=1,
    )

    assert turn_id > 0
    assert store.get_turn_by_request_id("req_no_trace_table") is not None
    with store.connect() as connection:
        prompt_json = connection.execute(
            "SELECT prompt_json FROM turns WHERE id = ?", (turn_id,)
        ).fetchone()[0]
    assert prompt_json is None


def test_trace_events_link_to_turn_and_detail_uses_actual_phases(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    store.begin_turn_request("idem-trace-1", "req_trace_1")
    store.record_trace_event(
        request_id="req_trace_1",
        phase_key="player_input",
        alignment_key="player_input",
        lane="main",
        event_type="player_input",
        status="completed",
        payload={"input": {"content": "Действую"}},
        party_turn=1,
    )
    store.record_narrative_attempt(
        {
            "request_id": "req_trace_1",
            "status": "completed",
            "model": "model-a",
            "attempt_index": 1,
            "input": {"payload": {"messages": [{"role": "user", "content": "Действую"}]}},
            "output": {"raw_response": '{"choices":[]}'},
        }
    )
    turn_id = record_request_and_turn_after_existing_request(store)

    payload = TurnTraceAssembler(store, party(3)).trace("req_trace_1")
    trace = payload["trace"]

    assert trace["turn_id"] == turn_id
    assert trace["scenario_type"] == "rp"
    assert trace["rp_contract_version"] == "rp-core.v1"
    assert trace["rp_contract_revision"] == 3
    assert {phase["phase_key"] for phase in trace["phases"]} >= {
        "player_input",
        "narrator:attempt:1",
        "turn_commit",
    }
    narrator = next(phase for phase in trace["phases"] if phase["phase_key"] == "narrator:attempt:1")
    assert narrator["metadata"]["model"] == "model-a"
    assert narrator["annotations"] == []
    with store.connect() as connection:
        row = connection.execute(
            "SELECT turn_id, party_turn FROM turn_trace_events WHERE phase_key = 'narrator:attempt:1'"
        ).fetchone()
    assert dict(row) == {"turn_id": turn_id, "party_turn": 1}


def record_request_and_turn_after_existing_request(store: StateStore) -> int:
    response = {
        "id": "completion-1",
        "choices": [{"message": {"role": "assistant", "content": "Ответ"}}],
    }
    turn_id = store.record_turn(
        "idem-trace-1",
        "req_trace_1",
        "Действую",
        "Ответ",
        response,
        store.current_version() or 1,
        prompt_messages=[{"role": "user", "content": "Действую"}],
        metadata={"turn_kind": "narrative", "rp_contract_revision": 3},
        party_turn=1,
    )
    store.complete_turn_request("idem-trace-1", response)
    return turn_id


def test_failed_request_without_turn_is_visible_and_annotatable(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    store.begin_turn_request("idem-failed", "req_failed")
    store.record_trace_event(
        request_id="req_failed",
        phase_key="validation:final",
        alignment_key="validation",
        lane="main",
        event_type="validation",
        status="failed",
        payload={"output": {"valid": False, "violations": ["absolute_rule"]}},
    )
    store.fail_turn_request("idem-failed", "RuntimeError: absolute rule violation")
    assembler = TurnTraceAssembler(store, party(3))

    listed = assembler.list_traces()
    assert listed["traces"][0]["request_id"] == "req_failed"
    assert listed["traces"][0]["turn_id"] is None
    assert listed["traces"][0]["status"] == "failed"

    saved = assembler.add_annotation(
        request_id="req_failed",
        annotation_id="annotation:req_failed:1",
        phase_key="validation:final",
        body="Нарушение подтверждено",
        author_user_id="user_1",
    )
    duplicate = assembler.add_annotation(
        request_id="req_failed",
        annotation_id="annotation:req_failed:1",
        phase_key="validation:final",
        body="Нарушение подтверждено",
        author_user_id="user_1",
    )
    assert saved["duplicate"] is False
    assert duplicate["duplicate"] is True
    detail = assembler.trace("req_failed")["trace"]
    phase = next(item for item in detail["phases"] if item["phase_key"] == "validation:final")
    assert phase["annotations"][0]["body"] == "Нарушение подтверждено"
    with store.connect() as connection:
        annotation_count = connection.execute("SELECT COUNT(*) FROM turn_phase_annotations").fetchone()[0]
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type = 'turn_trace_annotation_added'"
        ).fetchone()[0]
    assert annotation_count == 1
    assert audit_count == 1


def test_succeeded_service_job_does_not_leave_trace_settling(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    record_request_and_turn(store)
    job = store.enqueue_service_job("rp_story_memory", "req_trace_1")
    store.mark_service_job_running(int(job["id"]))

    assembler = TurnTraceAssembler(store, party(3))
    assert assembler.list_traces()["traces"][0]["settling"] is True

    store.complete_service_job(int(job["id"]))

    assert assembler.list_traces()["traces"][0]["settling"] is False


def test_failed_request_exposes_state_changed_before_missing_commit(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    store.begin_turn_request("idem-orphan", "req_orphan")
    state = store.get_state()
    state.setdefault("meta", {})["state_version"] = 2
    state["player"] = {"status": "wounded"}
    store.insert_state_version(state, reason="turn:req_orphan")
    store.fail_turn_request("idem-orphan", "provider failed after state write")

    trace = TurnTraceAssembler(store, party()).trace("req_orphan")["trace"]

    delta = next(phase for phase in trace["phases"] if phase["phase_key"] == "state_delta")
    assert delta["details"]["committed_turn"] is False
    assert delta["output"]["after"]["player"]["status"] == "wounded"
    assert "state_changed_without_committed_turn" in trace["omissions"]


def test_annotation_rejects_unknown_phase_and_conflicting_id(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    store.begin_turn_request("idem-failed", "req_failed")
    store.record_trace_event(
        request_id="req_failed",
        phase_key="request_failed",
        alignment_key="request_terminal",
        lane="main",
        event_type="request_failed",
        status="failed",
        payload={"error": {"type": "RuntimeError"}},
    )
    store.fail_turn_request("idem-failed", "failed")
    assembler = TurnTraceAssembler(store, party())

    with pytest.raises(ValueError, match="phase not found"):
        assembler.add_annotation(
            request_id="req_failed",
            annotation_id="annotation:unknown:1",
            phase_key="invented",
            body="No",
            author_user_id=None,
        )
    assembler.add_annotation(
        request_id="req_failed",
        annotation_id="annotation:known:1",
        phase_key="request_failed",
        body="First",
        author_user_id=None,
    )
    with pytest.raises(ValueError, match="different content"):
        assembler.add_annotation(
            request_id="req_failed",
            annotation_id="annotation:known:1",
            phase_key="request_failed",
            body="Second",
            author_user_id=None,
        )


def test_annotation_idempotency_key_is_scoped_to_campaign(tmp_path: Path) -> None:
    first = trace_store(tmp_path, "party-one")
    second = trace_store(tmp_path, "party-two")
    for store in (first, second):
        store.begin_turn_request("idem-shared", "req-shared")
        store.record_trace_event(
            request_id="req-shared",
            phase_key="request_failed",
            alignment_key="request_terminal",
            lane="main",
            event_type="request_failed",
            status="failed",
            payload={},
        )
        store.fail_turn_request("idem-shared", "failed")

    for store in (first, second):
        saved = TurnTraceAssembler(store, party()).add_annotation(
            request_id="req-shared",
            annotation_id="annotation:shared:1",
            phase_key="request_failed",
            body="Same client key, isolated party",
            author_user_id=None,
        )
        assert saved["duplicate"] is False


def test_projection_mutation_keeps_exact_transition(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    before = store.trace_projection_snapshot()
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO character_axis_state(
                campaign_id, character_id, axis, band, band_since_turn, updated_at
            ) VALUES(?, 'npc', 'trust', 'wary', 1, 1)
            """,
            (store.campaign_id,),
        )
    count = store.capture_projection_changes(
        "req_projection",
        before,
        source="test",
        reason="axis changed",
    )
    assert count == 1
    with store.connect() as connection:
        row = connection.execute("SELECT * FROM turn_state_mutations").fetchone()
    assert row["store_name"] == "character_axis_state"
    assert json.loads(row["after_json"])["band"] == "wary"


def test_projection_mutations_keep_actual_main_and_background_lanes(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    store.begin_turn_request("idem-projection-lanes", "req_projection_lanes")
    before = store.trace_projection_snapshot()
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO character_axis_state(
                campaign_id, character_id, axis, band, band_since_turn, updated_at
            ) VALUES(?, 'npc', 'trust', 'wary', 1, 1)
            """,
            (store.campaign_id,),
        )
    store.capture_projection_changes(
        "req_projection_lanes",
        before,
        source="relationship_turn_advance",
        reason="main mutation",
        lane="main",
    )
    before = store.trace_projection_snapshot()
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE character_axis_state SET band = 'trusted', updated_at = 2
            WHERE campaign_id = ? AND character_id = 'npc' AND axis = 'trust'
            """,
            (store.campaign_id,),
        )
    store.capture_projection_changes(
        "req_projection_lanes",
        before,
        source="relationship_extraction",
        reason="background mutation",
        lane="background",
    )

    phases = TurnTraceAssembler(store, party()).trace("req_projection_lanes")["trace"]["phases"]
    mutation_phases = [phase for phase in phases if phase["alignment_key"] == "projection_mutations"]
    assert [(phase["phase_key"], phase["lane"]) for phase in mutation_phases] == [
        ("projection_mutations:main", "main"),
        ("projection_mutations:background", "background"),
    ]


def test_branch_envelope_is_isolated_and_revision_aware(tmp_path: Path) -> None:
    source = trace_store(tmp_path, "source-campaign")
    branch = trace_store(tmp_path, "branch-campaign")
    source.begin_turn_request("source-idem", "source-request")
    branch.begin_turn_request("branch-idem", "branch-request")
    payload = TurnTraceAssembler(
        branch,
        party(0),
        {"id": "branch_1", "label": "Candidate", "rp_contract_revision": 6},
    ).list_traces()

    assert payload["state_campaign_id"] == "branch-campaign"
    assert payload["branch"]["rp_contract_revision"] == 6
    assert [item["request_id"] for item in payload["traces"]] == ["branch-request"]


@pytest.mark.parametrize("scenario_type", ["training", "novel"])
def test_non_rp_trace_contract_uses_scenario_type_without_rp_fields(
    tmp_path: Path,
    scenario_type: str,
) -> None:
    store = trace_store(tmp_path, f"{scenario_type}-campaign")
    request_id = f"req_{scenario_type}"
    idempotency_key = f"idem-{scenario_type}"
    response = {"choices": [{"message": {"role": "assistant", "content": "done"}}]}
    store.begin_turn_request(idempotency_key, request_id)
    store.record_turn(
        idempotency_key,
        request_id,
        "act",
        "done",
        response,
        store.current_version() or 1,
        metadata={
            "turn_kind": "narrative",
            "scenario_type": scenario_type,
            "rp_contract_version": "rp-core.v2",
            "rp_contract_revision": 6,
        },
        party_turn=1,
    )
    store.complete_turn_request(idempotency_key, response)
    assembler = TurnTraceAssembler(
        store,
        party(6, scenario_type=scenario_type, contract_version="rp-core.v2"),
    )

    summary = assembler.list_traces()["traces"][0]
    detail = assembler.trace(request_id)
    for trace in (summary, detail["trace"]):
        assert trace["scenario_type"] == scenario_type
        assert trace["rp_contract_version"] is None
        assert trace["rp_contract_revision"] is None
    assert detail["party"]["scenario_type"] == scenario_type
    assert detail["party"]["rp_contract_version"] is None
    assert detail["party"]["rp_contract_revision"] is None


def test_inherited_branch_turn_without_revision_keeps_source_revision(tmp_path: Path) -> None:
    store = trace_store(tmp_path, "branch-campaign")
    store.begin_turn_request("idem-legacy-branch", "req_legacy_branch")
    response = {"choices": [{"message": {"role": "assistant", "content": "legacy"}}]}
    store.record_turn(
        "idem-legacy-branch",
        "req_legacy_branch",
        "act",
        "legacy",
        response,
        store.current_version() or 1,
        metadata={"turn_kind": "narrative"},
        party_turn=1,
    )
    store.complete_turn_request("idem-legacy-branch", response)
    assembler = TurnTraceAssembler(
        store,
        party(0),
        {"id": "branch_candidate", "label": "Candidate", "rp_contract_revision": 6},
    )

    summary = assembler.list_traces()["traces"][0]
    detail = assembler.trace("req_legacy_branch")["trace"]
    assert summary["scenario_type"] == "rp"
    assert summary["rp_contract_version"] == "rp-core.v1"
    assert summary["rp_contract_revision"] == 0
    assert detail["scenario_type"] == "rp"
    assert detail["rp_contract_version"] == "rp-core.v1"
    assert detail["rp_contract_revision"] == 0


def test_failed_candidate_branch_request_uses_branch_revision(tmp_path: Path) -> None:
    store = trace_store(tmp_path, "branch-campaign")
    store.begin_turn_request("idem-candidate-failed", "req_candidate_failed")
    store.record_trace_event(
        request_id="req_candidate_failed",
        phase_key="request_failed",
        alignment_key="request_terminal",
        lane="main",
        event_type="request_failed",
        status="failed",
        payload={"error": {"type": "RuntimeError"}},
    )
    store.fail_turn_request("idem-candidate-failed", "candidate enforcement refused")
    assembler = TurnTraceAssembler(
        store,
        party(0),
        {"id": "branch_candidate", "label": "Candidate", "rp_contract_revision": 6},
    )

    assert assembler.list_traces()["traces"][0]["rp_contract_revision"] == 6
    assert assembler.trace("req_candidate_failed")["trace"]["rp_contract_revision"] == 6


def test_legacy_turn_marks_missing_attempt_capture_as_partial(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    record_request_and_turn(store)
    trace = TurnTraceAssembler(store, party()).trace("req_trace_1")["trace"]

    assert trace["capture_status"] == "partial"
    assert "narrator_attempts_not_captured" in trace["omissions"]
    assembly = next(phase for phase in trace["phases"] if phase["phase_key"] == "gateway_assembly")
    assert assembly["capture_status"] == "partial"


def test_migrated_legacy_service_call_remains_visible_by_turn_id(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    turn_id = record_request_and_turn(store)
    ServiceModelClient(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'rp_gateway.db'}",
            nvidia_api_base="mock://success",
            nvidia_api_key="test-key",
        )
    )
    with store.connect() as connection:
        connection.execute(
            """
            INSERT INTO service_call_log(
                party_id, turn_id, role, prompt_text, raw_response, created_at, status
            ) VALUES(?, ?, 'memory_summary', 'legacy prompt', 'legacy response',
                     '2026-08-01T00:00:00Z', 'completed')
            """,
            (store.campaign_id, turn_id),
        )

    trace = TurnTraceAssembler(store, party()).trace("req_trace_1")["trace"]
    service = next(phase for phase in trace["phases"] if phase["event_type"] == "service_model_call")
    assert service["input"]["messages"] == "legacy prompt"
    assert service["capture_status"] == "partial"


def test_list_trace_pagination_is_bounded(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    for index in range(4):
        store.begin_turn_request(f"idem-{index}", f"req-{index}")
    assembler = TurnTraceAssembler(store, party())
    first = assembler.list_traces(limit=2)
    second = assembler.list_traces(limit=2, before=first["next_before"])

    assert len(first["traces"]) == 2
    assert len(second["traces"]) == 2
    assert {item["request_id"] for item in first["traces"]}.isdisjoint(
        {item["request_id"] for item in second["traces"]}
    )


def test_pagination_cursor_does_not_skip_cross_table_timestamp_tie(tmp_path: Path) -> None:
    store = trace_store(tmp_path)
    store.begin_turn_request("idem-request-root", "req_request_root")
    response = {"choices": [{"message": {"role": "assistant", "content": "legacy"}}]}
    store.record_turn(
        "idem-orphan-turn",
        "req_orphan_turn",
        "act",
        "legacy",
        response,
        store.current_version() or 1,
        party_turn=1,
    )
    with store.connect() as connection:
        connection.execute("UPDATE turn_requests SET created_at = 100, updated_at = 100")
        connection.execute("UPDATE turns SET created_at = 100")
    assembler = TurnTraceAssembler(store, party())

    first = assembler.list_traces(limit=1)
    second = assembler.list_traces(limit=1, before=first["next_before"])

    assert first["traces"][0]["request_id"] == "req_request_root"
    assert second["traces"][0]["request_id"] == "req_orphan_turn"


def test_state_store_trace_migration_preserves_existing_database(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE campaigns(id TEXT PRIMARY KEY, created_at INTEGER NOT NULL)")
        connection.execute("INSERT INTO campaigns VALUES('legacy', 1)")
    store = StateStore(str(database), "legacy", str(tmp_path / "state.json"))
    with store.connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'turn_%'"
            )
        }
        mutation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(turn_state_mutations)")
        }
    assert {"turn_trace_events", "turn_state_mutations", "turn_phase_annotations"} <= tables
    assert "lane" in mutation_columns


def write_trace_worldpack(tmp_path: Path) -> None:
    worldpack = tmp_path / "worldpacks" / "demo"
    (worldpack / "prompts").mkdir(parents=True)
    (worldpack / "world-info").mkdir()
    seed = {
        "meta": {"campaign_id": "demo", "schema_version": "1.0.0", "state_version": 1, "turn": 0},
        "player": {},
        "characters": {},
        "factions": {},
        "locations": {},
        "resources": {},
        "relationships": {},
        "active_threads": [],
        "completed_threads": [],
        "world_constraints": [],
        "timeline": [],
        "last_turn": {},
        "uncertain_facts": [],
    }
    (worldpack / "manifest.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "title": "Demo",
                "player_role": "Tester",
                "scenario_types": {"recommended": "rp", "supported": ["rp"]},
                "files": {
                    "state_seed": "state-seed.json",
                    "gm_system": "prompts/gm-system.md",
                    "authors_note": "prompts/authors-note.md",
                    "opening_scene": "prompts/opening-scene.md",
                    "world_info": "world-info/index.md",
                },
            }
        ),
        encoding="utf-8",
    )
    (worldpack / "state-seed.json").write_text(json.dumps(seed), encoding="utf-8")
    (worldpack / "prompts" / "gm-system.md").write_text("Rules", encoding="utf-8")
    (worldpack / "prompts" / "authors-note.md").write_text("Note", encoding="utf-8")
    (worldpack / "prompts" / "opening-scene.md").write_text("Open", encoding="utf-8")
    (worldpack / "world-info" / "index.md").write_text("# Demo", encoding="utf-8")
    state_path = tmp_path / "state" / "current.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(seed), encoding="utf-8")


def test_party_trace_api_round_trip_and_no_store(tmp_path: Path) -> None:
    worldpack = tmp_path / "worldpacks" / "demo"
    (worldpack / "prompts").mkdir(parents=True)
    (worldpack / "world-info").mkdir()
    seed = {
        "meta": {"campaign_id": "demo", "schema_version": "1.0.0", "state_version": 1, "turn": 0},
        "player": {},
        "characters": {},
        "factions": {},
        "locations": {},
        "resources": {},
        "relationships": {},
        "active_threads": [],
        "completed_threads": [],
        "world_constraints": [],
        "timeline": [],
        "last_turn": {},
        "uncertain_facts": [],
    }
    (worldpack / "manifest.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "title": "Demo",
                "player_role": "Tester",
                "scenario_types": {"recommended": "rp", "supported": ["rp"]},
                "files": {
                    "state_seed": "state-seed.json",
                    "gm_system": "prompts/gm-system.md",
                    "authors_note": "prompts/authors-note.md",
                    "opening_scene": "prompts/opening-scene.md",
                    "world_info": "world-info/index.md",
                },
            }
        ),
        encoding="utf-8",
    )
    (worldpack / "state-seed.json").write_text(json.dumps(seed), encoding="utf-8")
    (worldpack / "prompts" / "gm-system.md").write_text("Rules", encoding="utf-8")
    (worldpack / "prompts" / "authors-note.md").write_text("Note", encoding="utf-8")
    (worldpack / "prompts" / "opening-scene.md").write_text("Open", encoding="utf-8")
    (worldpack / "world-info" / "index.md").write_text("# Demo", encoding="utf-8")
    state_path = tmp_path / "state" / "current.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(seed), encoding="utf-8")
    settings = Settings(
        app_env="test",
        campaign_id="default",
        database_url=f"sqlite:///{tmp_path / 'rp_gateway.db'}",
        world_state_path=str(state_path),
        party_state_root=str(tmp_path / "state" / "parties"),
        showroom_cover_dir=str(tmp_path / "covers"),
        worldpacks_path=str(tmp_path / "worldpacks"),
        nvidia_api_base="mock://success",
        nvidia_api_key="test-key",
        service_nvidia_api_base="mock://success",
        service_nvidia_api_key="test-key",
        post_turn_helpers_inline=True,
        auth_enabled=False,
    )
    client = TestClient(create_app(settings))
    model_id = client.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    character = client.post(
        "/api/player-characters",
        json={"worldpack_id": "demo", "name": "Mira", "description": "Tester", "profile": {}},
    ).json()["player_character"]
    created = client.post(
        "/api/parties",
        json={
            "title": "Trace API",
            "scenario_type": "rp",
            "worldpack_id": "demo",
            "player_character_id": character["id"],
            "model_profile_id": model_id,
        },
    )
    assert created.status_code == 200, created.text
    party_id = created.json()["party"]["id"]
    started = client.post(
        f"/api/parties/{party_id}/start",
        headers={"X-Request-ID": "req_api_start_trace"},
        json={"idempotency_key": "idem-api-start-trace"},
    )
    assert started.status_code == 200, started.text
    start_detail = client.get(f"/api/parties/{party_id}/turn-traces/req_api_start_trace")
    assert start_detail.status_code == 200, start_detail.text
    start_phases = {phase["phase_key"] for phase in start_detail.json()["trace"]["phases"]}
    assert {"player_input", "gateway_assembly", "narrator:attempt:1", "turn_commit"} <= start_phases
    played = client.post(
        f"/api/parties/{party_id}/messages",
        headers={"X-Request-ID": "req_api_trace"},
        json={"content": "Осматриваюсь", "idempotency_key": "idem-api-trace"},
    )
    assert played.status_code == 200, played.text

    listed = client.get(f"/api/parties/{party_id}/turn-traces")
    detail = client.get(f"/api/parties/{party_id}/turn-traces/req_api_trace")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    assert detail.json()["trace"]["request_id"] == "req_api_trace"
    phase_key = detail.json()["trace"]["phases"][0]["phase_key"]
    annotated = client.post(
        f"/api/parties/{party_id}/turn-traces/req_api_trace/annotations",
        json={
            "annotation_id": "annotation:req_api_trace:1",
            "phase_key": phase_key,
            "body": "Проверено",
        },
    )
    assert annotated.status_code == 200, annotated.text
    assert annotated.json()["annotation"]["author_user_id"] is None


def test_trace_api_is_admin_only_when_auth_is_enabled(tmp_path: Path) -> None:
    write_trace_worldpack(tmp_path)
    settings = Settings(
        app_env="test",
        campaign_id="default",
        database_url=f"sqlite:///{tmp_path / 'rp_gateway.db'}",
        world_state_path=str(tmp_path / "state" / "current.json"),
        party_state_root=str(tmp_path / "state" / "parties"),
        showroom_cover_dir=str(tmp_path / "covers"),
        worldpacks_path=str(tmp_path / "worldpacks"),
        nvidia_api_base="mock://success",
        nvidia_api_key="test-key",
        service_nvidia_api_base="mock://success",
        service_nvidia_api_key="test-key",
        post_turn_helpers_inline=True,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    app = create_app(settings)
    anonymous = TestClient(app)
    assert anonymous.get("/api/turn-traces/parties").status_code == 401

    admin = TestClient(app)
    assert admin.post(
        "/api/auth/login", json={"username": "admin", "password": "admin-secret"}
    ).status_code == 200
    for username in ("alice", "bob"):
        created_user = admin.post(
            "/api/admin/users",
            json={"username": username, "password": f"{username}-secret", "role": "user"},
        )
        assert created_user.status_code == 200, created_user.text

    alice = TestClient(app)
    bob = TestClient(app)
    assert alice.post(
        "/api/auth/login", json={"username": "alice", "password": "alice-secret"}
    ).status_code == 200
    assert bob.post(
        "/api/auth/login", json={"username": "bob", "password": "bob-secret"}
    ).status_code == 200
    model_id = alice.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    character = alice.post(
        "/api/player-characters",
        json={"worldpack_id": "demo", "name": "Alice", "description": "Tester", "profile": {}},
    ).json()["player_character"]
    party_id = alice.post(
        "/api/parties",
        json={
            "title": "Alice Trace",
            "scenario_type": "rp",
            "worldpack_id": "demo",
            "player_character_id": character["id"],
            "model_profile_id": model_id,
        },
    ).json()["party"]["id"]
    started = alice.post(
        f"/api/parties/{party_id}/start",
        headers={"X-Request-ID": "req_alice_start"},
        json={"idempotency_key": "idem-alice-start"},
    )
    assert started.status_code == 200, started.text
    checkpoint = alice.post(
        f"/api/parties/{party_id}/checkpoints", json={"label": "Trace branch base"}
    ).json()["checkpoint"]
    branch = alice.post(
        f"/api/parties/{party_id}/branches",
        json={"checkpoint_id": checkpoint["id"], "label": "Candidate"},
    ).json()["branch"]

    trace_paths = (
        "/api/turn-traces/parties",
        f"/api/turn-traces/parties/{party_id}/branches",
        f"/api/parties/{party_id}/turn-traces",
        f"/api/parties/{party_id}/turn-traces/req_alice_start",
    )
    for client in (alice, bob):
        for path in trace_paths:
            assert client.get(path).status_code == 403
        assert client.get(
            f"/api/parties/{party_id}/turn-traces", params={"branch_id": branch["id"]}
        ).status_code == 403

    admin_detail = admin.get(f"/api/parties/{party_id}/turn-traces/req_alice_start")
    assert admin_detail.status_code == 200, admin_detail.text
    phase_key = admin_detail.json()["trace"]["phases"][0]["phase_key"]
    annotation_url = f"/api/parties/{party_id}/turn-traces/req_alice_start/annotations"
    for client, expected_status in (
        (alice, 403),
        (bob, 403),
        (anonymous, 401),
    ):
        assert client.post(
            annotation_url,
            json={
                "annotation_id": f"annotation-denied-{expected_status}",
                "phase_key": phase_key,
                "body": "must not be stored",
            },
        ).status_code == expected_status

    assert admin.post(
        annotation_url,
        json={
            "annotation_id": "annotation-admin-allowed",
            "phase_key": phase_key,
            "body": "operator note",
        },
    ).status_code == 200

    assert admin.get(f"/api/parties/{party_id}/turn-traces").status_code == 200
    operator_parties = admin.get("/api/turn-traces/parties")
    assert party_id in {item["id"] for item in operator_parties.json()["parties"]}
    operator_branches = admin.get(f"/api/turn-traces/parties/{party_id}/branches")
    assert branch["id"] in {item["id"] for item in operator_branches.json()["branches"]}

    showroom = TestClient(app)
    assert showroom.get("/api/showroom/scenarios").status_code == 200
    for client in (anonymous, showroom):
        for path in trace_paths:
            assert client.get(path).status_code == 401
    assert showroom.post(
        annotation_url,
        json={
            "annotation_id": "annotation-showroom-denied",
            "phase_key": phase_key,
            "body": "must not be stored",
        },
    ).status_code == 401
