from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import RP_CONTRACT_MAX_REVISION, Settings
from app.main import party_chat_request, settings_for_party
from app.models.schemas import (
    AutoTestCreate,
    ModelProfileSummary,
    PartyBranchCreate,
    PartyMessageRequest,
)
from app.services.adjudicator import Adjudicator
from app.services.context_budget import estimate_tokens as estimate_prompt_tokens
from app.services.context_estimator import estimate_party_context
from app.services.narrative import PromptBudgetExceeded, fit_messages_to_context, response_text
from app.services.prompt_tools import PromptInspector
from app.services.rp_story_memory import RPStoryMemoryUpdater, empty_story_memory
from app.services.state_store import StateStore, StateVersionConflict
from test_gateway import client, create_demo_party, write_worldpack


def set_worldpack_revision(pack_dir: Path, revision: int) -> None:
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": revision}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize(
    ("observed_revision", "declared_revision", "scenario_type", "expected_revision"),
    [
        (6, 7, "rp", 6),
        (7, 7, "rp", 7),
        (7, 6, "rp", 6),
        (7, 7, "training", 0),
        (8, 8, "rp", 8),
        (8, 7, "rp", 7),
        (8, 8, "training", 0),
    ],
)
def test_revision_stamp_matches_api_sqlite_and_party_runtime(
    tmp_path: Path,
    observed_revision: int,
    declared_revision: int,
    scenario_type: str,
    expected_revision: int,
) -> None:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp", "training"])
    set_worldpack_revision(pack_dir, declared_revision)
    api = client(tmp_path, rp_contract_observed_revision=observed_revision)

    party = create_demo_party(api, scenario_type=scenario_type)
    assert party["rp_contract_revision"] == expected_revision

    party_store = api.app.state.party_store
    stored_party = party_store.get_party(party["id"])
    runtime = settings_for_party(party_store.settings, stored_party)
    assert runtime.rp_contract_revision == expected_revision

    with party_store.connect() as connection:
        sqlite_revision = connection.execute(
            "SELECT rp_contract_revision FROM parties WHERE id = ?",
            (party["id"],),
        ).fetchone()[0]
        party_store.migrate_rp_contract_version(connection)
        migrated_revision = connection.execute(
            "SELECT rp_contract_revision FROM parties WHERE id = ?",
            (party["id"],),
        ).fetchone()[0]
    assert sqlite_revision == expected_revision
    assert migrated_revision == expected_revision


def test_revision_seven_is_available_only_to_an_explicit_candidate_branch(tmp_path: Path) -> None:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    set_worldpack_revision(pack_dir, 7)
    api = client(tmp_path, rp_contract_observed_revision=6)
    party = create_demo_party(api)
    assert party["rp_contract_revision"] == 6

    checkpoint = api.post(
        f"/api/parties/{party['id']}/checkpoints",
        json={"label": "revision 7 candidate"},
    ).json()["checkpoint"]
    branch = api.post(
        f"/api/parties/{party['id']}/branches",
        json={
            "checkpoint_id": checkpoint["id"],
            "label": "revision 7 candidate",
            "rp_contract_revision": 7,
        },
    )

    assert branch.status_code == 200, branch.text
    assert branch.json()["branch"]["rp_contract_revision"] == 7
    assert api.get(f"/api/parties/{party['id']}").json()["party"]["rp_contract_revision"] == 6


@pytest.mark.parametrize("persisted_revision", range(8))
def test_migration_keeps_persisted_revisions_zero_through_seven(
    tmp_path: Path,
    persisted_revision: int,
) -> None:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    set_worldpack_revision(pack_dir, 7)
    api = client(tmp_path, rp_contract_observed_revision=6)
    party = create_demo_party(api)
    party_store = api.app.state.party_store

    with party_store.connect() as connection:
        connection.execute(
            "UPDATE parties SET rp_contract_revision = ? WHERE id = ?",
            (persisted_revision, party["id"]),
        )
        party_store.migrate_rp_contract_version(connection)
        migrated = connection.execute(
            "SELECT rp_contract_revision FROM parties WHERE id = ?",
            (party["id"],),
        ).fetchone()[0]

    assert migrated == persisted_revision


