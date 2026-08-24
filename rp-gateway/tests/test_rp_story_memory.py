from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome
from app.services.adjudicator import Adjudicator
from app.services.narrative import NarrativeClient
from app.services.rp_history import RP_MEMORY_SECTION_KEYS, story_memory_safe_coverage
from app.services.rp_story_memory import (
    RPStoryMemoryUpdater,
    STORY_FIELD_LIMITS,
    STORY_MEMORY_SCHEMA,
    STORY_MEMORY_SECTION_FIELDS,
    STORY_MEMORY_SECTIONS_MAX_TOKENS,
    apply_user_story_memory_corrections,
    completion_finish_reason,
    empty_sectioned_story_memory,
    empty_story_memory,
    normalize_sectioned_story_memory,
    normalize_story_memory,
    reconcile_story_memory,
    story_fact_id,
    validate_story_memory_corrections,
)
from app.services.service_model_client import ServiceCompletion, ServiceModelClient, service_prompt_text
from app.services.state_store import StateStore


def make_store(tmp_path: Path, campaign_id: str = "rp-story") -> StateStore:
    return StateStore(
        str(tmp_path / "state.db"),
        campaign_id,
        str(tmp_path / f"{campaign_id}.json"),
    )


def record_turns(store: StateStore, count: int, *, start: int = 1) -> None:
    for index in range(start, start + count):
        store.record_turn(
            f"turn-{index}",
            f"request-{index}",
            f"Игрок сделал действие {index}",
            f"Ведущий подтвердил последствие {index}",
            {},
            index,
        )


def story_document(label: str = "старый канон") -> dict[str, object]:
    return {
        "schema_version": STORY_MEMORY_SCHEMA,
        "canon": [label],
        "rules_and_abilities": ["Магия требует цены."],
        "inventory_and_assets": ["У героя серебряный ключ."],
        "characters": ["Мира доверяет герою, но скрывает страх."],
        "active_threads": ["Найти башню."],
        "resolved_threads": [],
        "unresolved_hooks": ["Кто поджёг архив?"],
        "current_situation": "Герой стоит у закрытых ворот.",
        "chronology": ["Герой получил ключ."],
    }


def empty_combined_sections() -> dict[str, dict[str, object]]:
    return {
        section_key: {
            field: None if field == "current_situation" else []
            for field in STORY_MEMORY_SECTION_FIELDS[section_key]
        }
        for section_key in RP_MEMORY_SECTION_KEYS
    }


def outcome() -> Outcome:
    return Outcome(
        check_id="rp-story-prompt",
        action_type="feasibility",
        actor="player",
        result="partial_success",
        roll=10,
        difficulty=10,
        modifiers={},
        final_score=10,
        consequences=["Ворота открылись с шумом."],
        authoritative_block="AUTHORITATIVE_OUTCOME: partial success.",
    )


def test_story_memory_reserve_applies_only_to_rp() -> None:
    rp = Settings(scenario_type="rp")
    training = Settings(scenario_type="training")

    assert rp.effective_party_history_token_budget == 71_920
    assert training.effective_party_history_token_budget == 81_920


