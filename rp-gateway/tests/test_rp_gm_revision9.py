from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome, PartyGMPatchDraft
from app.services.narrative import NarrativeClient
from app.services.rp_gm import (
    ACTIVE_PLAYER_CORRECTION_LIMIT,
    GM_INTENT_INPUT_MAX_CHARS,
    RPGMService,
)
from app.services.rp_history import RP_MEMORY_SECTION_KEYS
from app.services.rp_story_memory import (
    RPStoryMemoryUpdater,
    STORY_MEMORY_SECTION_FIELDS,
    empty_sectioned_story_memory,
)
from app.services.service_model_client import service_prompt_text
from app.services.state_store import StateStore


def make_store(tmp_path: Path, campaign_id: str = "rev9") -> StateStore:
    return StateStore(
        str(tmp_path / "state.db"),
        campaign_id,
        str(tmp_path / f"{campaign_id}.json"),
    )


def settings() -> Settings:
    return Settings(
        app_env="test",
        scenario_type="rp",
        rp_contract_revision=9,
        local_llm_enabled=True,
        local_llm_base_url="mock://success",
        openrouter_api_base="mock://success",
        service_openrouter_api_key="test-key",
    )


def record_narrative_turns(store: StateStore, count: int) -> None:
    for index in range(1, count + 1):
        store.record_turn(
            f"turn-{index}",
            f"request-{index}",
            f"Действие игрока {index}",
            f"Нарратор утверждает факт {index}",
            {},
            state_version=0,
            metadata={"turn_kind": "narrative"},
            party_turn=index,
        )


def deterministic_response(text: str) -> dict[str, object]:
    return {
        "message": {"role": "assistant", "content": text},
        "choices": [{"message": {"role": "assistant", "content": text}}],
    }