def test_revision_schema_bounds_accept_ten_and_reject_eleven() -> None:
    assert RP_CONTRACT_MAX_REVISION == 10
    assert PartyBranchCreate(
        checkpoint_id=1,
        label="candidate",
        rp_contract_revision=10,
    ).rp_contract_revision == 10
    assert AutoTestCreate(
        source_party_id="party-source",
        player_prompt="continue",
        turn_count=1,
        player_model_profile_id="model",
        rp_contract_revision=10,
    ).rp_contract_revision == 10

    with pytest.raises(ValidationError):
        PartyBranchCreate(checkpoint_id=1, label="unsupported", rp_contract_revision=11)
    with pytest.raises(ValidationError):
        AutoTestCreate(
            source_party_id="party-source",
            player_prompt="continue",
            turn_count=1,
            player_model_profile_id="model",
            rp_contract_revision=11,
        )


def test_only_merchant_declares_revision_ten_candidate() -> None:
    worldpacks = Path(__file__).resolve().parents[2] / "worldpacks"
    revision_ten_packs: set[str] = set()
    for manifest_path in worldpacks.glob("*/manifest.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "rp" not in manifest.get("scenario_types", {}).get("supported", []):
            continue
        contract = manifest.get("rp_contract") or {}
        if contract.get("revision") == 10:
            revision_ten_packs.add(manifest["id"])

    assert revision_ten_packs == {"merchant-sviatoslav"}
    starosta = json.loads((worldpacks / "starosta" / "manifest.json").read_text(encoding="utf-8"))
    assert starosta["rp_contract"] == {"schema_version": "rp-core.v2", "revision": 7}


def make_story_store(tmp_path: Path) -> StateStore:
    return StateStore(
        str(tmp_path / "story.db"),
        "revision-seven-story",
        str(tmp_path / "story-state.json"),
    )


def seed_known_scene_location(store: StateStore) -> None:
    state = store.get_state()
    state["player"]["location"] = "test-location"
    state["locations"] = {"test-location": {"name": "Test location"}}
    with store.connect() as connection:
        connection.execute(
            """
            UPDATE state_versions
            SET state_json = ?
            WHERE campaign_id = ? AND version = ?
            """,
            (
                json.dumps(state, ensure_ascii=False),
                store.campaign_id,
                int(state["meta"]["state_version"]),
            ),
        )
    store.write_state_file(state)


def record_story_turns(store: StateStore, count: int) -> None:
    for party_turn in range(1, count + 1):
        store.record_turn(
            f"turn-{party_turn}",
            f"request-{party_turn}",
            f"Player action {party_turn}",
            f"Narrator consequence {party_turn}",
            {},
            party_turn,
        )


def revision_seven_overflow_adjudicator(
    tmp_path: Path,
    *,
    campaign_id: str,
    world_system_prompt: str = "",
    story_batch_tokens: int = 20_000,
    context_tokens: int = 4_000,
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
        party_context_max_tokens=context_tokens,
        party_context_limit_tokens=context_tokens,
        party_context_min_history_tokens=1,
        party_context_completion_reserve_tokens=0,
        rp_story_memory_batch_tokens=story_batch_tokens,
        party_memory_retrieval_enabled=False,
        world_system_prompt=world_system_prompt,
    )
    store = StateStore(
        str(tmp_path / f"{campaign_id}.db"),
        campaign_id,
        str(tmp_path / f"{campaign_id}-state.json"),
    )
    seed_known_scene_location(store)
    for party_turn in range(1, 4):
        store.record_turn(
            f"existing-turn-{party_turn}",
            f"existing-request-{party_turn}",
            f"Player action {party_turn} " + "x " * 1_200,
            f"Narrator consequence {party_turn} " + "y " * 1_200,
            {},
            1,
            party_turn=party_turn,
        )
    store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=empty_story_memory(),
        model="service-model",
    )
    return settings, store, Adjudicator(settings, store)