def test_story_memory_updater_is_rp_only_and_cumulative(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record_turns(store, 4)
    rp_settings = Settings(
        scenario_type="rp",
        service_model_choice="or-qwen-3.5-flash",
        openrouter_api_base="mock://success",
        service_openrouter_api_key="test-service-key",
        local_llm_enabled=False,
        rp_story_memory_update_turns=4,
    )
    updater = RPStoryMemoryUpdater(rp_settings, store)

    first = asyncio.run(updater.update(None, fail_open=False))
    assert first["generated"] is True
    assert first["story_memory"]["revision"] == 1
    assert first["story_memory"]["from_turn_id"] == 1
    assert first["story_memory"]["to_turn_id"] == 4
    assert len(first["story_memory"]["memory"]["chronology"]) == 4
    assert first["story_memory"]["memory"]["current_situation"]["status"] == "active"
    assert first["story_memory"]["memory"]["current_situation"]["source_turn_ids"] == [4]

    record_turns(store, 1, start=5)
    second = asyncio.run(updater.update(None, force=True, fail_open=False))
    assert second["generated"] is True
    assert second["story_memory"]["revision"] == 2
    assert second["story_memory"]["from_turn_id"] == 1
    assert second["story_memory"]["to_turn_id"] == 5
    assert len(second["story_memory"]["memory"]["chronology"]) == 5

    training_plan, reason = RPStoryMemoryUpdater(Settings(scenario_type="training"), store).build_plan(force=True)
    assert training_plan is None
    assert reason == "not_rp"


def test_story_memory_prompt_block_and_order_are_rp_only(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record_turns(store, 1)
    snapshot = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=story_document(),
        model="service-model",
    )
    memory_chapter = {
        "from_turn_id": 1,
        "to_turn_id": 1,
        "state_version": 1,
        "summary_text": "Эпизодическая деталь.",
        "key_facts": [],
        "open_threads": [],
        "relationship_changes": [],
        "player_promises": [],
        "npc_obligations": [],
    }
    request = ChatCompletionRequest(
        model="mock",
        messages=[ChatMessage(role="user", content="Открываю ворота.")],
    )
    rp_messages = NarrativeClient(
        Settings(
            scenario_type="rp",
            world_system_prompt="WORLD RULE",
            world_authors_note="AUTHOR NOTE",
            llm_api_base="mock://success",
        )
    ).narrative_messages(
        request,
        store.get_state(),
        outcome(),
        repair_instruction=None,
        memory_summary=memory_chapter,
        rp_story_memory=snapshot,
    )
    contents = [message["content"] for message in rp_messages]
    story_index = next(index for index, content in enumerate(contents) if content.startswith("RP_STORY_MEMORY"))
    author_index = next(index for index, content in enumerate(contents) if content.startswith("WORLD_AUTHORS_NOTE"))
    chapter_index = next(index for index, content in enumerate(contents) if content.startswith("LONG_TERM_PARTY_MEMORY"))
    outcome_index = next(index for index, content in enumerate(contents) if content.startswith("AUTHORITATIVE_OUTCOME"))
    assert author_index < story_index < chapter_index < outcome_index
    assert contents[-1] == "Открываю ворота."

    training_messages = NarrativeClient(
        Settings(scenario_type="training", llm_api_base="mock://success")
    ).narrative_messages(
        request,
        store.get_state(),
        outcome(),
        repair_instruction=None,
        memory_summary=memory_chapter,
        rp_story_memory=snapshot,
    )
    assert not any(message["content"].startswith("RP_STORY_MEMORY") for message in training_messages)
    assert any(message["content"].startswith("LONG_TERM_PARTY_MEMORY") for message in training_messages)


def test_story_memory_prompt_projects_only_active_facts(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    memory = story_document()
    memory["rules_and_abilities"] = [
        {
            "text": "Сила не действует на живую материю.",
            "status": "retracted",
            "authority": "inference",
            "source_turn_ids": [42],
        },
        {
            "text": "Сила действует на живую материю.",
            "status": "active",
            "authority": "user_correction",
            "source_turn_ids": [43],
        },
    ]
    memory["current_situation"] = {
        "text": "Старая ситуация.",
        "status": "retracted",
        "authority": "inference",
        "source_turn_ids": [42],
    }
    snapshot = store.record_rp_story_memory(
        from_turn_id=42,
        to_turn_id=43,
        state_version=1,
        memory=memory,
        model="service-model",
    )

    messages = NarrativeClient(
        Settings(scenario_type="rp", rp_contract_version="rp-core.v2")
    ).narrative_messages(
        ChatCompletionRequest(messages=[ChatMessage(role="user", content="Продолжаю.")]),
        store.get_state(),
        outcome(),
        repair_instruction=None,
        rp_story_memory=snapshot,
    )
    prompt = "\n".join(message["content"] for message in messages)

    assert "Сила действует на живую материю." in prompt
    assert "Сила не действует на живую материю." not in prompt
    assert "Старая ситуация." not in prompt


def test_revision_two_prompt_excludes_unmigrated_legacy_projection(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    memory = story_document("legacy false memory")
    memory["rules_and_abilities"] = [
        {
            "fact_id": "fact:explicit-inference",
            "text": "Explicit v2 inference remains available.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [1],
        }
    ]
    snapshot = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=memory,
        model="service-model",
    )
    request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="Continue.")])

    legacy_prompt = "\n".join(
        message["content"]
        for message in NarrativeClient(
            Settings(scenario_type="rp", rp_contract_revision=1)
        ).narrative_messages(
            request,
            store.get_state(),
            outcome(),
            repair_instruction=None,
            rp_story_memory=snapshot,
        )
    )
    revision_two_prompt = "\n".join(
        message["content"]
        for message in NarrativeClient(
            Settings(scenario_type="rp", rp_contract_revision=2)
        ).narrative_messages(
            request,
            store.get_state(),
            outcome(),
            repair_instruction=None,
            rp_story_memory=snapshot,
        )
    )

    assert "legacy false memory" in legacy_prompt
    assert "legacy false memory" not in revision_two_prompt
    assert "Explicit v2 inference remains available." in revision_two_prompt


def test_revision_two_update_cannot_promote_legacy_paraphrase(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    record_turns(store, 2)
    previous = empty_story_memory()
    legacy = {
        "fact_id": "fact:legacy-false-memory",
        "text": "The sealed gate is permanently closed.",
        "status": "active",
        "authority": "legacy_projection",
        "source_turn_ids": [1],
    }
    previous["canon"] = [legacy]
    store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=previous,
        model="legacy-service-model",
    )
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=2),
        store,
    )

    async def paraphrased_generate(plan: object, **kwargs: object) -> dict[str, object]:
        payload = updater.update_payload(plan)  # type: ignore[arg-type]
        assert legacy["text"] not in json.dumps(payload, ensure_ascii=False)
        proposed = empty_story_memory()
        proposed["canon"] = [
            {
                "fact_id": legacy["fact_id"],
                "text": "The gate can never be opened.",
                "status": "active",
                "authority": "inference",
                "source_turn_ids": [2],
            }
        ]
        return {"memory": proposed, "model": "service-model"}

    monkeypatch.setattr(updater, "generate", paraphrased_generate)
    result = asyncio.run(updater.update(None, force=True, fail_open=False))

    assert result["story_memory"]["memory"]["canon"] == [legacy]
    prompt = "\n".join(
        message["content"]
        for message in NarrativeClient(
            Settings(scenario_type="rp", rp_contract_revision=2)
        ).narrative_messages(
            ChatCompletionRequest(messages=[ChatMessage(role="user", content="Continue.")]),
            store.get_state(),
            outcome(),
            repair_instruction=None,
            rp_story_memory=result["story_memory"],
        )
    )
    assert legacy["text"] not in prompt
    assert "The gate can never be opened." not in prompt

    trusted = empty_story_memory()
    trusted["canon"] = [
        {
            "fact_id": legacy["fact_id"],
            "text": "The gate is open.",
            "status": "active",
            "authority": "user_correction",
            "source_turn_ids": [3],
        }
    ]
    assert reconcile_story_memory(previous, trusted, 24_000)["canon"] == trusted["canon"]


