"""Human-readable party journal recap helpers."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.services.memory import as_list, clip, strip_code_fence
from app.services.narrative import response_text
from app.services.state_store import StateStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JournalPlan:
    turns: list[dict[str, Any]]
    from_turn_id: int
    to_turn_id: int
    state_version: int
    model: str


class JournalBuilder:
    """Creates player-facing recaps from completed party turns."""

    def __init__(self, settings: Settings, store: StateStore, min_unsummarized_turns: int = 6, max_batch_turns: int = 18):
        self.settings = settings
        self.store = store
        self.min_unsummarized_turns = min_unsummarized_turns
        self.max_batch_turns = max_batch_turns

    def stats(self) -> dict[str, Any]:
        turns = self.store.turns_for_memory()
        latest = self.store.latest_journal_entry()
        latest_to_turn_id = int(latest["to_turn_id"]) if latest else 0
        unsummarized = [turn for turn in turns if int(turn["id"]) > latest_to_turn_id]
        return {
            "total_turns": len(turns),
            "journaled_turns": latest_to_turn_id,
            "unsummarized_turns": len(unsummarized),
            "latest_entry_id": latest["id"] if latest else None,
            "latest_to_turn_id": latest_to_turn_id or None,
            "next_auto_entry_turns_remaining": max(self.min_unsummarized_turns - len(unsummarized), 0),
        }

    async def summarize(
        self,
        authorization: str | None,
        force: bool = False,
        fail_open: bool = True,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        plan, reason = self.build_plan(force=force)
        if plan is None:
            return {
                "generated": False,
                "reason": reason,
                "journal": self.store.latest_journal_entry(),
                "entries": self.store.journal_entries(limit=10),
                "stats": self.stats(),
            }
        try:
            recap = await self.generate(plan, authorization)
            entry = self.store.record_journal_entry(
                from_turn_id=plan.from_turn_id,
                to_turn_id=plan.to_turn_id,
                state_version=plan.state_version,
                title=recap["title"],
                recap_text=recap["recap_text"],
                important_changes=recap["important_changes"],
                model=recap.get("model") or plan.model,
            )
            self.store.audit(
                "journal_entry_generated",
                {
                    "entry_id": entry["id"],
                    "from_turn_id": plan.from_turn_id,
                    "to_turn_id": plan.to_turn_id,
                    "state_version": plan.state_version,
                    "model": entry["model"],
                },
                request_id,
            )
            return {
                "generated": True,
                "reason": "generated",
                "journal": entry,
                "entries": self.store.journal_entries(limit=10),
                "stats": self.stats(),
            }
        except Exception as exc:  # noqa: BLE001 - journal must not break gameplay
            logger.warning("journal_entry_failed campaign_id=%s error=%s", self.store.campaign_id, exc)
            self.store.audit(
                "journal_entry_failed",
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
                    "reason": "journal_failed",
                    "journal": self.store.latest_journal_entry(),
                    "entries": self.store.journal_entries(limit=10),
                    "stats": self.stats(),
                    "error": type(exc).__name__,
                }
            if isinstance(exc, PermissionError):
                raise
            raise RuntimeError("Journal provider failed") from exc

    def build_plan(self, force: bool = False) -> tuple[JournalPlan | None, str]:
        turns = self.store.turns_for_memory()
        latest = self.store.latest_journal_entry()
        latest_to_turn_id = int(latest["to_turn_id"]) if latest else 0
        unsummarized = [turn for turn in turns if int(turn["id"]) > latest_to_turn_id]
        if not unsummarized:
            return None, "up_to_date"
        if not force and len(unsummarized) < self.min_unsummarized_turns:
            return None, "not_enough_unsummarized_turns"
        batch = unsummarized[: self.max_batch_turns]
        return (
            JournalPlan(
                turns=batch,
                from_turn_id=int(batch[0]["id"]),
                to_turn_id=int(batch[-1]["id"]),
                state_version=self.store.current_version() or 1,
                model=self.settings.narrative_model,
            ),
            "ready",
        )

    async def generate(self, plan: JournalPlan, authorization: str | None) -> dict[str, Any]:
        if self.settings.nvidia_api_base.startswith("mock://"):
            return self.mock_entry(plan)

        request_authorization = authorization
        if self.settings.nvidia_api_key:
            request_authorization = f"Bearer {self.settings.nvidia_api_key}"
        if not request_authorization:
            raise PermissionError("NVIDIA API key is required to generate party journal")

        payload = self.payload(plan)
        timeout = httpx.Timeout(self.settings.model_attempt_timeout_seconds, connect=15.0)
        attempts = self.model_attempts(plan.model)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, model in enumerate(attempts):
                payload["model"] = model
                started = time.perf_counter()
                try:
                    response = await client.post(
                        f"{self.settings.nvidia_api_base.rstrip('/')}/chat/completions",
                        json=payload,
                        headers={"Authorization": request_authorization, "Content-Type": "application/json"},
                    )
                    if response.status_code == 429:
                        raise RuntimeError("NVIDIA API returned 429 rate limit")
                    response.raise_for_status()
                    data = response.json()
                    parsed = self.parse(response_text(data), plan)
                    parsed["model"] = data.get("model") or model
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    logger.info(
                        "journal_entry_success campaign_id=%s model=%s turns=%s-%s elapsed_ms=%s",
                        self.store.campaign_id,
                        parsed["model"],
                        plan.from_turn_id,
                        plan.to_turn_id,
                        elapsed_ms,
                    )
                    return parsed
                except (httpx.TimeoutException, httpx.HTTPStatusError, RuntimeError):
                    if index < len(attempts) - 1:
                        continue
                    raise
        raise RuntimeError("No NVIDIA model attempts configured for journal")

    def payload(self, plan: JournalPlan) -> dict[str, Any]:
        context = {
            "current_state": self.state_context(self.store.get_state()),
            "turns": [
                {
                    "turn_id": turn["id"],
                    "player_message": clip(turn["player_message"], 1000),
                    "gm_response": clip(turn["narrative_response"], 1000),
                    "state_version": turn["state_version"],
                }
                for turn in plan.turns
            ],
        }
        return {
            "model": plan.model,
            "stream": False,
            "temperature": 0.35,
            "max_tokens": 1200,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You write a concise player-facing roleplay session journal. Return strict JSON only. "
                        "Do not add new facts. Do not expose service internals. Keep unresolved threads unresolved. "
                        "Use keys: title, recap_text, important_changes."
                    ),
                },
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, indent=2)},
            ],
        }

    def model_attempts(self, primary_model: str) -> list[str]:
        disabled = set(self.settings.nvidia_disabled_models)
        candidates = [primary_model, *self.settings.nvidia_fallback_models]
        attempts: list[str] = []
        for model in candidates:
            if not model or model in disabled or model in attempts:
                continue
            attempts.append(model)
        return attempts or [primary_model]

    def parse(self, content: str, plan: JournalPlan) -> dict[str, Any]:
        cleaned = strip_code_fence(content.strip())
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = {"recap_text": content}
        if not isinstance(data, dict):
            data = {"recap_text": content}
        title = str(data.get("title") or f"Ходы {plan.from_turn_id}-{plan.to_turn_id}").strip()
        recap_text = str(data.get("recap_text") or data.get("summary") or content).strip()
        return {
            "title": clip(title, 160),
            "recap_text": clip(recap_text, 6000),
            "important_changes": as_list(data.get("important_changes")),
        }

    def mock_entry(self, plan: JournalPlan) -> dict[str, Any]:
        lines = [
            f"- Ход {turn['id']}: {clip(turn['player_message'], 140)} -> {clip(turn['narrative_response'], 180)}"
            for turn in plan.turns
        ]
        return {
            "title": f"Ходы {plan.from_turn_id}-{plan.to_turn_id}",
            "recap_text": "\n".join(lines),
            "important_changes": [
                {"turn_id": turn["id"], "change": f"Сцена обновлена ходом {turn['id']}."} for turn in plan.turns[:8]
            ],
            "model": plan.model,
        }

    def state_context(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "meta": state.get("meta", {}),
            "player": state.get("player", {}),
            "relationships": state.get("relationships", {}),
            "active_threads": state.get("active_threads", []),
            "timeline_tail": state.get("timeline", [])[-10:] if isinstance(state.get("timeline"), list) else [],
        }
