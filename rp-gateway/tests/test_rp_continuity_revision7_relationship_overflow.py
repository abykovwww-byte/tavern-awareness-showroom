from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage
from app.services.adjudicator import Adjudicator
from app.services.narrative import PromptBudgetExceeded
from app.services.state_store import StateStore


def test_revision_seven_terminal_overflow_does_not_seed_relationship_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack_root = Path(__file__).resolve().parents[2]
    worldpack = stack_root / "worldpacks" / "starosta"
    relationship_model = json.loads(
        (worldpack / "relationships" / "model.json").read_text(encoding="utf-8")
    )
    state_path = tmp_path / "starosta-state.json"
    state_path.write_text(
        (worldpack / "state-seed.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    settings = Settings(
        app_env="test",
        campaign_id="relationship-overflow",
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
        party_context_max_tokens=1_000,
        party_context_limit_tokens=1_000,
        party_context_min_history_tokens=1,
        party_context_completion_reserve_tokens=0,
        party_memory_retrieval_enabled=False,
        world_system_prompt="mandatory world rule " * 3_000,
    )
    store = StateStore(
        str(tmp_path / "relationship-overflow.db"),
        settings.campaign_id,
        str(state_path),
    )
    adjudicator = Adjudicator(settings, store, relationship_model=relationship_model)
    provider_calls = 0

    async def tracked_complete(*args: object, **kwargs: object) -> dict:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("terminal overflow must stop before the narrator provider")

    monkeypatch.setattr(adjudicator.narrative, "complete", tracked_complete)

    with pytest.raises(PromptBudgetExceeded):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Осматриваю обвал у яра")],
                ),
                authorization=None,
                idempotency_key="relationship-overflow",
                request_id="req-relationship-overflow",
            )
        )

    assert provider_calls == 0
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM turns WHERE campaign_id = ?",
            (store.campaign_id,),
        ).fetchone()[0] == 0
        for table in (
            "relationship_causes",
            "character_axis_state",
            "character_badges",
            "narrative_events",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE campaign_id = ?",  # noqa: S608 - fixed tables
                (store.campaign_id,),
            ).fetchone()[0] == 0