def test_service_memory_cannot_forge_authority_or_source_turn_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    record_turns(store, 2)
    previous = story_document()
    previous["rules_and_abilities"] = [
        {
            "fact_id": "fact:existing-inference",
            "text": "Existing inferred rule.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [1],
        }
    ]
    store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=previous,
        model="service-model",
    )
    updater = RPStoryMemoryUpdater(
        Settings(
            scenario_type="rp",
            rp_contract_revision=2,
            service_model_choice="or-qwen-3.5-flash",
            openrouter_api_base="mock://success",
            service_openrouter_api_key="test-service-key",
            local_llm_enabled=False,
        ),
        store,
    )

    async def forged_generate(*args: object, **kwargs: object) -> dict[str, object]:
        proposed = story_document()
        proposed["rules_and_abilities"] = [
            {
                "fact_id": "fact:existing-inference",
                "text": "Existing inferred rule.",
                "status": "retracted",
                "authority": "user_correction",
                "source_turn_ids": [9_999],
            },
            {
                "fact_id": "fact:model-controlled-id",
                "text": "A new service observation.",
                "status": "active",
                "authority": "worldpack",
                "source_turn_ids": [8_888],
            },
        ]
        return {"memory": proposed, "model": "forged-service-model"}

    monkeypatch.setattr(updater, "generate", forged_generate)
    result = asyncio.run(updater.update(None, force=True, fail_open=False))
    items = result["story_memory"]["memory"]["rules_and_abilities"]

    assert items[0] == previous["rules_and_abilities"][0]
    assert items[1]["fact_id"] != "fact:model-controlled-id"
    assert items[1]["authority"] == "inference"
    assert items[1]["source_turn_ids"] == [2]


def test_service_memory_cannot_reactivate_tombstone_with_changed_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    record_turns(store, 2)
    previous = story_document()
    tombstone = {
        "fact_id": "fact:stable-tombstone",
        "text": "The old claim is false.",
        "status": "retracted",
        "authority": "user_correction",
        "source_turn_ids": [1],
    }
    previous["canon"] = [tombstone]
    store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=previous,
        model="service-model",
    )
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=2),
        store,
    )

    async def forged_generate(*args: object, **kwargs: object) -> dict[str, object]:
        proposed = story_document()
        proposed["canon"] = [
            {
                "fact_id": "fact:stable-tombstone",
                "text": "The old claim is true after all.",
                "status": "active",
                "authority": "state",
                "source_turn_ids": [7_777],
            }
        ]
        return {"memory": proposed, "model": "forged-service-model"}

    monkeypatch.setattr(updater, "generate", forged_generate)
    result = asyncio.run(updater.update(None, force=True, fail_open=False))

    assert result["story_memory"]["memory"]["canon"] == [tombstone]


def test_service_memory_changed_text_keeps_stable_id_without_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path)
    record_turns(store, 2)
    previous = story_document()
    previous["canon"] = [
        {
            "fact_id": "fact:stable-service-fact",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [1],
        }
    ]
    store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        memory=previous,
        model="service-model",
    )
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=2),
        store,
    )

    async def changed_generate(*args: object, **kwargs: object) -> dict[str, object]:
        proposed = story_document()
        proposed["canon"] = [
            {
                "fact_id": "fact:stable-service-fact",
                "text": "The gate is now open.",
                "status": "active",
                "authority": "narrator",
                "source_turn_ids": [6_666],
            }
        ]
        return {"memory": proposed, "model": "service-model"}

    monkeypatch.setattr(updater, "generate", changed_generate)
    result = asyncio.run(updater.update(None, force=True, fail_open=False))
    items = result["story_memory"]["memory"]["canon"]

    assert items == [
        {
            "fact_id": "fact:stable-service-fact",
            "text": "The gate is now open.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [2],
        }
    ]


def test_typed_story_memory_replace_uses_committed_turn_id() -> None:
    previous = story_document()
    previous["canon"] = [
        {
            "fact_id": "fact:incorrect-gate",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [4],
        }
    ]
    turns = [
        {
            "id": 17,
            "metadata": {
                "story_memory_corrections": [
                    {
                        "field": "canon",
                        "fact_id": "fact:incorrect-gate",
                        "action": "replace",
                        "replacement_text": "The gate is open.",
                    }
                ]
            },
        }
    ]

    corrected = apply_user_story_memory_corrections(previous, turns, 24_000)

    assert corrected["canon"][0] == {
        "fact_id": "fact:incorrect-gate",
        "text": "The gate is closed.",
        "status": "superseded",
        "authority": "user_correction",
        "source_turn_ids": [17],
    }
    assert corrected["canon"][1]["fact_id"] != "fact:incorrect-gate"
    assert corrected["canon"][1]["text"] == "The gate is open."
    assert corrected["canon"][1]["status"] == "active"
    assert corrected["canon"][1]["authority"] == "user_correction"
    assert corrected["canon"][1]["source_turn_ids"] == [17]


def test_typed_replace_at_field_cap_survives_current_prompt_and_persistence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, campaign_id="capped-replace")
    previous = empty_story_memory()
    previous["canon"] = [
        {
            "fact_id": "fact:capped-target",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [0],
        },
        *[
            {
                "fact_id": f"fact:capped-filler-{index:02d}",
                "text": f"Filler fact {index}.",
                "status": "active",
                "authority": "inference",
                "source_turn_ids": [0],
            }
            for index in range(1, STORY_FIELD_LIMITS["canon"])
        ],
    ]
    store.record_rp_story_memory(
        from_turn_id=0,
        to_turn_id=0,
        state_version=1,
        memory=previous,
        model="service-model",
    )
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=2),
        store,
    )
    correction = {
        "field": "canon",
        "fact_id": "fact:capped-target",
        "action": "replace",
        "replacement_text": "The gate is open.",
    }

    transient = updater.prompt_snapshot([correction])

    assert transient is not None
    assert len(transient["memory"]["canon"]) == STORY_FIELD_LIMITS["canon"]
    transient_facts = {item["fact_id"]: item for item in transient["memory"]["canon"]}
    replacement_id = story_fact_id(None, correction["replacement_text"])
    assert transient_facts["fact:capped-target"]["status"] == "superseded"
    assert transient_facts[replacement_id]["status"] == "active"
    prompt = "\n".join(
        message["content"]
        for message in NarrativeClient(
            Settings(scenario_type="rp", rp_contract_revision=2)
        ).narrative_messages(
            ChatCompletionRequest(messages=[ChatMessage(role="user", content="Continue.")]),
            store.get_state(),
            outcome(),
            repair_instruction=None,
            rp_story_memory=transient,
        )
    )
    assert "The gate is open." in prompt
    assert "The gate is closed." not in prompt

    turn_id = store.record_turn(
        "capped-replace",
        "capped-replace-request",
        "Apply the correction.",
        "Continuing.",
        {},
        1,
        metadata={"story_memory_corrections": [correction]},
    )

    async def unchanged_generate(plan: object, **kwargs: object) -> dict[str, object]:
        return {
            "memory": plan.previous_memory["memory"],  # type: ignore[attr-defined,index]
            "model": "service-model",
        }

    monkeypatch.setattr(updater, "generate", unchanged_generate)
    persisted = asyncio.run(updater.update(None, force=True, fail_open=False))["story_memory"]

    assert len(persisted["memory"]["canon"]) == STORY_FIELD_LIMITS["canon"]
    persisted_facts = {item["fact_id"]: item for item in persisted["memory"]["canon"]}
    assert persisted_facts["fact:capped-target"]["status"] == "superseded"
    assert persisted_facts["fact:capped-target"]["source_turn_ids"] == [turn_id]
    assert persisted_facts[replacement_id]["status"] == "active"
    assert persisted_facts[replacement_id]["source_turn_ids"] == [turn_id]


