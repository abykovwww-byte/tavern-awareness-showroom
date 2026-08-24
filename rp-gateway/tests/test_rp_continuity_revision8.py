from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome
from app.services.adjudicator import Adjudicator
from app.services.character_retrieval import relationship_scene_character_ids
from app.services.context_budget import estimate_tokens
from app.services.narrative import (
    NarrativeClient,
    PromptBudgetExceeded,
    fit_messages_to_context,
    meaningful_rp_outcome_block,
    party_lore_cards_block,
    prompt_cache_observability,
    revision_eight_stable_prefix_hash,
    world_absolute_rules_block,
)
from app.services.prompt_tools import PromptInspector
from app.services.rp_history import (
    AUTO_START_HISTORY_MESSAGE,
    RP_MEMORY_SECTION_KEYS,
    eligible_rp_turns,
    raw_history_window,
    recent_rp_scan_text,
    removable_covered_history_units,
    rp_turn_messages,
    story_memory_safe_coverage,
)
from app.services.rp_story_memory import empty_sectioned_story_memory
from app.services.state_store import StateStore


def narrative_turn(turn_id: int, *, turn_kind: str | None = "narrative") -> dict[str, Any]:
    return {
        "id": turn_id,
        "player_message": f"player-{turn_id}",
        "narrative_response": f"narrator-{turn_id}",
        "metadata": {"turn_kind": turn_kind},
    }


def sectioned_snapshot(*, coverages: dict[str, int] | None = None) -> dict[str, Any]:
    memory = empty_sectioned_story_memory()
    for section_key in RP_MEMORY_SECTION_KEYS:
        memory["section_status"][section_key] = {
            "coverage": (coverages or {}).get(section_key, 3),
            "status": "fresh",
        }
    return {
        "id": 1,
        "revision": 1,
        "from_turn_id": 1,
        "to_turn_id": max(
            (int(item["coverage"]) for item in memory["section_status"].values()),
            default=0,
        ),
        "state_version": 1,
        "memory": memory,
    }


def outcome(**updates: Any) -> Outcome:
    payload: dict[str, Any] = {
        "check_id": "revision-eight-contract",
        "action_type": "feasibility",
        "actor": "player",
        "result": "narrative_continuation",
        "roll": 0,
        "difficulty": 0,
        "modifiers": {},
        "final_score": 0,
        "authoritative_block": "AUTHORITATIVE_OUTCOME: generic continuation",
    }
    payload.update(updates)
    return Outcome(**payload)


def test_eligible_rp_turns_accepts_opening_and_legacy_null_but_rejects_nonplayable_units() -> None:
    turns = [
        {
            "id": 1,
            "player_message": AUTO_START_HISTORY_MESSAGE,
            "narrative_response": "opening",
            "metadata": {"turn_kind": "opening_scene"},
        },
        narrative_turn(2, turn_kind=None),
        narrative_turn(3),
        {
            "id": 4,
            "player_message": "out of character correction",
            "narrative_response": "accepted",
            "metadata": {"turn_kind": "gm_correction"},
        },
        {
            "id": 5,
            "player_message": "missing narrator",
            "narrative_response": "",
            "metadata": {"turn_kind": "narrative"},
        },
        {
            "id": 6,
            "player_message": "",
            "narrative_response": "missing player",
            "metadata": {"turn_kind": "narrative"},
        },
        {
            "id": 7,
            "player_message": AUTO_START_HISTORY_MESSAGE,
            "narrative_response": "",
            "metadata": {"turn_kind": "opening_scene"},
        },
        {
            "id": 8,
            "player_message": "training action",
            "narrative_response": "training response",
            "metadata": {"turn_kind": "training"},
        },
    ]

    assert [turn["id"] for turn in eligible_rp_turns(turns)] == [1, 2, 3]


def test_raw_history_window_keeps_last_fifty_and_every_uncovered_unit() -> None:
    turns = [narrative_turn(turn_id) for turn_id in range(1, 81)]

    mostly_uncovered = raw_history_window(turns, safe_coverage=10, window_turns=50)
    recent_floor = raw_history_window(turns, safe_coverage=60, window_turns=50)

    assert [turn["id"] for turn in mostly_uncovered] == list(range(11, 81))
    assert [turn["id"] for turn in recent_floor] == list(range(25, 81))
    assert removable_covered_history_units(
        mostly_uncovered,
        safe_coverage=10,
        minimum_turns=20,
    ) == 0
    assert removable_covered_history_units(
        recent_floor,
        safe_coverage=60,
        minimum_turns=20,
    ) == 36


