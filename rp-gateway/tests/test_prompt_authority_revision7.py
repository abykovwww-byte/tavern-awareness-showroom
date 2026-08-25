from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.main import party_chat_request
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome, PartyMessageRequest
from app.services.adjudicator import Adjudicator
from app.services.context_budget import estimate_tokens
from app.services.context_estimator import estimate_party_context
from app.services.narrative import (
    PROMPT_SYSTEM_BLOCK_IDS,
    NarrativeClient,
    archived_memory_retrieval_block,
    fit_messages_to_context,
    long_term_memory_block,
    party_lore_cards_block,
    prompt_authority_block,
    prompt_block_id,
    prompt_block_ids,
    relevant_characters_block,
    rp_story_memory_block,
    scene_reanchor_prompt_block,
    scene_state_prompt_block,
    training_artifact_prompt_block,
    training_turn_prompt_block,
    uncompacted_archive_fallback_block,
    world_absolute_rules_block,
)
from app.services.prompt_tools import PromptInspector
from app.services.relationships import RelationshipMechanics
from app.services.rp_gm import RPGMService
from app.services.rp_story_memory import empty_story_memory
from app.services.scene_state import initial_scene_state
from app.services.state_store import StateStore
from app.services.world_clock import world_clock_prompt_projection
from test_gateway import base_state


PROMPT_ASSEMBLY_SCHEMA = "rp-gateway.prompt-assembly.v1"
AUTHORITY_ORDER = [
    "authoritative_outcome_current_action",
    "uncovered_raw_tail",
    "rp_story_memory",
    "archive",
]
PROMPT_ASSEMBLY_KEYS = {
    "schema_version",
    "rp_contract_revision",
    "authority_order",
    "story_memory_covered_through_turn_id",
    "included_block_ids",
    "raw_tail_turn_ids",
    "omitted_blocks",
}
def expected_prompt_block_ids(messages: list[dict[str, Any]]) -> list[str]:
    return prompt_block_ids(messages)


def assert_prompt_assembly_schema(value: dict[str, Any]) -> None:
    assert set(value) == PROMPT_ASSEMBLY_KEYS
    assert value["schema_version"] == PROMPT_ASSEMBLY_SCHEMA
    assert value["rp_contract_revision"] == 7
    assert value["authority_order"] == AUTHORITY_ORDER
    assert isinstance(value["story_memory_covered_through_turn_id"], int)
    assert isinstance(value["included_block_ids"], list)
    assert isinstance(value["raw_tail_turn_ids"], list)
    assert isinstance(value["omitted_blocks"], list)


def make_store(tmp_path: Path, campaign_id: str, *, turn: int) -> StateStore:
    state = base_state()
    state["meta"]["campaign_id"] = campaign_id
    state["meta"]["turn"] = turn
    state["player"]["known_world_facts"] = ["PRIVATE_STATE_VALUE"]
    state["characters"]["advisor"]["name"] = "PRIVATE_CHARACTER_NAME"
    state["locations"]["court"] = {"name": "Court"}
    state_path = tmp_path / f"{campaign_id}-state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return StateStore(str(tmp_path / f"{campaign_id}.db"), campaign_id, str(state_path))


def record_history(store: StateStore, count: int) -> list[int]:
    turn_ids: list[int] = []
    for party_turn in range(1, count + 1):
        turn_ids.append(
            store.record_turn(
                f"history-{party_turn}",
                f"history-request-{party_turn}",
                f"continuity-key player action {party_turn}",
                f"continuity-key narrator consequence {party_turn}",
                {},
                1,
                party_turn=party_turn,
            )
        )
    return turn_ids


def record_chapter(store: StateStore, turn_ids: list[int]) -> None:
    store.record_memory_chapter(
        from_turn_id=turn_ids[0],
        to_turn_id=turn_ids[-1],
        state_version=1,
        summary_text="continuity-key legacy chapter candidate",
        key_facts=[],
        open_threads=[],
        relationship_changes=[],
        player_promises=[],
        npc_obligations=[],
        model="service-model",
    )


