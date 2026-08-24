from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.main import party_chat_request
from app.models.schemas import ChatCompletionRequest, ChatMessage, PartyMessageRequest
from app.services.narrative import NarrativeClient, response_text
from app.services.adjudicator import Adjudicator
from app.services.relationship_extraction import RelationshipExtractionService
from app.services.rp_story_memory import RPStoryMemoryUpdater
from app.services.state_store import StateStore
from test_gateway import client, create_demo_party
from test_revision7_commit_boundaries import revision_seven_worldpack
from test_scene_state import (
    authoritative_counts,
    provider_response,
    relationship_model,
    revision_seven_adjudicator,
    scene_bundle,
)


def fallback_memory_settings() -> Settings:
    return Settings(
        app_env="test",
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=7,
        service_model_choice="or-qwen-3.5-flash",
        openrouter_api_base="mock://success",
        service_openrouter_api_key="test-service-key",
        local_llm_enabled=False,
        rp_story_memory_update_turns=1,
        party_memory_retrieval_enabled=True,
        post_turn_helpers_inline=False,
    )


def latest_turn_row(store: StateStore) -> dict[str, Any]:
    with store.connect() as connection:
        row = connection.execute(
            "SELECT id, player_message, narrative_response, metadata_json, excluded_from_memory "
            "FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
            (store.campaign_id,),
        ).fetchone()
    assert row is not None
    result = dict(row)
    result["metadata"] = json.loads(result.pop("metadata_json"))
    return result


def test_prebundle_transport_failure_commits_visible_noncanonical_stale_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "committed-safe-fallback")
    adjudicator = Adjudicator(
        adjudicator.settings,
        store,
        relationship_model=relationship_model(),
    )
    provider_calls = 0
    relationship_advance_calls = 0

    async def transport_timeout(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal provider_calls
        provider_calls += 1
        raise httpx.TimeoutException("all narrator transports exhausted")

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    def unexpected_relationship_advance(*args: object, **kwargs: object) -> None:
        nonlocal relationship_advance_calls
        relationship_advance_calls += 1

    monkeypatch.setattr(adjudicator.narrative, "complete", transport_timeout)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    assert adjudicator.relationship_mechanics is not None
    monkeypatch.setattr(
        adjudicator.relationship_mechanics,
        "advance_turn",
        unexpected_relationship_advance,
    )
    before = authoritative_counts(store)
    before_state = store.get_state()

    result = asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Я остаюсь во дворе и жду помощи.")],
            ),
            authorization=None,
            idempotency_key="committed-safe-fallback",
            request_id="req-committed-safe-fallback",
            allow_gateway_fallback=True,
        )
    )

    fallback_text = response_text(result)
    assert provider_calls == 1
    assert result["gateway_fallback"] == {"reason": "timeout"}
    assert relationship_advance_calls == 0
    assert fallback_text
    assert {
        table: authoritative_counts(store)[table] - before[table]
        for table in ("state_versions", "state_patches", "turns")
    } == {"state_versions": 1, "state_patches": 1, "turns": 1}
    current = store.get_state()
    assert current["timeline"] == before_state["timeline"]
    assert current["player"]["resources"] == before_state["player"]["resources"]
    assert current["meta"]["turn"] == 15
    assert current["scene_state"]["location_id"] == "yard"
    assert current["scene_state"]["present_character_ids"] == ["gorazd"]
    assert current["scene_state"]["as_of_state_version"] == 1
    assert current["scene_state"]["as_of_party_turn"] == 14
    assert current["scene_state"]["stale"] is True
    assert current["scene_state"]["stale_reason"] == "safe_fallback"

    turn = latest_turn_row(store)
    metadata = turn["metadata"]
    assert metadata["fallback"] is True
    assert metadata["fallback_reason"] == "timeout"
    assert metadata["story_memory_canonical"] is False
    assert metadata["scene_state_stale"] is True
    assert metadata["scene_state_after"] == current["scene_state"]
    saved_request = store.get_turn_request("req-committed-safe-fallback")
    assert saved_request is not None
    assert saved_request["status"] == "completed"