def campaign_table_count(store: StateStore, table: str) -> int:
    with store.connect() as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE campaign_id = ?",  # noqa: S608 - fixed test tables
                (store.campaign_id,),
            ).fetchone()[0]
        )


def test_revision_seven_eviction_is_whole_block_and_only_for_hard_budget() -> None:
    messages = [
        {"role": "system", "content": "WORLD_SYSTEM_PROMPT\nmandatory"},
        {"role": "system", "content": "RELEVANT_CHARACTERS\n" + "optional " * 800},
        {"role": "user", "content": "Prior player action"},
        {"role": "assistant", "content": "Prior narrator consequence"},
        {"role": "user", "content": "Current player action"},
    ]
    required_messages = [messages[0], *messages[2:]]
    hard_budget = estimate_prompt_tokens(
        "\n".join(message["content"] for message in required_messages)
    )

    fitted = fit_messages_to_context(
        messages,
        hard_budget,
        protect_history=True,
        fail_on_token_overflow=True,
    )

    assert fitted == required_messages

    percentage_only = fit_messages_to_context(
        messages,
        estimate_prompt_tokens("\n".join(message["content"] for message in messages)),
        max_prompt_chars=10,
        protect_history=True,
        fail_on_token_overflow=True,
    )
    assert percentage_only == messages


def test_revision_seven_raw_tail_follows_effective_story_memory_coverage(tmp_path: Path) -> None:
    store = make_story_store(tmp_path)
    record_story_turns(store, 3)
    snapshot = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=empty_story_memory(),
        model="service-model",
    )
    store.record_memory_chapter(
        from_turn_id=1,
        to_turn_id=3,
        state_version=1,
        summary_text="episodic coverage must not hide the RP continuity tail",
        key_facts=[],
        open_threads=[],
        relationship_changes=[],
        player_promises=[],
        npc_obligations=[],
        model="service-model",
    )

    request = party_chat_request(
        store,
        "mock-model",
        PartyMessageRequest(content="Current player action"),
        Settings(
            scenario_type="rp",
            rp_contract_revision=7,
            party_context_max_tokens=1,
            party_context_limit_tokens=1,
            party_context_min_history_tokens=1,
            party_context_completion_reserve_tokens=0,
            party_context_system_reserve_tokens=0,
            rp_story_memory_reserve_tokens=0,
        ),
    )

    assert [(message.role, message.content) for message in request.messages if message.role != "system"] == [
        ("user", "Player action 2"),
        ("assistant", "Narrator consequence 2"),
        ("user", "Player action 3"),
        ("assistant", "Narrator consequence 3"),
        ("user", "Current player action"),
    ]
    retrieval = next(
        message.content
        for message in request.messages
        if message.role == "system"
        and message.content.startswith("RETRIEVED_ARCHIVE_SCENES")
    )
    assert "Player action 1" in retrieval
    assert "Player action 2" not in retrieval
    assert request._latest_player_action == "Current player action"
    assert request._rp_story_memory_snapshot_id == snapshot["id"]
    assert request._rp_story_memory_covered_through_turn_id == 1


def test_story_memory_catch_up_drains_multiple_batches_and_reports_threshold(tmp_path: Path) -> None:
    store = make_story_store(tmp_path)
    record_story_turns(store, 3)
    updater = RPStoryMemoryUpdater(
        Settings(
            scenario_type="rp",
            rp_contract_revision=7,
            service_model_choice="or-qwen-3.5-flash",
            openrouter_api_base="mock://success",
            service_openrouter_api_key="test-service-key",
            local_llm_enabled=False,
            rp_story_memory_update_turns=2,
            rp_story_memory_batch_tokens=1,
        ),
        store,
    )

    before = updater.stats()
    result = asyncio.run(updater.catch_up(None, request_id="req-catch-up"))

    assert before["pending_turn_threshold"] == 2
    assert before["pending_turn_threshold_exceeded"] is True
    assert before["operator_status"] == "lagging"
    assert result["generated"] is True
    assert result["terminal_result"] == "up_to_date"
    assert result["batches"] == 3
    assert result["force_refresh_batches"] == 3
    assert result["coverage_before"] == 0
    assert result["coverage_after"] == 3
    assert result["stats"]["pending_turns"] == 0
    assert result["stats"]["pending_turn_threshold_exceeded"] is False
    assert result["stats"]["operator_status"] == "normal"