def test_typed_replace_at_field_cap_rejects_when_other_entries_are_state_or_worldpack() -> None:
    previous = empty_story_memory()
    previous["canon"] = [
        {
            "fact_id": "fact:protected-target",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [0],
        },
        *[
            {
                "fact_id": f"fact:protected-{index:02d}",
                "text": f"Protected fact {index}.",
                "status": "active",
                "authority": "state" if index % 2 else "worldpack",
                "source_turn_ids": [index],
            }
            for index in range(1, STORY_FIELD_LIMITS["canon"])
        ],
    ]

    with pytest.raises(ValueError, match="field is full and has no safely removable weak entry"):
        validate_story_memory_corrections(
            {"memory": previous},
            [
                {
                    "field": "canon",
                    "fact_id": "fact:protected-target",
                    "action": "replace",
                    "replacement_text": "The gate is open.",
                }
            ],
            24_000,
        )


def test_preflight_rejects_over_max_chars_before_provider_or_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, campaign_id="protected-max-chars")
    previous = empty_story_memory()
    previous["canon"] = [
        {
            "fact_id": "fact:protected-size-target",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "user_correction",
            "source_turn_ids": [1],
        },
        *[
            {
                "fact_id": f"fact:protected-size-{index:02d}",
                "text": f"Protected correction {index}: " + ("x" * 400),
                "status": "active",
                "authority": "user_correction",
                "source_turn_ids": [index + 1],
            }
            for index in range(3)
        ],
    ]
    previous = normalize_story_memory(previous, 100_000)
    max_chars = len(json.dumps(previous, ensure_ascii=False))
    store.record_rp_story_memory(
        from_turn_id=0,
        to_turn_id=0,
        state_version=1,
        memory=previous,
        model="service-model",
    )
    adjudicator = Adjudicator(
        Settings(
            scenario_type="rp",
            rp_contract_revision=2,
            rp_story_memory_max_chars=max_chars,
        ),
        store,
    )
    provider_calls = 0

    async def unexpected_provider(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider must not run for an oversized correction")

    monkeypatch.setattr(adjudicator.narrative, "complete", unexpected_provider)
    state_version = store.current_version()

    with pytest.raises(ValueError, match="exceeds max_chars capacity"):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    messages=[ChatMessage(role="user", content="Apply this correction.")]
                ),
                None,
                "oversized-correction",
                story_memory_corrections=[
                    {
                        "field": "canon",
                        "fact_id": "fact:protected-size-target",
                        "action": "replace",
                        "replacement_text": "The gate is open.",
                    }
                ],
            )
        )

    assert provider_calls == 0
    assert store.current_version() == state_version
    assert store.turn_history(limit=10) == []


def test_typed_story_memory_correction_rejects_unknown_fact() -> None:
    with pytest.raises(ValueError, match="active story-memory fact not found"):
        validate_story_memory_corrections(
            {"memory": story_document()},
            [
                {
                    "field": "canon",
                    "fact_id": "fact:not-present",
                    "action": "retract",
                }
            ],
            24_000,
        )


def test_prompt_snapshot_applies_pending_retract_without_persisting_it(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    memory = story_document()
    memory["canon"] = [
        {
            "fact_id": "fact:incorrect-gate",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [0],
        }
    ]
    store.record_rp_story_memory(
        from_turn_id=0,
        to_turn_id=0,
        state_version=1,
        memory=memory,
        model="service-model",
    )
    turn_id = store.record_turn(
        "pending-correction",
        "pending-correction-request",
        "Apply the correction.",
        "Continuing.",
        {},
        1,
        metadata={
            "story_memory_corrections": [
                {
                    "field": "canon",
                    "fact_id": "fact:incorrect-gate",
                    "action": "retract",
                }
            ]
        },
    )
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=2),
        store,
    )

    projected = updater.prompt_snapshot()

    assert projected is not None
    assert projected["to_turn_id"] == 0
    assert projected["memory"]["canon"] == [
        {
            "fact_id": "fact:incorrect-gate",
            "text": "The gate is closed.",
            "status": "retracted",
            "authority": "user_correction",
            "source_turn_ids": [turn_id],
        }
    ]
    persisted = store.latest_rp_story_memory()
    assert persisted is not None
    assert persisted["memory"]["canon"][0]["status"] == "active"
    with pytest.raises(ValueError, match="active story-memory fact not found"):
        updater.validate_corrections(
            [
                {
                    "field": "canon",
                    "fact_id": "fact:incorrect-gate",
                    "action": "retract",
                }
            ]
        )