def test_prebundle_connect_error_uses_bounded_public_and_private_reasons(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "connect-error-fallback")
    private_error_text = "PRIVATE provider host resolution detail"

    async def transport_connect_error(*args: object, **kwargs: object) -> dict[str, Any]:
        raise httpx.ConnectError(
            private_error_text,
            request=httpx.Request("POST", "https://provider.invalid/chat/completions"),
        )

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "complete", transport_connect_error)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    result = asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Я жду у ворот.")],
            ),
            authorization=None,
            idempotency_key="connect-error-fallback",
            request_id="req-connect-error-fallback",
            allow_gateway_fallback=True,
        )
    )

    assert result["gateway_fallback"] == {"reason": "http_error"}
    assert private_error_text not in json.dumps(result, ensure_ascii=False)
    turn = latest_turn_row(store)
    assert turn["metadata"]["fallback_reason"] == "network_error"
    assert turn["metadata"]["story_memory_canonical"] is False


def test_rollback_removes_noncanonical_marker_and_snapshot_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "fallback-rollback")
    player_text = "Я жду у ворот и не подтверждаю исход."

    async def transport_timeout(*args: object, **kwargs: object) -> dict[str, Any]:
        raise httpx.TimeoutException("transport exhausted before bundle")

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "complete", transport_timeout)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content=player_text)],
            ),
            authorization=None,
            idempotency_key="fallback-before-rollback",
            request_id="req-fallback-before-rollback",
            allow_gateway_fallback=True,
        )
    )
    fallback_turn = latest_turn_row(store)
    updater = RPStoryMemoryUpdater(fallback_memory_settings(), store)
    updated = asyncio.run(updater.update(None, force=True, fail_open=False))
    assert updated["story_memory"]["to_turn_id"] == fallback_turn["id"]
    assert store.effective_rp_story_memory() is not None

    restored = store.rollback(target_version=1, scene_state_enabled=True)
    assert restored["scene_state"]["as_of_party_turn"] == 14
    assert store.turns_for_memory(include_noncanonical_fallback=True) == []
    assert store.effective_rp_story_memory() is None
    plan, reason = RPStoryMemoryUpdater(fallback_memory_settings(), store).build_plan(force=True)
    assert plan is None
    assert reason == "up_to_date"

    request = party_chat_request(
        store,
        "mock-model",
        PartyMessageRequest(content="Что осталось после отката?"),
        fallback_memory_settings(),
    )
    rendered = "\n".join(str(message.content) for message in request.messages)
    assert player_text not in rendered
    assert "NON_CANONICAL_SAFE_FALLBACK" not in rendered

    checkpoint = store.create_memory_checkpoint("after fallback rollback")
    store.fork_from_checkpoint(
        checkpoint_id=checkpoint["id"],
        target_campaign_id="fallback-rollback-branch",
        target_state_path=str(tmp_path / "fallback-rollback-branch.json"),
    )
    branch = StateStore(
        str(tmp_path / "fallback-rollback.db"),
        "fallback-rollback-branch",
        str(tmp_path / "fallback-rollback-branch.json"),
    )
    assert branch.turns_for_memory(include_noncanonical_fallback=True) == []