def test_story_memory_catch_up_stops_at_the_safety_ceiling(tmp_path: Path) -> None:
    store = make_story_store(tmp_path)
    record_story_turns(store, 3)
    updater = RPStoryMemoryUpdater(
        Settings(
            scenario_type="rp",
            rp_contract_revision=7,
            service_model_choice="or-qwen-3.5-flash",
            openrouter_api_base="mock://success",
            service_openrouter_api_key="test-service-key",
            local_llm_enabled=False,
            rp_story_memory_batch_tokens=1,
        ),
        store,
    )

    result = asyncio.run(updater.catch_up(None, max_batches=2))

    assert result["terminal_result"] == "max_batches_reached"
    assert result["batches"] == 2
    assert result["coverage_before"] == 0
    assert result["coverage_after"] == 2
    assert result["stats"]["pending_turns"] == 1

    with pytest.raises(ValueError, match="max_batches"):
        asyncio.run(updater.catch_up(None, max_batches=0))


def test_adjudicator_refreshes_lagging_story_memory_once_before_narration_and_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, store, adjudicator = revision_seven_overflow_adjudicator(
        tmp_path,
        campaign_id="overflow-refresh-success",
    )
    assert adjudicator.rp_story_memory is not None
    initial_story = store.effective_rp_story_memory()
    assert initial_story is not None
    assert initial_story["to_turn_id"] == 1

    catch_up_results: list[dict] = []
    provider_requests: list[tuple[int | None, list[tuple[str, str]]]] = []
    original_catch_up = adjudicator.rp_story_memory.catch_up
    original_complete = adjudicator.narrative.complete

    async def tracked_catch_up(*args: object, **kwargs: object) -> dict:
        result = await original_catch_up(*args, **kwargs)
        catch_up_results.append(result)
        return result

    async def tracked_complete(request: object, *args: object, **kwargs: object) -> dict:
        provider_requests.append(
            (
                request._rp_story_memory_covered_through_turn_id,
                [(message.role, str(message.content)) for message in request.messages],
            )
        )
        return await original_complete(request, *args, **kwargs)

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.rp_story_memory, "catch_up", tracked_catch_up)
    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    request = party_chat_request(
        store,
        "mock-narrator",
        PartyMessageRequest(content="Current player action"),
        settings,
    )
    turns_before = campaign_table_count(store, "turns")
    versions_before = campaign_table_count(store, "state_versions")

    result = asyncio.run(
        adjudicator.handle_chat(
            request,
            authorization=None,
            idempotency_key="overflow-refresh-success",
            request_id="req-overflow-refresh-success",
        )
    )

    assert len(catch_up_results) == 1
    assert catch_up_results[0]["batches"] == 1
    assert catch_up_results[0]["coverage_before"] == 1
    assert catch_up_results[0]["coverage_after"] == 3
    assert len(provider_requests) == 1
    assert provider_requests[0][0] == 3
    assert [message for message in provider_requests[0][1] if message[0] != "system"] == [
        ("user", "Current player action")
    ]
    assert any(
        role == "system" and content.startswith("SCENE_STATE_BOUNDARY")
        for role, content in provider_requests[0][1]
    )
    assert request._rp_story_memory_snapshot_id == store.effective_rp_story_memory()["id"]
    assert response_text(result)
    assert campaign_table_count(store, "turns") == turns_before + 1
    assert campaign_table_count(store, "state_versions") == versions_before + 1
    assert store.get_state()["meta"]["turn"] == 1
    saved_request = store.get_turn_request("req-overflow-refresh-success")
    assert saved_request is not None
    assert saved_request["status"] == "completed"