def revision_seven_settings(campaign_id: str) -> Settings:
    return Settings(
        app_env="test",
        campaign_id=campaign_id,
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=7,
        llm_api_base="mock://success",
        llm_api_key="PRIVATE_PROVIDER_SECRET",
        local_llm_enabled=False,
        post_turn_helpers_inline=False,
        party_context_max_tokens=40_000,
        party_context_limit_tokens=40_000,
        party_context_completion_reserve_tokens=0,
        party_context_system_reserve_tokens=0,
        rp_story_memory_reserve_tokens=0,
        party_memory_retrieval_enabled=True,
        world_system_prompt="PRIVATE_WORLD_PROMPT",
    )


async def skip_post_turn_helpers(*_args: object, **_kwargs: object) -> None:
    return None


def run_recorded_turn(
    store: StateStore,
    settings: Settings,
    *,
    current_action: str,
    idempotency_key: str,
    request_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator = Adjudicator(settings, store)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    asyncio.run(
        adjudicator.handle_chat(
            party_chat_request(
                store,
                settings.narrative_model,
                PartyMessageRequest(content=current_action),
                settings,
            ),
            authorization=None,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    )


def recorded_prompt_evidence(
    store: StateStore,
    request_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], str, int]:
    with store.connect() as connection:
        turn = connection.execute(
            """
            SELECT id, prompt_json, metadata_json, narrative_response
            FROM turns
            WHERE campaign_id = ? AND request_id = ?
            """,
            (store.campaign_id, request_id),
        ).fetchone()
        trace = connection.execute(
            """
            SELECT payload_json
            FROM turn_trace_events
            WHERE campaign_id = ? AND request_id = ? AND phase_key = 'gateway_assembly'
            """,
            (store.campaign_id, request_id),
        ).fetchone()
    assert turn is not None
    assert trace is not None
    return (
        json.loads(turn["prompt_json"]),
        json.loads(turn["metadata_json"]),
        json.loads(trace["payload_json"]),
        str(turn["narrative_response"]),
        int(turn["id"]),
    )


def test_revision_seven_hard_budget_eviction_is_whole_block_with_exact_reasons() -> None:
    optional = [
        {"role": "system", "content": f"{prefix}\n" + marker * 600}
        for prefix, marker in (
            ("RETRIEVED_ARCHIVE_SCENES", "A"),
            ("LONG_TERM_PARTY_MEMORY", "B"),
            ("PARTY_LORE_CARDS", "C"),
            ("RELEVANT_CHARACTERS", "D"),
        )
    ]
    required = [
        {"role": "system", "content": "scenario rules"},
        {
            "role": "system",
            "content": (
                "PROMPT_AUTHORITY_HIERARCHY\n"
                "authoritative_outcome_current_action > uncovered_raw_tail > "
                "rp_story_memory > archive"
            ),
        },
        {"role": "system", "content": "RP_STORY_MEMORY\ncovered_through_turn_id=10"},
        {"role": "user", "content": "protected prior action"},
        {"role": "assistant", "content": "protected prior consequence"},
        {"role": "system", "content": "Relevant state summary: protected"},
        {"role": "system", "content": "AUTHORITATIVE_OUTCOME: protected"},
        {"role": "user", "content": "protected current action"},
    ]
    messages = [*required[:3], *optional, *required[3:]]
    hard_budget = estimate_tokens("\n".join(message["content"] for message in required))
    hard_diagnostics: dict[str, Any] = {}

    fitted = fit_messages_to_context(
        messages,
        hard_budget,
        protect_history=True,
        fail_on_token_overflow=True,
        diagnostics=hard_diagnostics,
    )

    assert fitted == required
    assert hard_diagnostics["omitted_blocks"] == [
        {"block_id": "retrieved_archive_scenes", "reason": "hard_input_budget"},
        {"block_id": "long_term_memory", "reason": "hard_input_budget"},
        {"block_id": "party_lore_cards", "reason": "hard_input_budget"},
        {"block_id": "relevant_characters", "reason": "hard_input_budget"},
    ]
    assert all(message not in fitted for message in optional)

    soft_diagnostics: dict[str, Any] = {}
    full_budget = estimate_tokens("\n".join(message["content"] for message in messages))
    percentage_only = fit_messages_to_context(
        messages,
        full_budget,
        max_prompt_chars=1,
        protect_history=True,
        fail_on_token_overflow=True,
        diagnostics=soft_diagnostics,
    )
    assert percentage_only == messages
    assert soft_diagnostics.get("omitted_blocks", []) == []


def test_registered_prompt_block_ids_match_their_real_emitters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = base_state()
    state["world_constraints"] = [
        {"id": "rule-1", "kind": "absolute", "source": "worldpack", "text": "Hold."}
    ]
    stale_state = json.loads(json.dumps(state))
    stale_scene = initial_scene_state(stale_state)
    stale_scene.update({"stale": True, "stale_reason": "world_change"})
    stale_state["scene_state"] = stale_scene

    store = make_store(tmp_path, "prompt-block-emitters", turn=1)
    relationships = RelationshipMechanics(store, {}, rp_contract_revision=7)
    monkeypatch.setattr(
        relationships.store,
        "pressure_rows",
        lambda _party_turn: [
            {
                "character_id": "advisor",
                "axis": "loyalty",
                "band": "steady",
                "causes": [{"weight": 1}],
                "events": [],
            }
        ],
    )
    monkeypatch.setattr(relationships, "_band", lambda *_args: {"label": "steady"})
    monkeypatch.setattr(
        relationships,
        "_qualitative_cause_pressure",
        lambda _causes: "recent cause",
    )
    relationship_pressure = relationships.pressure_block(
        1,
        {"advisor": "Advisor"},
        persist_seed_state=False,
        character_ids={"advisor"},
    )
    relationship_resolution = relationships.resolved_event_block(
        [
            {
                "action": "resolved",
                "character_id": "advisor",
                "event_id": "favour",
                "resolution": "delivered",
            }
        ],
        {"advisor": "Advisor"},
    )

    world_settings = replace(
        revision_seven_settings("prompt-block-emitters"),
        world_authors_note="Author note",
    )
    world_messages = NarrativeClient(world_settings).narrative_messages(
        narrative_request(),
        state,
        neutral_outcome(),
        repair_instruction=None,
    )
    world_system = next(
        message["content"]
        for message in world_messages
        if message["content"].startswith("WORLD_SYSTEM_PROMPT")
    )
    world_authors_note = next(
        message["content"]
        for message in world_messages
        if message["content"].startswith("WORLD_AUTHORS_NOTE")
    )
    gm_service = RPGMService(world_settings, store)
    monkeypatch.setattr(
        gm_service,
        "active_corrections",
        lambda: [
            {
                "target_slot": "characters.advisor.notes",
                "action": "replace",
                "before": "Old fact",
                "after": "Correct fact",
                "source_turn_id": 1,
                "status": "active",
            }
        ],
    )

    reanchor = scene_reanchor_prompt_block(stale_state)
    emitted_blocks = {
        "prompt_authority": prompt_authority_block(),
        "rp_story_memory": rp_story_memory_block(story_snapshot(), 10_000, 7),
        "long_term_memory": long_term_memory_block(legacy_memory_summary()),
        "world_system_prompt": world_system,
        "world_authors_note": world_authors_note,
        "training_turn_contract": training_turn_prompt_block({}),
        "training_interaction_contract": training_artifact_prompt_block({}),
        "relevant_characters": relevant_characters_block([{"id": "advisor"}]),
        "retrieved_archive_scenes": archived_memory_retrieval_block(
            [{"id": 1, "player_message": "ask", "narrative_response": "answer"}],
            1_000,
        ),
        "uncompacted_archive_fallback": uncompacted_archive_fallback_block(
            [{"id": 1, "player_message": "ask", "narrative_response": "answer"}],
            1_000,
        ),
        "party_lore_cards": party_lore_cards_block(
            [
                {
                    "id": "card-1",
                    "title": "Card",
                    "content": "Fact",
                    "keywords": ["fact"],
                    "source_turn_ids": [1],
                }
            ]
        ),
        "world_absolute_rules": world_absolute_rules_block(state),
        "player_corrections": gm_service.overlay_block(),
        "relationship_pressure": relationship_pressure,
        "relationship_event_resolution": relationship_resolution,
        "world_events": world_clock_prompt_projection(
            {
                "world_clock": {
                    "date": "2026-01-01T00:00:00Z",
                    "pending_announcements": [],
                    "event_statuses": {},
                    "world_facts": [],
                }
            },
            {
                "events": [
                    {
                        "id": "deadline",
                        "summary": "The deadline approaches.",
                        "condition": {
                            "type": "date_gte",
                            "date": "2026-01-02T00:00:00Z",
                        },
                    }
                ]
            },
        )["block"],
        "scene_state_contract": scene_state_prompt_block(state),
        "scene_reanchor_baseline": reanchor,
    }

    assert len(PROMPT_SYSTEM_BLOCK_IDS) == len(emitted_blocks)
    assert len({block_id for _prefix, block_id in PROMPT_SYSTEM_BLOCK_IDS}) == len(
        PROMPT_SYSTEM_BLOCK_IDS
    )
    for prefix, expected_id in PROMPT_SYSTEM_BLOCK_IDS:
        content = emitted_blocks[expected_id]
        assert isinstance(content, str) and content
        assert content.startswith(prefix)
        assert prompt_block_id({"role": "system", "content": content}, 1) == expected_id


def test_revision_seven_recorded_prompt_assembly_is_content_free_and_matches_all_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = "prompt-authority-parity"
    store = make_store(tmp_path, campaign_id, turn=3)
    turn_ids = record_history(store, 3)
    record_chapter(store, turn_ids)
    store.record_rp_story_memory(
        from_turn_id=turn_ids[0],
        to_turn_id=turn_ids[0],
        state_version=1,
        memory=empty_story_memory(),
        model="service-model",
    )
    settings = revision_seven_settings(campaign_id)
    current_action = "continuity-key PRIVATE_PLAYER_ACTION PRIVATE_CHARACTER_NAME"
    request_id = "req_prompt_authority_parity"

    run_recorded_turn(
        store,
        settings,
        current_action=current_action,
        idempotency_key="prompt-authority-parity",
        request_id=request_id,
        monkeypatch=monkeypatch,
    )
    prompt_messages, metadata, trace_payload, response_text, committed_turn_id = (
        recorded_prompt_evidence(store, request_id)
    )

    recorded = metadata["prompt_assembly"]
    traced = trace_payload["details"]["prompt_assembly"]
    inspector = PromptInspector(settings, store)
    last_preview = inspector.preview("", source="last")
    context = estimate_party_context(store, settings, None)

    assert_prompt_assembly_schema(recorded)
    assert traced == recorded
    assert last_preview["source"] == "recorded_last_turn"
    assert last_preview["prompt_assembly"] == recorded
    assert context["prompt_source"] == "recorded_last_turn"
    assert context["prompt_assembly"] == recorded
    assert recorded["story_memory_covered_through_turn_id"] == turn_ids[0]
    assert recorded["raw_tail_turn_ids"] == turn_ids[1:]
    assert recorded["included_block_ids"] == expected_prompt_block_ids(prompt_messages)
    assert "prompt_authority" in recorded["included_block_ids"]
    assert "rp_story_memory" in recorded["included_block_ids"]
    assert "long_term_memory" not in recorded["included_block_ids"]
    assert recorded["omitted_blocks"] == [
        {"block_id": "long_term_memory", "reason": "structural_deduplication"}
    ]

    contents = [str(message.get("content") or "") for message in prompt_messages]
    authority_blocks = [
        content for content in contents if content.startswith("PROMPT_AUTHORITY_HIERARCHY")
    ]
    assert len(authority_blocks) == 1
    authority_block = authority_blocks[0]
    assert authority_block.splitlines()[0] == "PROMPT_AUTHORITY_HIERARCHY"
    tier_offsets = [authority_block.index(tier) for tier in AUTHORITY_ORDER]
    assert tier_offsets == sorted(tier_offsets)
    assert "intent, not an automatic fact" in authority_block.lower()
    assert sum(content.startswith("RP_STORY_MEMORY") for content in contents) == 1
    assert not any(content.startswith("LONG_TERM_PARTY_MEMORY") for content in contents)
    retrieval = next(
        content for content in contents if content.startswith("RETRIEVED_ARCHIVE_SCENES")
    )
    assert "continuity-key player action 1" in retrieval
    assert "continuity-key player action 2" not in retrieval
    assert prompt_messages[-1] == {"role": "user", "content": current_action}

    diagnostic_text = json.dumps(recorded, ensure_ascii=False)
    for private_value in (
        "PRIVATE_WORLD_PROMPT",
        "PRIVATE_PLAYER_ACTION",
        "PRIVATE_CHARACTER_NAME",
        "PRIVATE_STATE_VALUE",
        "PRIVATE_PROVIDER_SECRET",
        response_text,
    ):
        assert private_value not in diagnostic_text
    provider_prompt_text = json.dumps(prompt_messages, ensure_ascii=False)
    assert PROMPT_ASSEMBLY_SCHEMA not in provider_prompt_text
    assert "raw_tail_turn_ids" not in provider_prompt_text

    current_preview = inspector.preview(
        "continuity-key a different current dry-run action",
        source="current",
    )
    current_assembly = current_preview["prompt_assembly"]
    assert_prompt_assembly_schema(current_assembly)
    assert current_assembly != recorded
    assert current_assembly["raw_tail_turn_ids"] == [*turn_ids[1:], committed_turn_id]


def test_excluded_latest_turn_keeps_recorded_prompt_assembly_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = "prompt-authority-excluded-turn"
    store = make_store(tmp_path, campaign_id, turn=1)
    record_history(store, 1)
    settings = revision_seven_settings(campaign_id)
    request_id = "req_prompt_authority_excluded_turn"

    run_recorded_turn(
        store,
        settings,
        current_action="record a diagnostic before rollback",
        idempotency_key="prompt-authority-excluded-turn",
        request_id=request_id,
        monkeypatch=monkeypatch,
    )
    _prompt, metadata, _trace, _response, committed_turn_id = recorded_prompt_evidence(
        store, request_id
    )
    recorded = metadata["prompt_assembly"]
    assert store.current_version() > 1

    store.rollback(target_version=1, scene_state_enabled=True)

    with store.connect() as connection:
        excluded = connection.execute(
            """
            SELECT excluded_from_memory, metadata_json
            FROM turns
            WHERE campaign_id = ? AND id = ?
            """,
            (campaign_id, committed_turn_id),
        ).fetchone()
    assert excluded is not None
    assert int(excluded["excluded_from_memory"]) == 1
    assert json.loads(excluded["metadata_json"])["rollback_excluded"] is True
    assert all(
        int(turn["id"]) != committed_turn_id
        for turn in store.turns_for_memory(to_turn_id=committed_turn_id)
    )
    assert all(
        int(turn["id"]) != committed_turn_id
        for turn in store.turns_for_memory(
            to_turn_id=committed_turn_id,
            include_noncanonical_fallback=True,
        )
    )

    last_preview = PromptInspector(settings, store).preview("", source="last")
    context = estimate_party_context(store, settings, None)

    assert last_preview["source"] == "recorded_last_turn"
    assert last_preview["prompt_assembly"] == recorded
    assert context["prompt_source"] == "recorded_last_turn"
    assert context["prompt_assembly"] == recorded
    public_diagnostics = json.dumps(
        {"preview": last_preview, "context": context},
        ensure_ascii=False,
    )
    assert "rollback_excluded" not in public_diagnostics
    assert "applied_scene_delta" not in public_diagnostics
    assert "dropped_scene_delta" not in public_diagnostics


def test_revision_seven_without_story_snapshot_keeps_long_term_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = "prompt-authority-no-story"
    store = make_store(tmp_path, campaign_id, turn=1)
    turn_ids = record_history(store, 1)
    record_chapter(store, turn_ids)
    settings = replace(
        revision_seven_settings(campaign_id),
        party_memory_retrieval_enabled=False,
    )
    request_id = "req_prompt_authority_no_story"

    run_recorded_turn(
        store,
        settings,
        current_action="continue without a story snapshot",
        idempotency_key="prompt-authority-no-story",
        request_id=request_id,
        monkeypatch=monkeypatch,
    )
    prompt_messages, metadata, _trace, _response, _turn_id = recorded_prompt_evidence(
        store, request_id
    )
    assembly = metadata["prompt_assembly"]
    contents = [str(message.get("content") or "") for message in prompt_messages]

    assert_prompt_assembly_schema(assembly)
    assert assembly["story_memory_covered_through_turn_id"] == 0
    assert assembly["raw_tail_turn_ids"] == turn_ids
    assert assembly["included_block_ids"] == expected_prompt_block_ids(prompt_messages)
    assert "prompt_authority" in assembly["included_block_ids"]
    assert "long_term_memory" in assembly["included_block_ids"]
    assert "rp_story_memory" not in assembly["included_block_ids"]
    assert {item["reason"] for item in assembly["omitted_blocks"]} <= {
        "structural_deduplication",
        "hard_input_budget",
    }
    assert {
        (item["block_id"], item["reason"]) for item in assembly["omitted_blocks"]
    }.isdisjoint({("long_term_memory", "structural_deduplication")})
    assert sum(content.startswith("LONG_TERM_PARTY_MEMORY") for content in contents) == 1
    assert not any(content.startswith("RP_STORY_MEMORY") for content in contents)


def test_revision_six_recorded_public_surfaces_do_not_gain_prompt_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign_id = "prompt-authority-revision-six"
    store = make_store(tmp_path, campaign_id, turn=1)
    turn_ids = record_history(store, 1)
    record_chapter(store, turn_ids)
    settings = replace(
        revision_seven_settings(campaign_id),
        rp_contract_revision=6,
        party_memory_retrieval_enabled=False,
    )
    request_id = "req_prompt_authority_revision_six"

    run_recorded_turn(
        store,
        settings,
        current_action="revision six public payload sentinel",
        idempotency_key="prompt-authority-revision-six",
        request_id=request_id,
        monkeypatch=monkeypatch,
    )
    _prompt, metadata, trace_payload, _response, _turn_id = recorded_prompt_evidence(
        store, request_id
    )
    last_preview = PromptInspector(settings, store).preview("", source="last")
    context = estimate_party_context(store, settings, None)

    assert "prompt_assembly" not in metadata
    assert "prompt_assembly" not in trace_payload["details"]
    assert last_preview["source"] == "recorded_last_turn"
    assert "prompt_assembly" not in last_preview
    assert context["prompt_source"] == "recorded_last_turn"
    assert "prompt_assembly" not in context


def legacy_memory_summary() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "memory_type": "chapter",
            "from_turn_id": 1,
            "to_turn_id": 1,
            "state_version": 1,
            "summary_text": "legacy memory must remain selected",
            "key_facts": [],
            "open_threads": [],
            "relationship_changes": [],
            "player_promises": [],
            "npc_obligations": [],
        }
    ]