@pytest.mark.parametrize(
    ("action", "replacement_text", "expected_status"),
    [
        ("retract", None, "retracted"),
        ("replace", "The corrected gate is open.", "superseded"),
    ],
)
def test_near_budget_update_retains_typed_correction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    replacement_text: str | None,
    expected_status: str,
) -> None:
    store = make_store(tmp_path, campaign_id=f"bounded-{action}")
    previous = empty_story_memory()
    previous["canon"] = [
        {
            "fact_id": f"fact:filler-{index:02d}",
            "text": f"Weak filler {index}: " + ("x" * 400),
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [0],
        }
        for index in range(6)
    ]
    previous["canon"].append(
        {
            "fact_id": "fact:bounded-target",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [0],
        }
    )
    previous = normalize_story_memory(previous, 100_000)
    max_chars = len(json.dumps(previous, ensure_ascii=False))
    store.record_rp_story_memory(
        from_turn_id=0,
        to_turn_id=0,
        state_version=1,
        memory=previous,
        model="service-model",
    )
    correction = {
        "field": "canon",
        "fact_id": "fact:bounded-target",
        "action": action,
    }
    if replacement_text is not None:
        correction["replacement_text"] = replacement_text
    turn_id = store.record_turn(
        f"bounded-{action}",
        f"bounded-{action}-request",
        "Apply the correction.",
        "Continuing.",
        {},
        1,
        metadata={"story_memory_corrections": [correction]},
    )
    updater = RPStoryMemoryUpdater(
        Settings(
            scenario_type="rp",
            rp_contract_revision=2,
            rp_story_memory_max_chars=max_chars,
        ),
        store,
    )

    async def unchanged_generate(plan: object, **kwargs: object) -> dict[str, object]:
        return {
            "memory": plan.previous_memory["memory"],  # type: ignore[attr-defined,index]
            "model": "service-model",
        }

    monkeypatch.setattr(updater, "generate", unchanged_generate)
    result = asyncio.run(updater.update(None, force=True, fail_open=False))

    persisted = store.latest_rp_story_memory()
    effective = updater.prompt_snapshot()
    assert persisted is not None
    assert effective is not None
    assert result["story_memory"]["memory"] == persisted["memory"] == effective["memory"]
    assert len(json.dumps(persisted["memory"], ensure_ascii=False)) <= max_chars
    facts = {item["fact_id"]: item for item in persisted["memory"]["canon"]}
    assert facts["fact:bounded-target"] == {
        "fact_id": "fact:bounded-target",
        "text": "The gate is closed.",
        "status": expected_status,
        "authority": "user_correction",
        "source_turn_ids": [turn_id],
    }
    if replacement_text is not None:
        replacement_id = story_fact_id(None, replacement_text)
        assert facts[replacement_id] == {
            "fact_id": replacement_id,
            "text": replacement_text,
            "status": "active",
            "authority": "user_correction",
            "source_turn_ids": [turn_id],
        }


def test_rollback_excludes_corrected_snapshot_from_effective_memory_and_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, campaign_id="rollback-correction")
    previous = empty_story_memory()
    previous["canon"] = [
        {
            "fact_id": "fact:rollback-gate",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [0],
        }
    ]
    seed_snapshot = store.record_rp_story_memory(
        from_turn_id=0,
        to_turn_id=0,
        state_version=1,
        memory=previous,
        model="service-model",
    )
    correction = {
        "field": "canon",
        "fact_id": "fact:rollback-gate",
        "action": "replace",
        "replacement_text": "The gate is open.",
    }
    correction_turn_id = store.record_turn(
        "rollback-correction",
        "rollback-correction-request",
        "Apply the correction.",
        "Continuing.",
        {},
        2,
        metadata={"story_memory_corrections": [correction]},
    )
    corrected_memory = apply_user_story_memory_corrections(
        previous,
        [{"id": correction_turn_id, "metadata": {"story_memory_corrections": [correction]}}],
        24_000,
    )
    corrected_snapshot = store.record_rp_story_memory(
        from_turn_id=0,
        to_turn_id=correction_turn_id,
        state_version=2,
        memory=corrected_memory,
        model="service-model",
    )
    state_v2 = store.get_state()
    state_v2["meta"]["state_version"] = 2
    state_v2["meta"]["turn"] = 1
    store.insert_state_version(state_v2, "corrected turn")
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=2),
        store,
    )
    assert updater.prompt_snapshot()["id"] == corrected_snapshot["id"]  # type: ignore[index]

    store.rollback(target_version=1)

    raw_latest = store.latest_rp_story_memory()
    effective_after_rollback = updater.prompt_snapshot()
    assert raw_latest is not None
    assert raw_latest["id"] == corrected_snapshot["id"]
    assert raw_latest["invalidated"] is True
    assert effective_after_rollback is not None
    assert effective_after_rollback["id"] == seed_snapshot["id"]
    assert effective_after_rollback["memory"]["canon"][0]["text"] == "The gate is closed."
    assert not any(
        item.get("text") == "The gate is open."
        for item in effective_after_rollback["memory"]["canon"]
    )

    later_turn_id = store.record_turn(
        "after-rollback",
        "after-rollback-request",
        "Continue from the restored state.",
        "Continuing.",
        {},
        store.current_version() or 3,
    )
    planned_turn_ids: list[int] = []

    async def unchanged_generate(plan: object, **kwargs: object) -> dict[str, object]:
        planned_turn_ids.extend(turn["id"] for turn in plan.turns)  # type: ignore[attr-defined]
        return {
            "memory": plan.previous_memory["memory"],  # type: ignore[attr-defined,index]
            "model": "service-model",
        }

    monkeypatch.setattr(updater, "generate", unchanged_generate)
    rebuilt = asyncio.run(updater.update(None, force=True, fail_open=False))["story_memory"]

    assert planned_turn_ids == [later_turn_id]
    assert rebuilt["to_turn_id"] == later_turn_id
    assert rebuilt["invalidated"] is False
    assert rebuilt["memory"]["canon"][0]["text"] == "The gate is closed."
    assert not any(item.get("text") == "The gate is open." for item in rebuilt["memory"]["canon"])
    snapshots = store.rp_story_memories(limit=10)
    assert any(snapshot["id"] == corrected_snapshot["id"] for snapshot in snapshots)
    assert updater.prompt_snapshot()["id"] == rebuilt["id"]  # type: ignore[index]


