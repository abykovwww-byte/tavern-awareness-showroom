"""Long-party memory summarization helpers."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.services.context_budget import (
    oldest_turns_within_token_budget,
    split_turns_by_token_budget,
    turns_token_count,
)
from app.services.narrative import response_text
from app.services.service_model_client import ServiceModelClient, service_prompt_text
from app.services.service_models import service_model_settings
from app.services.state_store import StateStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummaryPlan:
    previous_memory: dict[str, Any] | None
    turns: list[dict[str, Any]]
    from_turn_id: int
    to_turn_id: int
    state_version: int
    model: str
    stats: dict[str, Any]


class MemorySummarizer:
    """Creates immutable episodic chapters once the raw history exceeds its budget."""

    def __init__(
        self,
        settings: Settings,
        store: StateStore,
    ):
        self.settings = settings
        self.store = store
        self.history_token_budget = settings.effective_party_history_token_budget
        self.summary_batch_token_budget = max(settings.memory_summary_batch_tokens, 1)

    def stats(self) -> dict[str, Any]:
        turns = self.store.turns_for_memory()
        latest = self.store.latest_memory_coverage()
        latest_to_turn_id = int(latest["to_turn_id"]) if latest else 0
        raw_source_turns = [turn for turn in turns if int(turn["id"]) > latest_to_turn_id]
        old_turns, raw_turns = split_turns_by_token_budget(raw_source_turns, self.history_token_budget)
        unsummarized = [turn for turn in old_turns if int(turn["id"]) > latest_to_turn_id]
        return {
            "total_turns": len(turns),
            "context_limit_tokens": self.settings.effective_party_context_limit_tokens,
            "history_token_budget": self.history_token_budget,
            "raw_history_tokens": turns_token_count(raw_turns),
            "raw_turns_kept": len(raw_turns),
            "summary_batch_token_budget": self.summary_batch_token_budget,
            "eligible_old_turns": len(old_turns),
            "unsummarized_old_turns": len(unsummarized),
            "unsummarized_old_tokens": turns_token_count(unsummarized),
            "latest_memory_id": latest["id"] if latest else None,
            "latest_memory_type": latest.get("memory_type", "legacy_cumulative") if latest else None,
            "latest_to_turn_id": latest_to_turn_id or None,
            "chapter_count": len(self.store.memory_chapters()),
            "auto_summary_pending": bool(unsummarized),
        }

    async def summarize(
        self,
        authorization: str | None,
        force: bool = False,
        fail_open: bool = True,
        request_id: str | None = None,
        history_token_budget: int | None = None,
    ) -> dict[str, Any]:
        plan, reason = self.build_plan(force=force, history_token_budget=history_token_budget)
        if plan is None:
            return {
                "generated": False,
                "reason": reason,
                "memory": self.store.latest_memory_coverage(),
                "stats": self.stats(),
            }
        try:
            summary = await self.generate(plan, authorization, request_id=request_id)
            memory = self.store.record_memory_chapter(
                from_turn_id=plan.from_turn_id,
                to_turn_id=plan.to_turn_id,
                state_version=plan.state_version,
                summary_text=summary["summary_text"],
                key_facts=summary["key_facts"],
                open_threads=summary["open_threads"],
                relationship_changes=summary["relationship_changes"],
                player_promises=summary["player_promises"],
                npc_obligations=summary["npc_obligations"],
                model=summary.get("model") or plan.model,
            )
            self.store.audit(
                "memory_chapter_generated",
                {
                    "chapter_id": memory["id"],
                    "from_turn_id": plan.from_turn_id,
                    "to_turn_id": plan.to_turn_id,
                    "state_version": plan.state_version,
                    "model": memory["model"],
                },
                request_id,
            )
            return {"generated": True, "reason": "generated", "memory": memory, "stats": self.stats()}
        except Exception as exc:  # noqa: BLE001 - auto memory must never break a turn
            logger.warning("memory_summary_failed campaign_id=%s error=%s", self.store.campaign_id, exc)
            self.store.audit(
                "memory_summary_failed",
                {
                    "from_turn_id": plan.from_turn_id,
                    "to_turn_id": plan.to_turn_id,
                    "state_version": plan.state_version,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                request_id,
            )
            if fail_open:
                return {
                    "generated": False,
                    "reason": "summary_failed",
                    "memory": self.store.latest_memory_coverage(),
                    "stats": self.stats(),
                    "error": type(exc).__name__,
                }
            if isinstance(exc, PermissionError):
                raise
            raise RuntimeError("Memory summary provider failed") from exc

    def build_plan(
        self,
        force: bool = False,
        history_token_budget: int | None = None,
    ) -> tuple[SummaryPlan | None, str]:
        turns = self.store.turns_for_memory()
        latest = self.store.latest_memory_coverage()
        budget = max(history_token_budget if history_token_budget is not None else self.history_token_budget, 0)
        old_turns, _ = split_turns_by_token_budget(turns, budget)
        if force:
            latest_to_turn_id = int(latest["to_turn_id"]) if latest else 0
            unsummarized = [turn for turn in turns if int(turn["id"]) > latest_to_turn_id]
        elif not old_turns:
            return None, "within_context_budget"
        else:
            latest_to_turn_id = int(latest["to_turn_id"]) if latest else 0
            unsummarized = [turn for turn in old_turns if int(turn["id"]) > latest_to_turn_id]
        if not unsummarized:
            return None, "no_new_turns_for_chapter" if force else "up_to_date"
        batch = oldest_turns_within_token_budget(unsummarized, self.summary_batch_token_budget)
        from_turn_id = int(batch[0]["id"])
        to_turn_id = int(batch[-1]["id"])
        stats = self.stats()
        return (
            SummaryPlan(
                previous_memory=None,
                turns=batch,
                from_turn_id=from_turn_id,
                to_turn_id=to_turn_id,
                state_version=self.store.current_version() or 1,
                model=self.memory_service_settings().narrative_model,
                stats=stats,
            ),
            "ready",
        )

    async def generate(
        self,
        plan: SummaryPlan,
        authorization: str | None,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        service_settings = self.memory_service_settings()
        if service_settings.llm_api_base.startswith("mock://"):
            return self.mock_summary(plan)
        payload = self.summary_payload(plan)
        attempts = self.model_attempts(plan.model, service_settings)
        client = ServiceModelClient(service_settings)
        for index, model in enumerate(attempts):
            payload["model"] = model
            started = time.perf_counter()
            try:
                completion = await client.complete(
                    role="memory_summary",
                    provider=service_settings.llm_provider,
                    model=model,
                    party_id=self.store.campaign_id,
                    turn_id=plan.to_turn_id,
                    request_id=request_id,
                    party_turn=plan.turns[-1].get("party_turn"),
                    attempt=index + 1,
                    prompt=service_prompt_text(payload),
                    payload=payload,
                )
                data = completion.data
                parsed = self.parse_summary(response_text(data))
                parsed["model"] = data.get("model") or model
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.info(
                    "memory_summary_success campaign_id=%s model=%s turns=%s-%s elapsed_ms=%s",
                    self.store.campaign_id,
                    parsed["model"],
                    plan.turns[0]["id"],
                    plan.turns[-1]["id"],
                    elapsed_ms,
                )
                return parsed
            except (httpx.TimeoutException, httpx.HTTPStatusError, RuntimeError):
                if index < len(attempts) - 1:
                    continue
                raise
        raise RuntimeError(f"No model attempts configured for {self.settings.llm_provider} memory summarization")

    def summary_payload(self, plan: SummaryPlan) -> dict[str, Any]:
        context = {
            "current_state_summary": self.state_summary(self.store.get_state()),
            "episode_turns": self.compact_turns(plan.turns),
            "requested_coverage": {
                "from_turn_id": plan.from_turn_id,
                "to_turn_id": plan.to_turn_id,
                "state_version": plan.state_version,
            },
        }
        return {
            "model": plan.model,
            "stream": False,
            "temperature": 0.2,
            "max_tokens": self.settings.party_memory_chapter_max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You write one immutable episodic chapter for a deterministic training session. Return strict JSON only. "
                        "Use only supplied current authoritative state and the supplied turn history. "
                        "Do not turn attempts, player claims, failed checks, or unresolved possibilities into confirmed facts. "
                        "summary_text is a detailed chronological history, not a state summary: preserve scene order, "
                        "player actions, meaningful NPC dialogue/reactions, discoveries, locations, possessions, tone, "
                        "and unresolved leads. Compress prose, but do not replace the history with only facts or a checklist. "
                        "Keep confirmed facts, unresolved threads, relationship changes, player promises, and NPC obligations distinct. "
                        "Do not mutate state or contradict AUTHORITATIVE world state."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Create one detailed chronological episode chapter with keys: summary_text, key_facts, open_threads, "
                        "relationship_changes, player_promises, npc_obligations.\n\n"
                        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
                    ),
                },
            ],
        }

    def memory_service_settings(self) -> Settings:
        return service_model_settings(self.settings)

    def model_attempts(self, primary_model: str, service_settings: Settings) -> list[str]:
        disabled = set(service_settings.llm_disabled_models)
        candidates = [primary_model, *service_settings.llm_fallback_models]
        attempts: list[str] = []
        for model in candidates:
            if not model or model in disabled or model in attempts:
                continue
            attempts.append(model)
        return attempts or [primary_model]

    def parse_summary(self, content: str) -> dict[str, Any]:
        cleaned = strip_code_fence(content.strip())
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = {"summary_text": content}
        if not isinstance(data, dict):
            data = {"summary_text": content}
        summary_text = str(data.get("summary_text") or data.get("summary") or content).strip()
        return {
            "summary_text": clip(summary_text, self.settings.party_memory_chapter_max_chars),
            "key_facts": as_list(data.get("key_facts")),
            "open_threads": as_list(data.get("open_threads")),
            "relationship_changes": as_list(data.get("relationship_changes")),
            "player_promises": as_list(data.get("player_promises")),
            "npc_obligations": as_list(data.get("npc_obligations")),
        }

    def mock_summary(self, plan: SummaryPlan) -> dict[str, Any]:
        new_lines = [
            (
                f"Turn {turn['id']}: player message={clip(turn['player_message'], 180)}; "
                f"narrator response={clip(turn['narrative_response'], 180)}"
            )
            for turn in plan.turns
        ]
        summary_parts = [
            f"Confirmed conversation memory for turns {plan.turns[0]['id']}-{plan.turns[-1]['id']}:\n"
            + "\n".join(new_lines)
        ]
        return {
            "summary_text": clip("\n\n".join(part for part in summary_parts if part), 6000),
            "key_facts": [
                {
                    "turn_id": turn["id"],
                    "fact": f"Turn {turn['id']} is recorded in gateway history; player message: {clip(turn['player_message'], 160)}",
                }
                for turn in plan.turns
            ],
            "open_threads": [],
            "relationship_changes": [],
            "player_promises": [],
            "npc_obligations": [],
            "model": plan.model,
        }

    def state_summary(self, state: dict[str, Any]) -> dict[str, Any]:
        completed_threads = state.get("completed_threads", [])
        if not isinstance(completed_threads, list):
            completed_threads = []
        return {
            "meta": {
                "campaign_id": state.get("meta", {}).get("campaign_id"),
                "turn": state.get("meta", {}).get("turn"),
                "state_version": state.get("meta", {}).get("state_version"),
            },
            "player": state.get("player", {}),
            "relationships": state.get("relationships", {}),
            "active_threads": state.get("active_threads", []),
            "completed_threads": completed_threads[-10:],
            "uncertain_facts": state.get("uncertain_facts", []),
        }

    def compact_turns(self, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "turn_id": turn["id"],
                "state_version": turn["state_version"],
                "player_message": clip(turn["player_message"], 1200),
                "narrative_response": clip(turn["narrative_response"], 1200),
            }
            for turn in turns
        ]

    def prompt_memory(self, memory: dict[str, Any] | None) -> dict[str, Any] | None:
        if not memory:
            return None
        return {
            "covered_turns": [memory["from_turn_id"], memory["to_turn_id"]],
            "state_version": memory["state_version"],
            "summary_text": memory["summary_text"],
            "key_facts": memory.get("key_facts", []),
            "open_threads": memory.get("open_threads", []),
            "relationship_changes": memory.get("relationship_changes", []),
            "player_promises": memory.get("player_promises", []),
            "npc_obligations": memory.get("npc_obligations", []),
        }


def strip_code_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content


def as_list(value: Any, limit: int = 40) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value[:limit]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def previous_list(memory: dict[str, Any] | None, key: str) -> list[Any]:
    if not memory:
        return []
    value = memory.get(key)
    return value if isinstance(value, list) else []


def clip(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