def test_adjudicator_rebuilds_after_each_forced_batch_and_stops_when_prompt_fits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, store, adjudicator = revision_seven_overflow_adjudicator(
        tmp_path,
        campaign_id="overflow-refresh-incremental",
        story_batch_tokens=1,
        context_tokens=4_500,
    )
    assert adjudicator.rp_story_memory is not None
    catch_up_results: list[dict] = []
    provider_requests: list[tuple[int | None, list[tuple[str, str]]]] = []
    original_catch_up = adjudicator.rp_story_memory.catch_up
    original_complete = adjudicator.narrative.complete

    async def tracked_catch_up(*args: object, **kwargs: object) -> dict:
        result = await original_catch_up(*args, **kwargs)
        catch_up_results.append(result)
        return result

    async def tracked_complete(request: object, *args: object, **kwargs: object) -> dict:
        provider_requests.append(
            (
                request._rp_story_memory_covered_through_turn_id,
                [(message.role, str(message.content)) for message in request.messages],
            )
        )
        return await original_complete(request, *args, **kwargs)

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.rp_story_memory, "catch_up", tracked_catch_up)
    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)

    asyncio.run(
        adjudicator.handle_chat(
            party_chat_request(
                store,
                "mock-narrator",
                PartyMessageRequest(content="Current player action"),
                settings,
            ),
            authorization=None,
            idempotency_key="overflow-refresh-incremental",
            request_id="req-overflow-refresh-incremental",
        )
    )

    assert len(catch_up_results) == 1
    assert catch_up_results[0]["terminal_result"] == "stop_condition_met"
    assert catch_up_results[0]["batches"] == 1
    assert catch_up_results[0]["coverage_before"] == 1
    assert catch_up_results[0]["coverage_after"] == 2
    assert len(provider_requests) == 1
    assert provider_requests[0][0] == 2
    assert [message for message in provider_requests[0][1] if message[0] != "system"] == [
        ("user", "Player action 3 " + "x " * 1_200),
        ("assistant", "Narrator consequence 3 " + "y " * 1_200),
        ("user", "Current player action"),
    ]
    assert any(
        role == "system" and content.startswith("SCENE_STATE_BOUNDARY")
        for role, content in provider_requests[0][1]
    )


def test_adjudicator_rejects_second_overflow_without_narration_or_projection_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, store, adjudicator = revision_seven_overflow_adjudicator(
        tmp_path,
        campaign_id="overflow-refresh-rejected",
        world_system_prompt="mandatory world rule " * 3_000,
    )
    assert adjudicator.rp_story_memory is not None
    catch_up_results: list[dict] = []
    provider_calls = 0
    original_catch_up = adjudicator.rp_story_memory.catch_up
    original_complete = adjudicator.narrative.complete

    async def tracked_catch_up(*args: object, **kwargs: object) -> dict:
        result = await original_catch_up(*args, **kwargs)
        catch_up_results.append(result)
        return result

    async def tracked_complete(*args: object, **kwargs: object) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        return await original_complete(*args, **kwargs)

    monkeypatch.setattr(adjudicator.rp_story_memory, "catch_up", tracked_catch_up)
    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)
    request = party_chat_request(
        store,
        "mock-narrator",
        PartyMessageRequest(content="Current player action"),
        settings,
    )
    turns_before = store.turns_for_memory()
    state_before = store.get_state()
    versions_before = campaign_table_count(store, "state_versions")
    relationship_causes_before = campaign_table_count(store, "relationship_causes")
    relationship_projections_before = store.trace_projection_snapshot()

    with pytest.raises(PromptBudgetExceeded):
        asyncio.run(
            adjudicator.handle_chat(
                request,
                authorization=None,
                idempotency_key="overflow-refresh-rejected",
                request_id="req-overflow-refresh-rejected",
            )
        )

    assert len(catch_up_results) == 1
    assert catch_up_results[0]["coverage_before"] == 1
    assert catch_up_results[0]["coverage_after"] == 3
    assert provider_calls == 0
    assert store.turns_for_memory() == turns_before
    assert store.get_state() == state_before
    assert campaign_table_count(store, "state_versions") == versions_before
    assert campaign_table_count(store, "relationship_causes") == relationship_causes_before
    assert store.trace_projection_snapshot() == relationship_projections_before
    saved_request = store.get_turn_request("req-overflow-refresh-rejected")
    assert saved_request is not None
    assert saved_request["status"] == "failed"
    assert "PromptBudgetExceeded" in saved_request["error"]
    context = estimate_party_context(store, settings, None)
    assert context["rp_story_memory_hard_overflow"] is True
    assert context["rp_story_memory_operator_status"] == "overflow"
    assert context["rp_story_memory_force_refresh_attempted"] is True
    assert context["rp_story_memory_force_refresh_batches"] == 1
    assert context["rp_story_memory_force_refresh_terminal_result"] == "up_to_date"
    assert context["rp_story_memory_force_refresh_coverage_before"] == 1
    assert context["rp_story_memory_force_refresh_coverage_after"] == 3
    assert "mandatory world rule" not in json.dumps(context, ensure_ascii=False)