def test_noncanonical_fallback_keeps_player_marker_but_never_fallback_prose_in_canon(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "fallback-canon-boundary")
    player_text = "Я остаюсь во дворе и жду помощи."

    async def transport_timeout(*args: object, **kwargs: object) -> dict[str, Any]:
        raise httpx.TimeoutException("transport exhausted before bundle")

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "complete", transport_timeout)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    response = asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content=player_text)],
            ),
            authorization=None,
            idempotency_key="fallback-canon-boundary",
            request_id="req-fallback-canon-boundary",
            allow_gateway_fallback=True,
        )
    )
    forbidden_fallback_text = response_text(response)
    turn = latest_turn_row(store)

    updater = RPStoryMemoryUpdater(fallback_memory_settings(), store)
    plan, reason = updater.build_plan(force=True)
    assert reason == "ready"
    assert plan is not None
    assert plan.to_turn_id == turn["id"]
    payload_text = json.dumps(updater.update_payload(plan), ensure_ascii=False)
    assert player_text in payload_text
    assert "NON_CANONICAL_SAFE_FALLBACK" in payload_text
    assert forbidden_fallback_text not in payload_text

    updated = asyncio.run(updater.update(None, force=True, fail_open=False))
    snapshot_text = json.dumps(updated["story_memory"]["memory"], ensure_ascii=False)
    assert updated["story_memory"]["to_turn_id"] == turn["id"]
    assert forbidden_fallback_text not in snapshot_text

    next_request = party_chat_request(
        store,
        "mock-model",
        PartyMessageRequest(content="Что происходит дальше?"),
        fallback_memory_settings(),
    )
    rendered = "\n".join(str(message.content) for message in next_request.messages)
    assert player_text in rendered
    assert "NON_CANONICAL_SAFE_FALLBACK" in rendered
    assert "stale" in rendered.lower()
    assert "as_of_party_turn=14" in rendered
    assert forbidden_fallback_text not in rendered

    protected_before = [
        (message.role, str(message.content))
        for message in next_request.messages
        if str(message.content).startswith("SCENE_STATE_BOUNDARY")
        or str(message.content) in {player_text, "NON_CANONICAL_SAFE_FALLBACK"}
    ]
    adjudicator.rebuild_revision_seven_request(
        next_request,
        store.effective_rp_story_memory(),
        "Что происходит дальше?",
    )
    protected_after = [
        (message.role, str(message.content))
        for message in next_request.messages
        if str(message.content).startswith("SCENE_STATE_BOUNDARY")
        or str(message.content) in {player_text, "NON_CANONICAL_SAFE_FALLBACK"}
    ]
    assert protected_after == protected_before

    assert store.search_archived_turns(
        forbidden_fallback_text,
        through_turn_id=int(turn["id"]),
        limit=5,
    ) == []
    assert forbidden_fallback_text not in json.dumps(store.memory_chapters(), ensure_ascii=False)

    extraction = RelationshipExtractionService(fallback_memory_settings(), store, relationship_model())
    extraction_calls = 0

    async def unexpected_extraction(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal extraction_calls
        extraction_calls += 1
        return {"choices": [{"message": {"role": "assistant", "content": '{"events": []}'}}]}

    monkeypatch.setattr(extraction, "_complete", unexpected_extraction)
    extraction_result = asyncio.run(extraction.process_turn(int(turn["id"]), None))
    assert extraction_calls == 0
    assert extraction_result["processed"] is False
    assert extraction_result["applied"] is False
    assert "noncanonical" in extraction_result["reason"]
    with store.connect() as connection:
        relationship_rows = sum(
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE campaign_id = ?",  # noqa: S608 - fixed test table
                    (store.campaign_id,),
                ).fetchone()[0]
            )
            for table in ("relationship_causes", "character_axis_state", "character_badges")
        )
    assert relationship_rows == 0

    async def accepted_bundle(*args: object, **kwargs: object) -> dict[str, Any]:
        return provider_response(scene_bundle())

    monkeypatch.setattr(adjudicator.narrative, "complete", accepted_bundle)
    asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Я осматриваюсь во дворе.")],
            ),
            authorization=None,
            idempotency_key="fallback-reanchored",
            request_id="req-fallback-reanchored",
        )
    )
    reanchored_state = store.get_state()
    assert reanchored_state["scene_state"]["stale"] is False
    assert reanchored_state["scene_state"]["as_of_party_turn"] == 16
    after_reanchor = party_chat_request(
        store,
        "mock-model",
        PartyMessageRequest(content="И что происходит теперь?"),
        fallback_memory_settings(),
    )
    reanchored_rendered = "\n".join(str(message.content) for message in after_reanchor.messages)
    assert player_text not in reanchored_rendered
    assert "NON_CANONICAL_SAFE_FALLBACK" not in reanchored_rendered


def test_fallback_authoritative_write_failure_rolls_back_noncanonical_turn_and_stale_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "fallback-atomic-failure")

    async def transport_timeout(*args: object, **kwargs: object) -> dict[str, Any]:
        raise httpx.TimeoutException("transport exhausted before bundle")

    monkeypatch.setattr(adjudicator.narrative, "complete", transport_timeout)
    before = authoritative_counts(store)
    before_state = store.get_state()
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_fallback_turn_insert
            BEFORE INSERT ON turns
            BEGIN
                SELECT RAISE(FAIL, 'forced fallback turn failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced fallback turn failure"):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Я жду.")],
                ),
                authorization=None,
                idempotency_key="fallback-atomic-failure",
                request_id="req-fallback-atomic-failure",
                allow_gateway_fallback=True,
            )
        )

    assert authoritative_counts(store) == before
    assert store.get_state() == before_state
    saved_request = store.get_turn_request("req-fallback-atomic-failure")
    assert saved_request is not None
    assert saved_request["status"] == "failed"