def test_raw_history_window_moves_its_anchor_only_once_per_eight_turns() -> None:
    turns = [narrative_turn(turn_id) for turn_id in range(1, 75)]

    starts = {
        count: raw_history_window(
            turns[:count],
            safe_coverage=count,
            window_turns=50,
        )[0]["id"]
        for count in range(58, 74)
    }

    assert {starts[count] for count in range(58, 66)} == {9}
    assert {starts[count] for count in range(66, 74)} == {17}
    assert all((start - 1) % 8 == 0 for start in starts.values())


def test_story_memory_safe_coverage_uses_the_minimum_section_and_fails_closed() -> None:
    coverages = {
        "situation": 12,
        "threads": 11,
        "characters": 7,
        "assets_and_rules": 10,
        "chronology_and_hooks": 9,
    }
    snapshot = sectioned_snapshot(coverages=coverages)

    assert story_memory_safe_coverage(snapshot) == 7
    assert story_memory_safe_coverage({"to_turn_id": 6, "memory": {}}) == 0
    assert story_memory_safe_coverage(None) == 0

    missing_section = deepcopy(snapshot)
    del missing_section["memory"]["section_status"]["characters"]
    assert story_memory_safe_coverage(missing_section) == 0

    invalid_boolean_coverage = deepcopy(snapshot)
    invalid_boolean_coverage["memory"]["section_status"]["characters"]["coverage"] = True
    assert story_memory_safe_coverage(invalid_boolean_coverage) == 0


def test_opening_history_suppresses_only_the_exact_auto_start_sentinel() -> None:
    exact_opening = {
        "player_message": AUTO_START_HISTORY_MESSAGE,
        "narrative_response": "The opening scene",
    }
    lookalike_opening = {
        "player_message": AUTO_START_HISTORY_MESSAGE + " ",
        "narrative_response": "A later scene",
    }

    assert rp_turn_messages(exact_opening) == [("assistant", "The opening scene")]
    assert rp_turn_messages(lookalike_opening) == [
        ("user", AUTO_START_HISTORY_MESSAGE + " "),
        ("assistant", "A later scene"),
    ]

    scan = recent_rp_scan_text(
        [
            exact_opening | {"id": 1, "metadata": {"turn_kind": "opening_scene"}},
            narrative_turn(2),
        ],
        "current action",
    )
    assert AUTO_START_HISTORY_MESSAGE not in scan
    assert scan.splitlines() == [
        "The opening scene",
        "player-2",
        "narrator-2",
        "current action",
    ]


def test_revision_eight_relationship_scope_ignores_stale_seed_signals() -> None:
    class GuardedState(dict[str, Any]):
        def get(self, key: str, default: Any = None) -> Any:
            if key in {"scene_state", "player", "active_threads"}:
                raise AssertionError(f"revision 8 must not read {key}")
            return super().get(key, default)

    state = GuardedState(
        characters={
            "advisor": {"name": "Advisor"},
            "guard": {"name": "Guard"},
        }
    )

    assert relationship_scene_character_ids(
        state,
        "I ask Advisor to stay.",
        outcome_target="guard",
        use_scene_state=False,
        use_seed_signals=False,
    ) == {"advisor", "guard"}