def test_prompt_preview_returns_sanitized_structured_hard_overflow(tmp_path: Path) -> None:
    secret_world_text = "SECRET-WORLD-CONTEXT"
    secret_player_text = "SECRET-PLAYER-ACTION"
    settings = Settings(
        campaign_id="overflow-preview",
        scenario_type="rp",
        rp_contract_revision=7,
        party_context_max_tokens=1_000,
        party_context_limit_tokens=1_000,
        party_context_min_history_tokens=1,
        party_context_completion_reserve_tokens=0,
        world_system_prompt=(secret_world_text + " ") * 2_000,
        party_memory_retrieval_enabled=False,
    )
    store = StateStore(
        str(tmp_path / "overflow-preview.db"),
        "overflow-preview",
        str(tmp_path / "overflow-preview-state.json"),
    )

    preview = PromptInspector(settings, store).preview_current(secret_player_text)
    encoded = json.dumps(preview, ensure_ascii=False)

    assert preview["error"]["type"] == "PromptBudgetExceeded"
    assert preview["hard_budget_status"] == "over_budget"
    assert preview["hard_overflow"] is True
    assert preview["estimated_prompt_tokens"] > preview["hard_input_budget_tokens"]
    assert preview["messages"] == []
    assert preview["blocks"] == []
    assert preview["inspection"]["story_memory"]["operator_status"] == "overflow"
    assert secret_world_text not in encoded
    assert secret_player_text not in encoded


def test_party_message_hard_overflow_returns_sanitized_502_without_mutation(
    tmp_path: Path,
) -> None:
    secret_world_text = "SECRET-HTTP-WORLD-CONTEXT"
    secret_player_text = "SECRET-HTTP-PLAYER-ACTION"
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    set_worldpack_revision(pack_dir, 7)
    (pack_dir / "prompts" / "gm-system.md").write_text(
        (secret_world_text + " ") * 2_000,
        encoding="utf-8",
    )
    api = client(
        tmp_path,
        rp_contract_observed_revision=7,
        party_context_max_tokens=1_000,
        party_context_limit_tokens=1_000,
        party_context_min_history_tokens=1,
        party_context_completion_reserve_tokens=0,
        party_memory_retrieval_enabled=False,
    )
    party = create_demo_party(api)
    party_store = api.app.state.party_store
    store = party_store.store_for_party(party["id"])
    turns_before = campaign_table_count(store, "turns")
    versions_before = campaign_table_count(store, "state_versions")
    state_before = store.get_state()

    response = api.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": secret_player_text,
            "idempotency_key": "http-overflow-no-mutation",
        },
        headers={"X-Request-ID": "req-http-overflow-no-mutation"},
    )
    encoded = json.dumps(response.json(), ensure_ascii=False)

    assert response.status_code == 502
    assert response.json()["detail"] == "Required RP continuity context exceeds the provider input budget"
    assert secret_world_text not in encoded
    assert secret_player_text not in encoded
    assert campaign_table_count(store, "turns") == turns_before
    assert campaign_table_count(store, "state_versions") == versions_before
    assert store.get_state() == state_before