def test_opening_prebundle_transport_failure_commits_one_noncanonical_seed_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision_seven_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=7, post_turn_helpers_inline=False)
    party = create_demo_party(api)
    calls = 0

    async def transport_timeout(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        raise httpx.TimeoutException("opening transport exhausted before bundle")

    monkeypatch.setattr(NarrativeClient, "complete", transport_timeout)
    started = api.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "opening-safe-fallback"},
        headers={"X-Request-ID": "req-opening-safe-fallback"},
    )

    assert started.status_code == 200, started.text
    assert calls == 1
    assert started.json()["gateway_fallback"] == {"reason": "timeout"}
    store = api.app.state.party_store.store_for_party(str(party["id"]))
    assert len(store.turn_history()) == 1
    current = store.get_state()
    assert current["scene_state"]["location_id"] == "court"
    assert current["scene_state"]["present_character_ids"] == ["advisor"]
    assert current["scene_state"]["as_of_party_turn"] == 0
    assert current["scene_state"]["stale"] is True
    assert current["scene_state"]["stale_reason"] == "safe_fallback"
    turn = latest_turn_row(store)
    assert turn["metadata"]["story_memory_canonical"] is False
    assert turn["metadata"]["scene_state_stale"] is True

    retried = api.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "opening-safe-fallback"},
        headers={"X-Request-ID": "req-opening-safe-fallback"},
    )
    assert retried.status_code == 200
    assert calls == 1
    assert len(store.turn_history()) == 1


def test_opening_connect_error_uses_bounded_public_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision_seven_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=7, post_turn_helpers_inline=False)
    party = create_demo_party(api)
    private_error_text = "PRIVATE opening connection detail"

    async def transport_connect_error(*args: object, **kwargs: object) -> dict[str, Any]:
        raise httpx.ConnectError(
            private_error_text,
            request=httpx.Request("POST", "https://provider.invalid/chat/completions"),
        )

    monkeypatch.setattr(NarrativeClient, "complete", transport_connect_error)
    started = api.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "opening-connect-error"},
        headers={"X-Request-ID": "req-opening-connect-error"},
    )

    assert started.status_code == 200, started.text
    assert started.json()["gateway_fallback"] == {"reason": "http_error"}
    assert private_error_text not in started.text
    store = api.app.state.party_store.store_for_party(str(party["id"]))
    turn = latest_turn_row(store)
    assert turn["metadata"]["fallback_reason"] == "network_error"
    assert turn["metadata"]["story_memory_canonical"] is False


def test_revision_six_story_memory_keeps_existing_fallback_behavior(tmp_path: Path) -> None:
    store = StateStore(
        str(tmp_path / "legacy-fallback.db"),
        "legacy-fallback-party",
        str(tmp_path / "legacy-fallback-state.json"),
    )
    fallback_text = "Legacy revision-six fallback remains part of its existing ledger."
    store.record_turn(
        "legacy-fallback-turn",
        "legacy-fallback-request",
        "I wait.",
        fallback_text,
        {},
        1,
        party_turn=1,
        metadata={"fallback": True, "story_memory_canonical": False},
    )
    updater = RPStoryMemoryUpdater(
        Settings(
            scenario_type="rp",
            rp_contract_revision=6,
            service_model_choice="or-qwen-3.5-flash",
            openrouter_api_base="mock://success",
            service_openrouter_api_key="test-service-key",
            local_llm_enabled=False,
            rp_story_memory_update_turns=1,
        ),
        store,
    )
    plan, reason = updater.build_plan(force=True)

    assert reason == "ready"
    assert plan is not None
    payload_text = json.dumps(updater.update_payload(plan), ensure_ascii=False)
    assert fallback_text in payload_text
    assert "NON_CANONICAL_SAFE_FALLBACK" not in payload_text
    result = asyncio.run(updater.update(None, force=True, fail_open=False))
    assert result["story_memory"]["memory"]["current_situation"]["text"] == fallback_text