def test_revision_eight_prompt_omits_legacy_layers_and_preserves_block_order() -> None:
    settings = Settings(
        scenario_type="rp",
        rp_contract_revision=8,
        party_context_max_tokens=100_000,
        party_context_limit_tokens=100_000,
        party_context_completion_reserve_tokens=0,
        world_system_prompt="World premise",
        world_authors_note="Immediate author emphasis",
    )
    state = {
        "player": {"location": "court"},
        "characters": {
            "advisor": {"name": "Advisor", "location": "court"},
        },
        "relationships": {
            "player-advisor": {"from": "player", "to": "advisor", "trust": 5},
        },
        "active_threads": [{"character_id": "advisor"}],
        "world_constraints": [
            {
                "id": "authored-rule",
                "kind": "absolute",
                "source": "worldpack:test/state-seed.json",
                "text": "Never decide the player character's inner choice.",
            }
        ],
        "scene_state": {
            "stale": False,
            "location_id": "court",
            "present_character_ids": ["advisor"],
        },
    }
    request = ChatCompletionRequest(
        model="mock-model",
        messages=[
            ChatMessage(role="system", content="PARTY_LORE_CARDS\nA changing gate card."),
            ChatMessage(role="user", content="Prior action"),
            ChatMessage(role="assistant", content="Prior consequence"),
            ChatMessage(role="user", content="I ask Advisor to open the gate"),
        ],
    )
    diagnostics: dict[str, Any] = {}

    messages = NarrativeClient(settings).narrative_messages(
        request,
        state,
        outcome(target="the gate", consequences=["The gate must remain closed"]),
        repair_instruction=None,
        memory_summary={"summary_text": "legacy duplicate"},
        rp_story_memory=sectioned_snapshot(),
        relationship_pressure="RELATIONSHIP_PRESSURE\nAdvisor remembers the unpaid debt.",
        diagnostics=diagnostics,
    )

    labels: list[str] = []
    for index, message in enumerate(messages):
        content = message["content"]
        if index == 0:
            labels.append("system_rules")
        elif content.startswith("WORLD_SYSTEM_PROMPT"):
            labels.append("world_system_prompt")
        elif content.startswith("WORLD_ABSOLUTE_RULES"):
            labels.append("world_absolute_rules")
        elif content.startswith("RP_STORY_MEMORY"):
            labels.append("rp_story_memory")
        elif content.startswith("PARTY_LORE_CARDS"):
            labels.append("party_lore_cards")
        elif content == "Prior action":
            labels.append("raw_user")
        elif content == "Prior consequence":
            labels.append("raw_assistant")
        elif content.startswith("AUTHORITATIVE_OUTCOME"):
            labels.append("authoritative_outcome")
        elif content.startswith("RELATIONSHIP_PRESSURE"):
            labels.append("relationship_pressure")
        elif content.startswith("WORLD_AUTHORS_NOTE"):
            labels.append("world_authors_note")
        elif content == "I ask Advisor to open the gate":
            labels.append("current_action")
        else:
            labels.append("unexpected")

    assert labels == [
        "system_rules",
        "world_system_prompt",
        "world_absolute_rules",
        "raw_user",
        "raw_assistant",
        "rp_story_memory",
        "party_lore_cards",
        "authoritative_outcome",
        "relationship_pressure",
        "world_authors_note",
        "current_action",
    ]
    contents = [message["content"] for message in messages]
    assert not any(content.startswith("PROMPT_AUTHORITY_HIERARCHY") for content in contents)
    assert not any(content.startswith("SCENE_STATE_CONTRACT") for content in contents)
    assert not any(content.startswith("RELEVANT_CHARACTERS") for content in contents)
    assert not any(content.startswith("LONG_TERM_PARTY_MEMORY") for content in contents)
    assert not any(content.startswith("Relevant state summary:") for content in contents)
    assert diagnostics["omitted_blocks"] == [
        {"block_id": "long_term_memory", "reason": "disabled_revision8"}
    ]


def test_revision_eight_cache_hash_covers_the_anchored_fifty_unit_base() -> None:
    stable_system = [
        {"role": "system", "content": "Narrator rules"},
        {"role": "system", "content": "WORLD_SYSTEM_PROMPT\nWorld rules"},
        {"role": "system", "content": "WORLD_ABSOLUTE_RULES\n1. Absolute rule"},
    ]
    history = [
        message
        for turn_id in range(1, 51)
        for message in (
            {"role": "user", "content": f"player-{turn_id}"},
            {"role": "assistant", "content": f"narrator-{turn_id}"},
        )
    ]
    first = [
        *stable_system,
        *history,
        {"role": "system", "content": "RP_STORY_MEMORY\ncoverage 48"},
        {"role": "user", "content": "current-51"},
    ]
    next_turn = [
        *stable_system,
        *history,
        {"role": "user", "content": "player-51"},
        {"role": "assistant", "content": "narrator-51"},
        {"role": "system", "content": "RP_STORY_MEMORY\ncoverage 51"},
        {"role": "system", "content": "PARTY_LORE_CARDS\nchanged card"},
        {"role": "user", "content": "current-52"},
    ]
    shifted = [
        *stable_system,
        *history[16:],
        {"role": "user", "content": "player-51"},
        {"role": "assistant", "content": "narrator-51"},
        {"role": "user", "content": "player-52"},
        {"role": "assistant", "content": "narrator-52"},
        {"role": "system", "content": "RP_STORY_MEMORY\ncoverage 52"},
        {"role": "user", "content": "current-53"},
    ]

    first_hash = revision_eight_stable_prefix_hash(first)
    assert revision_eight_stable_prefix_hash(next_turn) == first_hash
    assert revision_eight_stable_prefix_hash(shifted) != first_hash
    assert prompt_cache_observability(
        {
            "usage": {
                "prompt_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 77},
            }
        },
        next_turn,
    ) == {
        "cached_prompt_tokens": 77,
        "prompt_tokens": 100,
        "stable_prompt_prefix_hash": first_hash,
    }