def test_prompt_payload_keeps_legacy_block_estimates_with_separate_hard_status(
    tmp_path: Path,
) -> None:
    settings = Settings(campaign_id="prompt-payload", scenario_type="rp", rp_contract_revision=7)
    store = StateStore(
        str(tmp_path / "prompt-payload.db"),
        "prompt-payload",
        str(tmp_path / "prompt-payload-state.json"),
    )
    inspector = PromptInspector(settings, store)
    messages = [
        {"role": "system", "content": "WORLD_SYSTEM_PROMPT\nmandatory"},
        {"role": "user", "content": "continue"},
    ]
    blocks = inspector.blocks(messages)

    payload = inspector.payload(
        "continue",
        messages,
        blocks,
        source="contract-test",
        dry_run=True,
        token_budget=10_000,
    )

    assert payload["estimated_prompt_tokens"] == sum(
        block["estimated_tokens"] for block in blocks
    )
    assert payload["estimated_prompt_chars"] == sum(len(block["content"]) for block in blocks)
    assert payload["hard_budget_status"] == "within_budget"
    assert payload["hard_overflow"] is False


def test_empty_context_hard_budget_uses_larger_model_completion_reserve(tmp_path: Path) -> None:
    settings = Settings(
        campaign_id="empty-context-budget",
        scenario_type="rp",
        rp_contract_revision=7,
        party_context_max_tokens=2_000,
        party_context_limit_tokens=2_000,
        party_context_min_history_tokens=1,
        party_context_completion_reserve_tokens=200,
    )
    store = StateStore(
        str(tmp_path / "empty-context-budget.db"),
        "empty-context-budget",
        str(tmp_path / "empty-context-budget-state.json"),
    )
    profile = ModelProfileSummary(
        id="model",
        title="Model",
        provider="mock",
        base_url="mock://success",
        model="mock-model",
        params={"max_tokens": 750},
        api_key_source="none",
    )

    context = estimate_party_context(store, settings, profile)

    assert context["completion_reserved_tokens"] == 750
    assert context["hard_input_budget_tokens"] == 1_250
    assert context["hard_budget_status"] == "unknown"


def test_adjudicator_rebuilds_when_story_snapshot_advances_before_narrator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, store, adjudicator = revision_seven_overflow_adjudicator(
        tmp_path,
        campaign_id="snapshot-advance-before-narrator",
        context_tokens=100_000,
    )
    assert adjudicator.rp_story_memory is not None
    initial_snapshot = store.effective_rp_story_memory()
    assert initial_snapshot is not None
    original_messages = adjudicator.narrative.narrative_messages
    original_complete = adjudicator.narrative.complete
    assembly_calls = 0
    provider_requests: list[tuple[int | None, list[tuple[str, str]]]] = []

    def advance_after_first_assembly(*args: object, **kwargs: object) -> list[dict[str, str]]:
        nonlocal assembly_calls
        messages = original_messages(*args, **kwargs)
        assembly_calls += 1
        if assembly_calls == 1:
            advanced = store.record_rp_story_memory(
                from_turn_id=1,
                to_turn_id=2,
                state_version=1,
                memory=empty_story_memory(),
                model="service-model",
                contributing_turn_ids=[2],
                base_snapshot_id=int(initial_snapshot["id"]),
            )
            assert advanced is not None
        return messages

    async def tracked_complete(request: object, *args: object, **kwargs: object) -> dict:
        provider_requests.append(
            (
                request._rp_story_memory_covered_through_turn_id,
                [(message.role, str(message.content)) for message in request.messages],
            )
        )
        return await original_complete(request, *args, **kwargs)

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "narrative_messages", advance_after_first_assembly)
    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    request = party_chat_request(
        store,
        "mock-narrator",
        PartyMessageRequest(content="Current player action"),
        settings,
    )

    asyncio.run(
        adjudicator.handle_chat(
            request,
            authorization=None,
            idempotency_key="snapshot-advance-before-narrator",
            request_id="req-snapshot-advance-before-narrator",
        )
    )

    assert assembly_calls >= 3
    assert len(provider_requests) == 1
    assert provider_requests[0][0] == 2
    assert [message for message in provider_requests[0][1] if message[0] != "system"] == [
        ("user", "Player action 3 " + "x " * 1_200),
        ("assistant", "Narrator consequence 3 " + "y " * 1_200),
        ("user", "Current player action"),
    ]
    assert any(
        role == "system" and content.startswith("SCENE_STATE_BOUNDARY")
        for role, content in provider_requests[0][1]
    )
    assert request._rp_story_memory_snapshot_id == store.effective_rp_story_memory()["id"]