def test_rollback_while_story_generation_is_paused_discards_stale_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, campaign_id="rollback-race")
    previous = empty_story_memory()
    previous["canon"] = [
        {
            "fact_id": "fact:race-gate",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [0],
        }
    ]
    seed_snapshot = store.record_rp_story_memory(
        from_turn_id=0,
        to_turn_id=0,
        state_version=1,
        memory=previous,
        model="service-model",
    )
    assert seed_snapshot is not None
    correction = {
        "field": "canon",
        "fact_id": "fact:race-gate",
        "action": "replace",
        "replacement_text": "The gate is open.",
    }
    turn_id = store.record_turn(
        "rollback-race",
        "rollback-race-request",
        "Apply the correction.",
        "Continuing.",
        {},
        2,
        metadata={"story_memory_corrections": [correction]},
    )
    state_v2 = store.get_state()
    state_v2["meta"]["state_version"] = 2
    state_v2["meta"]["turn"] = 1
    store.insert_state_version(state_v2, "race turn")
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=2),
        store,
    )
    generation_started = asyncio.Event()
    resume_generation = asyncio.Event()

    async def paused_generate(plan: object, **kwargs: object) -> dict[str, object]:
        assert [turn["id"] for turn in plan.turns] == [turn_id]  # type: ignore[attr-defined]
        generation_started.set()
        await resume_generation.wait()
        return {
            "memory": plan.previous_memory["memory"],  # type: ignore[attr-defined,index]
            "model": "service-model",
        }

    monkeypatch.setattr(updater, "generate", paused_generate)

    async def race() -> dict[str, object]:
        update_task = asyncio.create_task(updater.update(None, force=True))
        await generation_started.wait()
        store.rollback(target_version=1)
        resume_generation.set()
        return await update_task

    result = asyncio.run(race())

    assert result["generated"] is False
    assert result["reason"] == "stale_plan"
    assert result["error"] == "stale_plan"
    assert [snapshot["id"] for snapshot in store.rp_story_memories(limit=10)] == [seed_snapshot["id"]]
    effective = updater.prompt_snapshot()
    assert effective is not None
    assert effective["id"] == seed_snapshot["id"]
    assert effective["memory"]["canon"][0]["text"] == "The gate is closed."
    assert asyncio.run(updater.update(None, force=True))["reason"] == "up_to_date"


def test_story_memory_retracted_fact_cannot_be_resurrected_by_later_summary() -> None:
    fact_id = "fact:absolute-power-living"
    previous = story_document()
    previous["rules_and_abilities"] = [
        {
            "fact_id": fact_id,
            "text": "Сила не действует на живую материю.",
            "status": "retracted",
            "authority": "user_correction",
            "source_turn_ids": [43],
        }
    ]
    proposed = story_document()
    proposed["rules_and_abilities"] = [
        {
            "fact_id": fact_id,
            "text": "Сила не действует на живую материю.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [44],
        }
    ]

    merged = reconcile_story_memory(previous, proposed, 24_000)

    assert merged["rules_and_abilities"] == [
        {
            "fact_id": fact_id,
            "text": "Сила не действует на живую материю.",
            "status": "retracted",
            "authority": "user_correction",
            "source_turn_ids": [43],
        }
    ]


def test_story_memory_user_correction_retracts_existing_fact_by_stable_id() -> None:
    fact_id = "fact:absolute-power-living"
    previous = story_document()
    previous["rules_and_abilities"] = [
        {
            "fact_id": fact_id,
            "text": "Сила не действует на живую материю.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [42],
        }
    ]
    proposed = story_document()
    proposed["rules_and_abilities"] = [
        {
            "fact_id": fact_id,
            "text": "Сила не действует на живую материю.",
            "status": "retracted",
            "authority": "user_correction",
            "source_turn_ids": [43],
        },
        {
            "fact_id": "fact:absolute-power-corrected",
            "text": "Сила действует на живую материю.",
            "status": "active",
            "authority": "user_correction",
            "source_turn_ids": [43],
        },
    ]

    merged = reconcile_story_memory(previous, proposed, 24_000)

    assert [item["status"] for item in merged["rules_and_abilities"]] == ["retracted", "active"]
    assert [item["source_turn_ids"] for item in merged["rules_and_abilities"]] == [[43], [43]]