def test_revision_eight_absolute_rules_are_plain_prose_without_storage_metadata() -> None:
    block = world_absolute_rules_block(
        {
            "world_constraints": [
                {
                    "id": "rule-one",
                    "kind": "absolute",
                    "source": "worldpack:test/state-seed.json",
                    "text": "First authored rule.",
                },
                {
                    "id": "rule-two",
                    "kind": "absolute",
                    "source": "worldpack:test/state-seed.json",
                    "text": "Second authored rule.",
                },
                {
                    "id": "runtime-note",
                    "kind": "derived",
                    "source": "runtime",
                    "text": "Must not enter the absolute block.",
                },
            ]
        },
        rp_contract_revision=8,
    )

    assert block is not None
    assert block.splitlines() == [
        "WORLD_ABSOLUTE_RULES",
        "Эти правила мира обязательны; не ослабляй и не переиначивай их.",
        "1. First authored rule.",
        "2. Second authored rule.",
    ]
    assert "rule-one" not in block
    assert "worldpack:" not in block
    assert "runtime-note" not in block
    assert "{" not in block


def test_revision_eight_omits_generic_outcome_but_keeps_turn_specific_authority() -> None:
    generic = outcome(
        consequences=[
            "Continue the roleplaying scene from the player's stated intent.",
            "Apply active WorldPack rules, current state, character goals, relationships, and prior consequences.",
            "Leave consequential choices and the player character's inner decisions to the player.",
        ]
    )
    specific = outcome(
        target="north gate",
        blocked_reasons=["The lock has not been opened"],
        consequences=["The guard hears the attempt"],
    )

    assert meaningful_rp_outcome_block(generic) is None
    assert meaningful_rp_outcome_block(specific) == (
        "AUTHORITATIVE_OUTCOME\n"
        "Цель текущего действия: north gate.\n"
        "Обязательное ограничение: The lock has not been opened.\n"
        "Обязательное последствие: The guard hears the attempt."
    )


def test_revision_eight_opening_outcome_omits_generic_mechanics_and_storage_id() -> None:
    block = meaningful_rp_outcome_block(
        outcome(
            actor="system",
            target="opening_scene",
            consequences=[
                "Initial scene is introduced; no player decision has been resolved yet."
            ],
            authoritative_block=(
                "AUTHORITATIVE_OUTCOME: No mechanical check was rolled for opening_scene."
            ),
        )
    )

    assert block is not None
    assert "начальная сцена" in block
    assert "opening_scene" not in block
    assert "mechanical" not in block