def test_adjudicator_fails_before_provider_when_state_changes_during_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = "snapshot-rollback-before-narrator"
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
        party_context_max_tokens=5_000,
        party_context_limit_tokens=5_000,
        party_context_min_history_tokens=1,
        party_context_completion_reserve_tokens=0,
        party_memory_retrieval_enabled=False,
    )
    store = StateStore(
        str(tmp_path / f"{campaign_id}.db"),
        campaign_id,
        str(tmp_path / f"{campaign_id}-state.json"),
    )
    seed_known_scene_location(store)
    store.record_turn(
        "kept-turn",
        "kept-request",
        "Kept player action " + "x " * 7_000,
        "Kept narrator consequence " + "y " * 7_000,
        {},
        1,
        party_turn=1,
    )
    store.record_turn(
        "rolled-back-turn",
        "rolled-back-request",
        "Rolled-back action",
        "Rolled-back consequence",
        {},
        2,
        party_turn=2,
    )
    snapshot = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=2,
        state_version=1,
        memory=empty_story_memory(),
        model="service-model",
    )
    assert snapshot is not None
    adjudicator = Adjudicator(settings, store)
    assert adjudicator.rp_story_memory is not None
    original_messages = adjudicator.narrative.narrative_messages
    original_catch_up = adjudicator.rp_story_memory.catch_up
    original_complete = adjudicator.narrative.complete
    assembly_calls = 0
    catch_up_results: list[dict] = []
    provider_requests: list[tuple[int | None, list[tuple[str, str]]]] = []

    def rollback_after_first_assembly(*args: object, **kwargs: object) -> list[dict[str, str]]:
        nonlocal assembly_calls
        messages = original_messages(*args, **kwargs)
        assembly_calls += 1
        if assembly_calls == 1:
            store.rollback(1)
        return messages

    async def tracked_catch_up(*args: object, **kwargs: object) -> dict:
        result = await original_catch_up(*args, **kwargs)
        catch_up_results.append(result)
        return result

    async def tracked_complete(request: object, *args: object, **kwargs: object) -> dict:
        provider_requests.append(
            (
                request._rp_story_memory_covered_through_turn_id,
                [(message.role, str(message.content)) for message in request.messages],
            )
        )
        return await original_complete(request, *args, **kwargs)

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "narrative_messages", rollback_after_first_assembly)
    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)
    monkeypatch.setattr(adjudicator.rp_story_memory, "catch_up", tracked_catch_up)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    request = party_chat_request(
        store,
        "mock-narrator",
        PartyMessageRequest(content="Current player action"),
        settings,
    )
    turns_before = campaign_table_count(store, "turns")
    versions_before = campaign_table_count(store, "state_versions")

    with pytest.raises(StateVersionConflict):
        asyncio.run(
            adjudicator.handle_chat(
                request,
                authorization=None,
                idempotency_key="snapshot-rollback-before-narrator",
                request_id="req-snapshot-rollback-before-narrator",
            )
        )

    assert len(catch_up_results) == 1
    assert catch_up_results[0]["coverage_before"] == 0
    assert catch_up_results[0]["coverage_after"] == 1
    assert assembly_calls >= 2
    assert provider_requests == []
    assert campaign_table_count(store, "turns") == turns_before
    assert campaign_table_count(store, "state_versions") == versions_before + 1
    saved_request = store.get_turn_request("req-snapshot-rollback-before-narrator")
    assert saved_request is not None
    assert saved_request["status"] == "failed"
    assert request._rp_story_memory_snapshot_id == store.effective_rp_story_memory()["id"]
    assert request._rp_story_memory_covered_through_turn_id == 1