def test_revision8_waits_through_fifty_then_updates_all_sections_in_one_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, campaign_id="rev8-first-batch")
    settings = Settings(scenario_type="rp", rp_contract_revision=8)
    updater = RPStoryMemoryUpdater(settings, store)
    record_turns(store, 50)

    assert updater.should_enqueue() is False
    waiting = asyncio.run(updater.update(None, request_id="request-50"))
    assert waiting["generated"] is False
    assert waiting["reason"] == "waiting_for_raw_window"

    record_turns(store, 1, start=51)
    combined_calls: list[dict[str, list[int]]] = []
    targeted_calls: list[str] = []

    async def generated_sections(
        plan: object,
        section_turns: dict[str, list[dict[str, object]]],
        memory: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        combined_calls.append(
            {
                section_key: [int(turn["id"]) for turn in section_turns[section_key]]
                for section_key in RP_MEMORY_SECTION_KEYS
            }
        )
        return {
            "sections": empty_combined_sections(),
            "errors": {},
            "model": settings.rp_story_memory_model,
            "turns": section_turns,
        }

    async def unexpected_targeted_retry(*args: object, **kwargs: object) -> dict[str, object]:
        targeted_calls.append(str(args[1]))
        raise AssertionError("valid empty sections must not be retried")

    monkeypatch.setattr(updater, "generate_sections", generated_sections)
    monkeypatch.setattr(updater, "generate_section", unexpected_targeted_retry)
    result = asyncio.run(updater.update(None, request_id="request-51", fail_open=False))

    assert result["generated"] is True
    assert len(combined_calls) == 1
    assert all(
        combined_calls[0][section_key] == list(range(1, 9))
        for section_key in RP_MEMORY_SECTION_KEYS
    )
    assert targeted_calls == []
    snapshot = result["story_memory"]
    assert story_memory_safe_coverage(snapshot) == 8
    assert snapshot["memory"]["observed_through_turn_id"] == 51
    assert {
        key: snapshot["memory"]["section_status"][key]
        for key in RP_MEMORY_SECTION_KEYS
    } == {
        key: {"coverage": 8, "status": "fresh"}
        for key in RP_MEMORY_SECTION_KEYS
    }
    assert updater.should_enqueue() is False


def test_revision8_retry_updates_only_failed_sections_even_after_new_cadence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, campaign_id="rev8-partial-retry")
    settings = Settings(scenario_type="rp", rp_contract_revision=8)
    updater = RPStoryMemoryUpdater(settings, store)
    record_turns(store, 51)

    main_calls = 0
    first_targeted_calls: list[str] = []

    async def partial_sections(
        plan: object,
        section_turns: dict[str, list[dict[str, object]]],
        memory: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        nonlocal main_calls
        main_calls += 1
        sections = empty_combined_sections()
        sections.pop("characters")
        return {
            "sections": sections,
            "errors": {"characters": "forced structural failure"},
            "model": settings.rp_story_memory_model,
            "turns": section_turns,
        }

    async def failing_targeted_retry(
        plan: object,
        section_key: str,
        turns: list[dict[str, object]],
        memory: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        first_targeted_calls.append(section_key)
        raise ValueError("forced targeted failure")

    monkeypatch.setattr(updater, "generate_sections", partial_sections)
    monkeypatch.setattr(updater, "generate_section", failing_targeted_retry)
    first = asyncio.run(updater.update(None, request_id="same-job", fail_open=True))
    assert main_calls == 1
    assert first_targeted_calls == ["characters"]
    assert first["retry_required"] is True
    assert first["failed_sections"] == ["characters"]
    assert story_memory_safe_coverage(first["story_memory"]) == 0

    record_turns(store, 4, start=52)
    retry_calls: list[tuple[str, list[int]]] = []

    async def successful_retry(
        plan: object,
        section_key: str,
        turns: list[dict[str, object]],
        memory: dict[str, object],
        **kwargs: object,
    ) -> dict[str, object]:
        retry_calls.append((section_key, [int(turn["id"]) for turn in turns]))
        return {
            "section": updater.mock_section(section_key, turns, memory),
            "model": settings.rp_story_memory_model,
            "turns": turns,
        }

    async def unexpected_combined_retry(*args: object, **kwargs: object) -> dict[str, object]:
        raise AssertionError("durable retry must stay section-scoped")

    monkeypatch.setattr(updater, "generate_sections", unexpected_combined_retry)
    monkeypatch.setattr(updater, "generate_section", successful_retry)
    second = asyncio.run(updater.update(None, request_id="same-job", fail_open=False))

    assert retry_calls == [("characters", list(range(1, 9)))]
    assert second["retry_required"] is False
    assert story_memory_safe_coverage(second["story_memory"]) == 8
    assert all(
        second["story_memory"]["memory"]["section_status"][key]["status"] == "fresh"
        for key in RP_MEMORY_SECTION_KEYS
    )


def test_revision8_combined_structural_validation_accepts_empty_sections(
    tmp_path: Path,
) -> None:
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=8),
        make_store(tmp_path, campaign_id="rev8-empty-sections"),
    )

    sections, errors = updater.parse_sections(
        json.dumps(empty_combined_sections()),
        empty_sectioned_story_memory(),
    )

    assert errors == {}
    assert sections == empty_combined_sections()


def test_revision8_structural_validation_rejects_changed_existing_fact_id_only(
    tmp_path: Path,
) -> None:
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=8),
        make_store(tmp_path, campaign_id="rev8-fact-id"),
    )
    memory = empty_sectioned_story_memory()
    memory["characters"] = [
        {
            "fact_id": "fact:character-original",
            "text": "Мира хранит серебряный ключ.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [7],
        }
    ]
    response = empty_combined_sections()
    response["characters"]["characters"] = [
        {
            "fact_id": "fact:character-replaced",
            "text": "Мира хранит серебряный ключ.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [7],
        }
    ]

    sections, errors = updater.parse_sections(json.dumps(response), memory)

    assert set(sections) == set(RP_MEMORY_SECTION_KEYS) - {"characters"}
    assert errors == {"characters": "story-memory response changed an existing fact_id"}


def test_revision8_combined_length_finish_is_failure_for_every_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path, campaign_id="rev8-length")
    record_turns(store, 51)
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=8),
        store,
    )
    plan, reason = updater.build_section_plan(request_id="length-response")
    assert reason == "ready"
    assert plan is not None
    recorded: dict[str, object] = {}

    async def length_response(self: ServiceModelClient, **kwargs: object) -> ServiceCompletion:
        recorded.update(kwargs)
        return ServiceCompletion(
            data={
                "model": "deepseek/deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": json.dumps(empty_combined_sections())},
                    }
                ],
            },
            raw_response="{}",
            status="completed",
            status_code=200,
        )

    monkeypatch.setattr(ServiceModelClient, "complete", length_response)
    generated = asyncio.run(
        updater.generate_sections(
            plan,
            plan.section_turns,
            empty_sectioned_story_memory(),
            request_id="length-response",
        )
    )

    assert completion_finish_reason({"choices": [{"finish_reason": "length"}]}) == "length"
    assert generated["sections"] == {}
    assert set(generated["errors"]) == set(RP_MEMORY_SECTION_KEYS)
    assert recorded["section_key"] == "all"
    assert recorded["payload"]["max_tokens"] == STORY_MEMORY_SECTIONS_MAX_TOKENS


def test_story_memory_correction_replay_is_idempotent() -> None:
    memory = empty_story_memory()
    memory["canon"] = [
        {
            "fact_id": "fact:retry-target",
            "text": "The gate is closed.",
            "status": "active",
            "authority": "inference",
            "source_turn_ids": [1],
        }
    ]
    turn = {
        "id": 12,
        "metadata": {
            "story_memory_corrections": [
                {
                    "field": "canon",
                    "fact_id": "fact:retry-target",
                    "action": "replace",
                    "replacement_text": "The gate is open.",
                }
            ]
        },
    }

    first = apply_user_story_memory_corrections(
        memory,
        [turn],
        24_000,
        authority="user",
    )
    second = apply_user_story_memory_corrections(
        first,
        [turn],
        24_000,
        authority="user",
    )

    assert second == first
    assert [item["authority"] for item in second["canon"]] == ["user", "user"]