def test_revision_eight_accepts_stable_partial_section_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_env="test",
        campaign_id="rev8-partial-stable",
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=8,
        llm_api_base="mock://success",
        llm_api_key="test-key",
        openrouter_api_base="mock://success",
        service_openrouter_api_key="test-key",
        post_turn_helpers_inline=False,
        party_context_max_tokens=100_000,
        party_context_limit_tokens=100_000,
        party_context_min_history_tokens=1,
        party_context_completion_reserve_tokens=0,
        party_memory_retrieval_enabled=False,
    )
    store = StateStore(
        str(tmp_path / "partial-stable.db"),
        settings.campaign_id,
        str(tmp_path / "partial-stable-state.json"),
    )
    for party_turn in range(1, 52):
        store.record_turn(
            f"existing-turn-{party_turn}",
            f"existing-request-{party_turn}",
            f"Player action {party_turn}",
            f"Narrator consequence {party_turn}",
            {},
            1,
            party_turn=party_turn,
        )
    memory = empty_sectioned_story_memory()
    coverages = {
        "situation": 8,
        "threads": 7,
        "characters": 8,
        "assets_and_rules": 8,
        "chronology_and_hooks": 8,
    }
    for section_key, coverage in coverages.items():
        memory["section_status"][section_key] = {
            "coverage": coverage,
            "status": "fresh" if coverage == 8 else "stale",
        }
    snapshot = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=8,
        state_version=1,
        memory=memory,
        model="deepseek/deepseek-v4-pro",
        contributing_turn_ids=list(range(1, 9)),
        update_id="partial-stable-update",
        allow_same_coverage=True,
    )
    assert snapshot is not None
    adjudicator = Adjudicator(settings, store)
    provider_coverages: list[int | None] = []
    original_complete = adjudicator.narrative.complete

    async def tracked_complete(
        request: ChatCompletionRequest,
        *args: object,
        **kwargs: object,
    ) -> dict[str, Any]:
        provider_coverages.append(request._rp_story_memory_covered_through_turn_id)
        return await original_complete(request, *args, **kwargs)

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)

    asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Current player action")],
            ),
            authorization=None,
            idempotency_key="rev8-partial-stable",
            request_id="req-rev8-partial-stable",
        )
    )

    assert provider_coverages == [7]
    committed = store.latest_turn(include_prompt=True, include_response=True)
    assert committed is not None
    metadata = store.turn_metadata(int(committed["id"]))
    assert metadata["cached_prompt_tokens"] == 0
    assert metadata["prompt_tokens"] == 10
    assert len(metadata["stable_prompt_prefix_hash"]) == 64


def test_revision_eight_overflow_does_not_force_story_memory_catch_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        app_env="test",
        campaign_id="rev8-no-overflow-catch-up",
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=8,
        llm_api_base="mock://success",
        llm_api_key="test-key",
        openrouter_api_base="mock://success",
        service_openrouter_api_key="test-key",
        post_turn_helpers_inline=False,
    )
    store = StateStore(
        str(tmp_path / "no-overflow-catch-up.db"),
        settings.campaign_id,
        str(tmp_path / "no-overflow-catch-up-state.json"),
    )
    adjudicator = Adjudicator(settings, store)
    assert adjudicator.rp_story_memory is not None
    catch_up_calls = 0

    def overflow(*args: object, **kwargs: object) -> list[dict[str, str]]:
        raise PromptBudgetExceeded(estimated_tokens=101, token_budget=100)

    async def forbidden_catch_up(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal catch_up_calls
        catch_up_calls += 1
        raise AssertionError("revision 8 overflow must fail before story-memory refresh")

    monkeypatch.setattr(adjudicator.narrative, "narrative_messages", overflow)
    monkeypatch.setattr(adjudicator.rp_story_memory, "catch_up", forbidden_catch_up)

    with pytest.raises(PromptBudgetExceeded):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Current player action")],
                ),
                authorization=None,
                idempotency_key="rev8-no-overflow-catch-up",
                request_id="req-rev8-no-overflow-catch-up",
            )
        )

    assert catch_up_calls == 0
    assert store.turns_for_memory() == []


def test_revision_eight_prompt_inspection_skips_legacy_memory_and_archive_readers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        campaign_id="rev8-inspection-readers",
        scenario_type="rp",
        rp_contract_revision=8,
    )
    store = StateStore(
        str(tmp_path / "inspection-readers.db"),
        settings.campaign_id,
        str(tmp_path / "inspection-readers-state.json"),
    )

    def forbidden(*args: object, **kwargs: object) -> Any:
        raise AssertionError("revision 8 inspection must not read a disabled legacy layer")

    monkeypatch.setattr(store, "memory_for_prompt", forbidden)
    monkeypatch.setattr(store, "latest_memory_summary", forbidden)
    monkeypatch.setattr(store, "memory_chapters", forbidden)
    monkeypatch.setattr(store, "explain_archived_retrieval", forbidden)

    inspection = PromptInspector(settings, store).memory_inspection("Где астролябия?")

    assert inspection["chapters"] == {"included": [], "excluded": []}
    assert inspection["retrieval"] == []