def story_snapshot() -> dict[str, Any]:
    return {
        "id": 1,
        "revision": 1,
        "from_turn_id": 1,
        "to_turn_id": 1,
        "state_version": 1,
        "memory": empty_story_memory(),
    }


def narrative_request() -> ChatCompletionRequest:
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[ChatMessage(role="user", content="legacy current action")],
        stream=False,
    )
    request._raw_transcript_chars = None
    return request


def neutral_outcome() -> Outcome:
    return Outcome(
        check_id="prompt-authority-legacy",
        action_type="feasibility",
        actor="player",
        result="narrative_continuation",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        authoritative_block="AUTHORITATIVE_OUTCOME: continue",
    )


@pytest.mark.parametrize(("revision", "expected_blocks"), [(6, 0), (7, 1)])
def test_prompt_authority_is_revision_seven_only_in_compact_repair_messages(
    revision: int,
    expected_blocks: int,
) -> None:
    settings = Settings(scenario_type="rp", rp_contract_revision=revision)

    messages = NarrativeClient(settings).repair_messages(
        base_state(),
        neutral_outcome(),
        repair_instruction="correct the continuity error",
        failed_response_text="failed narrator response",
    )
    authority_blocks = [
        str(message.get("content") or "")
        for message in messages
        if str(message.get("content") or "").startswith("PROMPT_AUTHORITY_HIERARCHY")
    ]

    assert len(authority_blocks) == expected_blocks
    if revision == 7:
        assert "intent, not an automatic fact" in authority_blocks[0].lower()