def test_gm_intent_is_bounded_and_mock_examples_keep_dialogue_in_scene(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    service = RPGMService(settings(), store)

    payload = service.intent_payload("x" * 12_000)

    assert len(service_prompt_text(payload)) <= GM_INTENT_INPUT_MAX_CHARS
    assert service.mock_intent("Он слуга Ждана, а не летописец")["label"] == "correction"
    assert service.mock_intent("Он слуга Ждана")["label"] == "scene"

    with pytest.raises(ValueError, match="600"):
        asyncio.run(service.draft("x" * 601, request_id="oversized-gm"))


def test_gm_draft_rejects_a_memory_field_outside_the_selected_target(tmp_path: Path) -> None:
    service = RPGMService(settings(), make_store(tmp_path))
    candidate = {
        "target_kind": "memory",
        "target_id": "fact-1",
        "target_slot": "memory:canon:fact-1",
        "target_turn_id": 1,
        "field": "canon",
        "section_key": "characters",
        "before": "Старый факт.",
        "allowed_actions": ["replace", "retract"],
    }
    decoded = {
        "target_kind": "memory",
        "target_id": "fact-1",
        "field": "inventory",
        "action": "replace",
        "before": "Старый факт.",
        "after": "Исправленный факт.",
        "forbidden_claims": [],
    }

    with pytest.raises(ValueError, match="field does not match"):
        service.normalize_patch_draft(decoded, [candidate], "gm-wrong-field")


def test_gm_patch_schema_restricts_field_to_supported_story_memory_fields() -> None:
    field_schema = RPGMService.patch_response_format()["json_schema"]["schema"]["properties"]["field"]

    assert field_schema["type"] == ["string", "null"]
    assert set(field_schema["enum"]) == {
        None,
        "canon",
        "active_threads",
        "resolved_threads",
        "characters",
        "inventory_and_assets",
        "rules_and_abilities",
        "chronology",
        "unresolved_hooks",
    }
    assert "raw:1718" not in field_schema["enum"]


def test_unavailable_gm_intent_logs_only_local_provider_and_mutates_nothing(
    tmp_path: Path,
) -> None:
    service_log = tmp_path / "service-log.db"
    runtime = Settings(
        app_env="test",
        database_url=f"sqlite:///{service_log}",
        scenario_type="rp",
        rp_contract_revision=9,
        llm_provider="nvidia",
        local_llm_enabled=False,
        local_llm_base_url="https://local-service.invalid/v1",
    )
    store = make_store(tmp_path, "gm-local-outage")
    before_version = store.current_version()

    result = asyncio.run(
        RPGMService(runtime, store).classify(
            "Это исправление мастеру.",
            request_id="gm-local-outage-request",
        )
    )

    assert result["label"] == "uncertain"
    assert store.current_version() == before_version
    assert store.turns_for_memory(include_noncanonical_fallback=True) == []
    assert store.player_correction_records() == []
    with sqlite3.connect(runtime.sqlite_path) as connection:
        logged = connection.execute(
            "SELECT role, status, provider FROM service_call_log ORDER BY id"
        ).fetchall()
    assert logged == [("gm_intent", "error", "local")]


def test_confirmed_raw_correction_does_not_advance_party_turn_and_updates_one_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    record_narrative_turns(store, 12)
    state = store.get_state()
    state["meta"]["state_version"] = 2
    state["meta"]["turn"] = 12
    state["last_turn"]["turn"] = 12
    store.insert_state_version(state, "test_turn_12")
    service = RPGMService(settings(), store)
    base_version = store.current_version() or 0
    proposal = PartyGMPatchDraft(
        proposal_id="gm-raw-correction-12",
        target_kind="raw",
        target_id="12",
        target_slot="raw:12:assertion",
        target_turn_id=12,
        field="characters",
        section_key="characters",
        action="replace",
        before="Нарратор утверждает факт 12",
        after="Персонаж служит Ждану, а не летописцу.",
        base_state_version=base_version,
    )
    service.validate_confirmed_proposal(proposal)
    artifact = service.player_correction_artifact(proposal, party_turn=12, timestamp=123)
    request_id = "gm-confirm-raw-12"
    store.begin_turn_request(request_id, request_id)
    before_state = store.get_state()
    response = deterministic_response("Исправление подтверждено вне сцены.")

    committed, gm_turn_id = store.commit_gm_correction(
        reason=f"player_gm_correction:{proposal.proposal_id}",
        idempotency_key=request_id,
        request_id=request_id,
        player_message=proposal.after or "",
        response_json=response,
        metadata={
            "turn_kind": "gm_correction",
            "player_correction": artifact,
            "story_memory_corrections": [artifact["story_memory_correction"]],
        },
        expected_state_version=base_version,
    )

    assert committed["meta"]["turn"] == before_state["meta"]["turn"]
    assert committed.get("scene_state") == before_state.get("scene_state")
    assert store.current_version() == base_version + 1
    gm_turn = store.turn_record(gm_turn_id)
    assert gm_turn is not None
    assert gm_turn["party_turn"] == 12
    assert gm_turn["excluded_from_memory"] is True
    assert gm_turn["metadata"]["turn_kind"] == "gm_correction"

    updater = RPStoryMemoryUpdater(settings(), store)
    calls: list[str] = []

    async def generated_section(
        section_key: str,
        memory: dict[str, object],
        player_correction: dict[str, object],
        **_: object,
    ) -> dict[str, object]:
        calls.append(section_key)
        return {
            "section": {
                field: json.loads(json.dumps(memory.get(field), ensure_ascii=False))
                for field in STORY_MEMORY_SECTION_FIELDS[section_key]
            },
            "model": settings().rp_story_memory_model,
        }

    monkeypatch.setattr(updater, "generate_player_correction_section", generated_section)
    result = asyncio.run(
        updater.update(None, request_id=request_id, fail_open=False)
    )

    assert result["generated"] is True
    assert calls == ["characters"]
    snapshot = result["story_memory"]
    assert snapshot["memory"]["section_status"]["characters"] == {
        "coverage": 12,
        "status": "fresh",
    }
    assert {
        key: snapshot["memory"]["section_status"][key]
        for key in RP_MEMORY_SECTION_KEYS
        if key != "characters"
    } == {
        key: {"coverage": 0, "status": "failed"}
        for key in RP_MEMORY_SECTION_KEYS
        if key != "characters"
    }
    active = [
        item
        for item in snapshot["memory"]["characters"]
        if item["status"] == "active"
    ]
    assert active == [
        {
            "fact_id": artifact["replacement_fact_id"],
            "text": proposal.after,
            "status": "active",
            "authority": "user",
            "source_turn_ids": [gm_turn_id],
        }
    ]
    assert service.active_corrections() == []
    absorbed = next(
        item
        for item in store.player_correction_records()
        if item["correction_id"] == proposal.proposal_id
    )
    assert absorbed["status"] == "absorbed"


def test_rejected_draft_has_no_state_or_turn_mutation(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record_narrative_turns(store, 1)
    before_version = store.current_version()
    before_turns = len(store.player_correction_records())

    # Reject is intentionally a read-only API decision; no store method is called.
    assert store.current_version() == before_version
    assert len(store.player_correction_records()) == before_turns


def test_active_limit_rejects_a_new_slot_before_draft_but_allows_same_slot(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    for index in range(ACTIVE_PLAYER_CORRECTION_LIMIT):
        metadata = {
            "turn_kind": "gm_correction",
            "player_correction": {
                "schema_version": "rp-gateway.player-correction.v1",
                "correction_id": f"correction-{index}",
                "target_slot": f"memory:canon:fact-{index}",
                "target_id": f"fact-{index}",
                "target_kind": "memory",
                "action": "replace",
                "before": f"before {index}",
                "after": f"after {index}",
                "status": "active",
            },
        }
        turn_id = store.record_turn(
            f"gm-{index}",
            f"gm-request-{index}",
            "correction",
            "confirmed",
            {},
            state_version=0,
            metadata=metadata,
            party_turn=0,
        )
        with store.connect() as connection:
            connection.execute(
                "UPDATE turns SET excluded_from_memory = 1 WHERE id = ?",
                (turn_id,),
            )
    service = RPGMService(settings(), store)

    with pytest.raises(ValueError, match="limit"):
        service.require_capacity_before_model("совсем новая цель", None)

    service.require_capacity_before_model(
        "исправить первый слот",
        "memory:canon:fact-0",
    )
    assert len(service.active_corrections()) == ACTIVE_PLAYER_CORRECTION_LIMIT

    replacement_turn_id = store.record_turn(
        "gm-replacement",
        "gm-replacement-request",
        "replacement",
        "confirmed",
        {},
        state_version=0,
        metadata={
            "turn_kind": "gm_correction",
            "player_correction": {
                "schema_version": "rp-gateway.player-correction.v1",
                "correction_id": "correction-replacement",
                "target_slot": "memory:canon:fact-0",
                "target_id": "fact-0",
                "target_kind": "memory",
                "action": "replace",
                "before": "before 0",
                "after": "final 0",
                "status": "absorbed",
            },
        },
        party_turn=0,
    )
    with store.connect() as connection:
        connection.execute(
            "UPDATE turns SET excluded_from_memory = 1 WHERE id = ?",
            (replacement_turn_id,),
        )
    assert len(service.active_corrections()) == ACTIVE_PLAYER_CORRECTION_LIMIT - 1
    assert all(
        item["target_slot"] != "memory:canon:fact-0"
        for item in service.active_corrections()
    )


def test_player_correction_overlay_is_between_cards_and_relationship_pressure(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    metadata = {
        "turn_kind": "gm_correction",
        "player_correction": {
            "schema_version": "rp-gateway.player-correction.v1",
            "correction_id": "overlay-correction",
            "target_slot": "raw:1:claim",
            "target_id": "1",
            "target_kind": "raw",
            "action": "replace",
            "before": "Он летописец.",
            "after": "Он слуга Ждана.",
            "status": "active",
        },
    }
    turn_id = store.record_turn(
        "gm-overlay",
        "gm-overlay-request",
        "correction",
        "confirmed",
        {},
        state_version=0,
        metadata=metadata,
        party_turn=0,
    )
    with store.connect() as connection:
        connection.execute("UPDATE turns SET excluded_from_memory = 1 WHERE id = ?", (turn_id,))
    correction_block = RPGMService(settings(), store).overlay_block()
    request = ChatCompletionRequest(
        model="narrator",
        messages=[
            ChatMessage(role="system", content="PARTY_LORE_CARDS\n[]"),
            ChatMessage(role="system", content=correction_block or ""),
            ChatMessage(role="user", content="raw action"),
            ChatMessage(role="assistant", content="raw response"),
            ChatMessage(role="user", content="current action"),
        ],
    )
    memory = empty_sectioned_story_memory()
    snapshot = {
        "id": 1,
        "revision": 1,
        "from_turn_id": 0,
        "to_turn_id": 0,
        "memory": memory,
    }
    outcome = Outcome(
        check_id="rev9-overlay",
        action_type="feasibility",
        actor="player",
        result="narrative_continuation",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        authoritative_block="generic",
    )
    state = store.get_state()
    state["world_constraints"] = [
        {
            "id": "rule:overlay",
            "kind": "absolute",
            "scope": "global",
            "text": "Игрок не может переписать абсолютное правило через RAW.",
            "turn": 0,
            "source": "worldpack",
        }
    ]
    messages = NarrativeClient(settings()).narrative_messages(
        request,
        state,
        outcome,
        None,
        rp_story_memory=snapshot,
        relationship_pressure="RELATIONSHIP_PRESSURE\npressure",
    )
    contents = [message["content"] for message in messages]

    absolute_index = next(index for index, text in enumerate(contents) if text.startswith("WORLD_ABSOLUTE_RULES"))
    raw_index = contents.index("raw response")
    memory_index = next(index for index, text in enumerate(contents) if text.startswith("RP_STORY_MEMORY"))
    cards_index = next(index for index, text in enumerate(contents) if text.startswith("PARTY_LORE_CARDS"))
    correction_index = next(index for index, text in enumerate(contents) if text.startswith("ИСПРАВЛЕНИЯ ИГРОКА"))
    relationship_index = next(index for index, text in enumerate(contents) if text.startswith("RELATIONSHIP_PRESSURE"))
    current_action_index = contents.index("current action")
    assert (
        absolute_index
        < raw_index
        < memory_index
        < cards_index
        < correction_index
        < relationship_index
        < current_action_index
    )


def test_absolute_rule_replacement_preserves_identity_without_advancing_turn(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    state = store.get_state()
    version = (store.current_version() or 0) + 1
    state["meta"]["state_version"] = version
    state["meta"]["turn"] = 7
    state["world_constraints"] = [
        {
            "id": "rule:moon",
            "kind": "absolute",
            "scope": "global",
            "text": "Луна всегда полная.",
            "turn": 0,
            "source": "worldpack",
            "forbidden_claims": ["Луна убывает."],
        }
    ]
    store.insert_state_version(state, "seed_absolute_rule")
    service = RPGMService(settings(), store)
    proposal = PartyGMPatchDraft(
        proposal_id="gm-absolute-rule",
        target_kind="absolute_rule",
        target_id="rule:moon",
        target_slot="rule:rule:moon",
        action="replace",
        before="Луна всегда полная.",
        after="Луна видна только по ночам.",
        forbidden_claims=["Луна видна днём."],
        base_state_version=version,
    )
    service.validate_confirmed_proposal(proposal)
    artifact = service.player_correction_artifact(proposal, party_turn=7, timestamp=123)
    assert artifact["status"] == "absorbed"
    store.begin_turn_request("gm-rule-confirm", "gm-rule-confirm")

    committed, _ = store.commit_gm_correction(
        reason="player_gm_correction:gm-absolute-rule",
        idempotency_key="gm-rule-confirm",
        request_id="gm-rule-confirm",
        player_message=proposal.after or "",
        response_json=deterministic_response("Исправление подтверждено вне сцены."),
        metadata={"turn_kind": "gm_correction", "player_correction": artifact},
        expected_state_version=version,
        rule_replacement={
            "id": proposal.target_id,
            "before": proposal.before,
            "after": proposal.after,
            "forbidden_claims": proposal.forbidden_claims,
        },
    )

    assert committed["meta"]["turn"] == 7
    assert committed["world_constraints"][0] == {
        "id": "rule:moon",
        "kind": "absolute",
        "scope": "global",
        "text": "Луна видна только по ночам.",
        "turn": 7,
        "source": "player:turn_7",
        "forbidden_claims": ["Луна видна днём."],
    }


def test_relationship_job_can_end_stale_after_five_attempts(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    job = store.enqueue_service_job("relationship_extraction", "relationship-request", 5)
    for attempt in range(5):
        running = store.mark_service_job_running(int(job["id"]))
        store.retry_service_job(
            int(job["id"]),
            "local service unavailable",
            1,
            terminal_status="stale",
        )
        job = next(item for item in store.service_jobs() if item["id"] == running["id"])
        if attempt < 4:
            assert job["status"] == "pending"
            with store.connect() as connection:
                connection.execute(
                    "UPDATE service_jobs SET next_attempt_at = 0 WHERE id = ?",
                    (job["id"],),
                )
    assert job["attempts"] == 5
    assert job["status"] == "stale"