def test_revision_eight_prompt_inspection_reports_the_fitted_raw_window(
    tmp_path: Path,
) -> None:
    settings = Settings(
        campaign_id="rev8-inspection-fitted-raw",
        scenario_type="rp",
        rp_contract_revision=8,
        rp_raw_history_window_turns=50,
    )
    store = StateStore(
        str(tmp_path / "inspection-fitted-raw.db"),
        settings.campaign_id,
        str(tmp_path / "inspection-fitted-raw-state.json"),
    )
    for index in range(1, 56):
        store.record_turn(
            f"turn-{index}",
            f"request-{index}",
            f"player {index}",
            f"narrator {index}",
            {},
            1,
            metadata={"turn_kind": "narrative"},
            party_turn=index,
        )

    memory = empty_sectioned_story_memory()
    memory["observed_through_turn_id"] = 55
    for section_key in RP_MEMORY_SECTION_KEYS:
        memory["section_status"][section_key] = {
            "coverage": 55,
            "status": "fresh",
        }
    snapshot = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=55,
        state_version=1,
        memory=memory,
        model="deepseek/deepseek-v4-pro",
        contributing_turn_ids=list(range(1, 56)),
        update_id="update-55",
    )
    assert snapshot is not None

    fitted_ids = list(range(36, 56))
    inspection = PromptInspector(settings, store).memory_inspection(
        "current action",
        fitted_raw_turn_ids=fitted_ids,
    )

    assert inspection["raw"] == {
        "included_turn_ids": fitted_ids,
        "excluded_turn_ids": list(range(1, 36)),
        "excluded_reason": "hard_input_budget",
    }
    assert inspection["fallback"]["active"] is False
    assert inspection["fallback"]["turn_ids"] == []


def test_revision_eight_overflow_evicts_lore_then_complete_raw_units() -> None:
    messages = [
        {"role": "system", "content": "WORLD_SYSTEM_PROMPT\nmandatory"},
        {"role": "system", "content": "PARTY_LORE_CARDS\n" + "optional lore " * 300},
        {"role": "assistant", "content": "opening consequence " + "o" * 300},
        {"role": "user", "content": "old action " + "a" * 300},
        {"role": "assistant", "content": "old consequence " + "b" * 300},
        {"role": "user", "content": "recent action"},
        {"role": "assistant", "content": "recent consequence"},
        {"role": "user", "content": "current action"},
    ]
    original = deepcopy(messages)
    expected = [messages[0], *messages[5:]]
    diagnostics: dict[str, Any] = {}

    fitted = fit_messages_to_context(
        messages,
        estimate_tokens("\n".join(message["content"] for message in expected)),
        protect_history=True,
        fail_on_token_overflow=True,
        diagnostics=diagnostics,
        history_removable_units=2,
        raw_history_turn_ids=[1, 2, 3],
    )

    assert fitted == expected
    assert messages == original
    assert diagnostics == {
        "omitted_blocks": [
            {"block_id": "party_lore_cards", "reason": "hard_input_budget"}
        ],
        "raw_history_turn_ids": [3],
    }


def test_revision_eight_lore_block_keeps_whole_cards_inside_four_thousand_chars() -> None:
    cards = [
        {
            "id": 1,
            "title": "Oversized",
            "content": "x" * 5_000,
            "keywords": ["oversized"],
            "source_turn_ids": [],
        },
        {
            "id": 2,
            "title": "Useful",
            "content": "A complete useful card.",
            "keywords": ["useful"],
            "source_turn_ids": [],
        },
    ]

    block = party_lore_cards_block(cards, max_chars=4_000)

    assert block is not None
    assert len(block) <= 4_000
    assert "A complete useful card." in block
    assert "Oversized" not in block
    assert "x" * 100 not in block


def test_revision_eight_overflow_raises_instead_of_truncating_protected_units() -> None:
    messages = [
        {"role": "system", "content": "WORLD_SYSTEM_PROMPT\nmandatory"},
        {"role": "user", "content": "protected action"},
        {"role": "assistant", "content": "protected consequence"},
        {"role": "user", "content": "current action"},
    ]
    full_tokens = estimate_tokens("\n".join(message["content"] for message in messages))

    with pytest.raises(PromptBudgetExceeded):
        fit_messages_to_context(
            messages,
            full_tokens - 1,
            protect_history=True,
            fail_on_token_overflow=True,
            history_removable_units=0,
            raw_history_turn_ids=[1],
        )