@pytest.mark.parametrize("revision", range(7))
def test_rp_revisions_zero_through_six_keep_legacy_prompt_layers(revision: int) -> None:
    settings = Settings(
        scenario_type="rp",
        rp_contract_revision=revision,
        party_context_max_tokens=40_000,
        party_context_limit_tokens=40_000,
        party_context_completion_reserve_tokens=0,
    )

    messages = NarrativeClient(settings).narrative_messages(
        narrative_request(),
        base_state(),
        neutral_outcome(),
        repair_instruction=None,
        memory_summary=legacy_memory_summary(),
        rp_story_memory=story_snapshot() if revision >= 2 else None,
    )
    contents = [str(message.get("content") or "") for message in messages]

    assert not any(content.startswith("PROMPT_AUTHORITY_HIERARCHY") for content in contents)
    assert sum(content.startswith("LONG_TERM_PARTY_MEMORY") for content in contents) == 1
    if revision >= 2:
        assert sum(content.startswith("RP_STORY_MEMORY") for content in contents) == 1


@pytest.mark.parametrize("scenario_type", ["training"])
def test_training_mode_does_not_receive_revision_seven_prompt_authority(
    scenario_type: str,
) -> None:
    settings = Settings(
        scenario_type=scenario_type,
        rp_contract_revision=7,
        party_context_max_tokens=40_000,
        party_context_limit_tokens=40_000,
        party_context_completion_reserve_tokens=0,
    )

    messages = NarrativeClient(settings).narrative_messages(
        narrative_request(),
        base_state(),
        neutral_outcome(),
        repair_instruction=None,
        memory_summary=legacy_memory_summary(),
        rp_story_memory=story_snapshot(),
    )
    contents = [str(message.get("content") or "") for message in messages]

    assert not any(content.startswith("PROMPT_AUTHORITY_HIERARCHY") for content in contents)
    assert sum(content.startswith("LONG_TERM_PARTY_MEMORY") for content in contents) == 1
    assert not any(content.startswith("RP_STORY_MEMORY") for content in contents)