def test_revision8_sectioned_memory_normalizes_legacy_correction_authority() -> None:
    memory = empty_sectioned_story_memory()
    memory["canon"] = [
        {
            "fact_id": "fact:legacy-correction",
            "text": "The corrected gate is open.",
            "status": "active",
            "authority": "user_correction",
            "source_turn_ids": [12],
        }
    ]

    normalized = normalize_sectioned_story_memory(memory, 24_000)

    assert normalized["canon"][0]["authority"] == "user"
    assert "absorbed_correction_ids" not in normalized


def test_revision8_section_input_keeps_complete_turns_under_twenty_thousand_chars(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path, campaign_id="rev8-input-bound")
    for index in range(1, 52):
        long_suffix = ("x" * 1_800) if index <= 8 else "short"
        store.record_turn(
            f"turn-{index}",
            f"request-{index}",
            f"PLAYER-{index}-{long_suffix}",
            f"NARRATOR-{index}-{long_suffix}",
            {},
            index,
        )
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=8),
        store,
    )
    plan, reason = updater.build_section_plan(request_id="bounded-input")
    assert reason == "ready"
    assert plan is not None

    payload, included = updater.section_payload(
        plan,
        "situation",
        plan.section_turns["situation"],
        empty_story_memory(),
    )
    context = json.loads(payload["messages"][1]["content"])

    assert payload["model"] == "deepseek/deepseek-v4-pro"
    assert payload["max_tokens"] == 800
    assert len(service_prompt_text(payload)) <= 20_000
    assert 0 < len(included) < 8
    assert context["new_confirmed_turns"][-1]["narrator"] == included[-1]["narrative_response"]


def test_revision8_combined_input_keeps_complete_turns_under_twenty_thousand_chars(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path, campaign_id="rev8-combined-input-bound")
    for index in range(1, 52):
        long_suffix = ("x" * 1_800) if index <= 8 else "short"
        store.record_turn(
            f"turn-{index}",
            f"request-{index}",
            f"PLAYER-{index}-{long_suffix}",
            f"NARRATOR-{index}-{long_suffix}",
            {},
            index,
        )
    updater = RPStoryMemoryUpdater(
        Settings(scenario_type="rp", rp_contract_revision=8),
        store,
    )
    plan, reason = updater.build_section_plan(request_id="bounded-combined-input")
    assert reason == "ready"
    assert plan is not None

    payload, included = updater.sections_payload(
        plan,
        plan.section_turns,
        empty_sectioned_story_memory(),
    )
    context = json.loads(payload["messages"][1]["content"])

    assert payload["model"] == "deepseek/deepseek-v4-pro"
    assert payload["max_tokens"] == STORY_MEMORY_SECTIONS_MAX_TOKENS
    assert len(service_prompt_text(payload)) <= 20_000
    assert all(0 < len(included[key]) < 8 for key in RP_MEMORY_SECTION_KEYS)
    assert context["section_turn_ids"] == {
        key: [int(turn["id"]) for turn in included[key]]
        for key in RP_MEMORY_SECTION_KEYS
    }
    last_included_id = max(
        int(turn["id"])
        for turns in included.values()
        for turn in turns
    )
    rendered_last = next(
        turn for turn in context["new_confirmed_turns"] if turn["turn_id"] == last_included_id
    )
    source_last = store.get_turn_by_request_id(f"request-{last_included_id}")
    assert source_last is not None
    assert rendered_last["player"] == source_last["player_message"]
    assert rendered_last["narrator"] == source_last["narrative_response"]


@pytest.mark.parametrize(
    ("scenario_type", "expected_job_types"),
    [
        ("rp", {"memory", "rp_story_memory"}),
        ("training", {"memory"}),
    ],
)
def test_post_turn_story_job_is_enqueued_only_for_rp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario_type: str,
    expected_job_types: set[str],
) -> None:
    store = make_store(tmp_path, campaign_id=f"jobs-{scenario_type}")
    adjudicator = Adjudicator(Settings(scenario_type=scenario_type), store)
    monkeypatch.setattr(adjudicator, "schedule_service_jobs", lambda _authorization=None: None)

    asyncio.run(adjudicator.after_turn_recorded(None, f"request-{scenario_type}"))

    assert {job["job_type"] for job in store.service_jobs()} == expected_job_types


def test_story_memory_snapshot_follows_party_branch(tmp_path: Path) -> None:
    store = make_store(tmp_path, campaign_id="source")
    record_turns(store, 2)
    source_snapshot = store.record_rp_story_memory(
        from_turn_id=1,
        to_turn_id=2,
        state_version=1,
        memory=story_document("исходный канон"),
        model="service-model",
    )
    checkpoint = store.create_memory_checkpoint("before fork")

    store.fork_from_checkpoint(
        checkpoint_id=checkpoint["id"],
        target_campaign_id="branch",
        target_state_path=str(tmp_path / "branch.json"),
    )
    branch_store = StateStore(str(tmp_path / "state.db"), "branch", str(tmp_path / "branch.json"))
    branch_snapshot = branch_store.latest_rp_story_memory()

    assert branch_snapshot is not None
    assert branch_snapshot["campaign_id"] == "branch"
    assert branch_snapshot["revision"] == 1
    assert branch_snapshot["memory"] == source_snapshot["memory"]


def test_service_memory_state_excerpt_never_contains_character_secrets(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    state = store.get_state()
    state["characters"] = {
        "mira": {
            "name": "Мира",
            "status": "alive",
            "knowledge": ["Герой нашёл ключ."],
            "secrets": ["Мира подожгла архив."],
        }
    }
    excerpt = RPStoryMemoryUpdater(Settings(scenario_type="rp"), store).state_excerpt(state)

    assert "Герой нашёл ключ" in excerpt
    assert "Мира подожгла архив" not in excerpt
