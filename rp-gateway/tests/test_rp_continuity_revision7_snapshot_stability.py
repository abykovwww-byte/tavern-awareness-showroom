from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage
from app.services.adjudicator import Adjudicator
from app.services.rp_story_memory import empty_story_memory
from app.services.state_store import StateStore


def snapshot_adjudicator(
    tmp_path: Path,
    *,
    campaign_id: str,
    turn_count: int,
) -> tuple[Settings, StateStore, Adjudicator]:
    settings = Settings(
        app_env="test",
        campaign_id=campaign_id,
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=7,
        llm_api_base="mock://success",
        llm_api_key="test-key",
        service_model_choice="or-qwen-3.5-flash",
        openrouter_api_base="mock://success",
        service_openrouter_api_key="test-service-key",
        local_llm_enabled=False,
        post_turn_helpers_inline=False,
        party_context_max_tokens=100_000,
        party_context_limit_tokens=100_000,
        party_context_min_history_tokens=1,
        party_context_completion_reserve_tokens=0,
        party_memory_retrieval_enabled=False,
    )
    store = StateStore(
        str(tmp_path / f"{campaign_id}.db"),
        campaign_id,
        str(tmp_path / f"{campaign_id}-state.json"),
    )
    known_scene_state = store.get_state()
    known_scene_state["meta"]["state_version"] = 2
    known_scene_state["player"]["location"] = "test-location"
    known_scene_state["locations"] = {"test-location": {"name": "Test location"}}
    store.insert_state_version(known_scene_state, reason="test:known-scene-location")
    for party_turn in range(1, turn_count + 1):
        store.record_turn(
            f"existing-turn-{party_turn}",
            f"existing-request-{party_turn}",
            f"Player action {party_turn}",
            f"Narrator consequence {party_turn}",
            {},
            1,
            party_turn=party_turn,
        )
    snapshot = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=empty_story_memory(),
        model="service-model",
    )
    assert snapshot is not None
    return settings, store, Adjudicator(settings, store)


def advance_story_snapshot(store: StateStore) -> None:
    current = store.effective_rp_story_memory()
    assert current is not None
    next_turn_id = int(current["to_turn_id"]) + 1
    advanced = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=next_turn_id,
        state_version=1,
        memory=empty_story_memory(),
        model="service-model",
        contributing_turn_ids=[next_turn_id],
        base_snapshot_id=int(current["id"]),
    )
    assert advanced is not None


def test_revision_seven_rechecks_snapshot_after_second_reassembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, store, adjudicator = snapshot_adjudicator(
        tmp_path,
        campaign_id="snapshot-second-reassembly",
        turn_count=3,
    )
    original_messages = adjudicator.narrative.narrative_messages
    original_complete = adjudicator.narrative.complete
    assembly_calls = 0
    provider_coverages: list[int | None] = []

    def advance_after_first_two_assemblies(*args: object, **kwargs: object) -> list[dict[str, str]]:
        nonlocal assembly_calls
        messages = original_messages(*args, **kwargs)
        assembly_calls += 1
        if assembly_calls <= 2:
            advance_story_snapshot(store)
        return messages

    async def tracked_complete(request: ChatCompletionRequest, *args: object, **kwargs: object) -> dict:
        provider_coverages.append(request._rp_story_memory_covered_through_turn_id)
        return await original_complete(request, *args, **kwargs)

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        adjudicator.narrative,
        "narrative_messages",
        advance_after_first_two_assemblies,
    )
    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    request = ChatCompletionRequest(
        model="mock-narrator",
        messages=[ChatMessage(role="user", content="Current player action")],
    )

    asyncio.run(
        adjudicator.handle_chat(
            request,
            authorization=None,
            idempotency_key="snapshot-second-reassembly",
            request_id="req-snapshot-second-reassembly",
        )
    )

    assert provider_coverages == [3]
    assert request._rp_story_memory_covered_through_turn_id == 3
    assert request._rp_story_memory_snapshot_id == store.effective_rp_story_memory()["id"]


def test_revision_seven_fails_closed_when_story_snapshot_never_stabilizes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, store, adjudicator = snapshot_adjudicator(
        tmp_path,
        campaign_id="snapshot-continuous-churn",
        turn_count=5,
    )
    assert adjudicator.rp_story_memory is not None
    original_messages = adjudicator.narrative.narrative_messages
    provider_calls = 0
    catch_up_calls = 0

    def advance_after_every_assembly(*args: object, **kwargs: object) -> list[dict[str, str]]:
        messages = original_messages(*args, **kwargs)
        advance_story_snapshot(store)
        return messages

    async def tracked_complete(*args: object, **kwargs: object) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("an unstable story snapshot must not reach the narrator provider")

    async def tracked_catch_up(*args: object, **kwargs: object) -> dict:
        nonlocal catch_up_calls
        catch_up_calls += 1
        raise AssertionError("snapshot churn without overflow must not force story-memory refresh")

    monkeypatch.setattr(
        adjudicator.narrative,
        "narrative_messages",
        advance_after_every_assembly,
    )
    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)
    monkeypatch.setattr(adjudicator.rp_story_memory, "catch_up", tracked_catch_up)
    turns_before = store.turns_for_memory()

    with pytest.raises(RuntimeError, match="snapshot did not stabilize"):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Current player action")],
                ),
                authorization=None,
                idempotency_key="snapshot-continuous-churn",
                request_id="req-snapshot-continuous-churn",
            )
        )

    assert provider_calls == 0
    assert catch_up_calls == 0
    assert store.turns_for_memory() == turns_before
