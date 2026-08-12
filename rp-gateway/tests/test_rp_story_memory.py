from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome
from app.services.adjudicator import Adjudicator
from app.services.narrative import NarrativeClient
from app.services.rp_story_memory import RPStoryMemoryUpdater, STORY_MEMORY_SCHEMA
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
    novel = Settings(scenario_type="novel")

    assert rp.effective_party_history_token_budget == 71_920
    assert training.effective_party_history_token_budget == 81_920
    assert novel.effective_party_history_token_budget == 81_920


def test_story_memory_updater_is_rp_only_and_cumulative(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    record_turns(store, 4)
    rp_settings = Settings(
        scenario_type="rp",
        service_nvidia_api_base="mock://success",
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
            nvidia_api_base="mock://success",
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
        Settings(scenario_type="training", nvidia_api_base="mock://success")
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


@pytest.mark.parametrize(
    ("scenario_type", "expected_job_types"),
    [
        ("rp", {"memory", "rp_story_memory"}),
        ("training", {"memory"}),
        ("novel", {"memory"}),
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
