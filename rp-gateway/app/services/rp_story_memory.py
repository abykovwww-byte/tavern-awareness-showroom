"""RP-only cumulative story memory maintained by the global service model."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.core.config import Settings
from app.services.context_budget import oldest_turns_within_token_budget, turns_token_count
from app.services.scene_state import unresolved_noncanonical_fallback_turns
from app.services.service_model_client import ServiceModelClient, service_prompt_text
from app.services.service_models import service_model_settings
from app.services.state_store import StateStore


logger = logging.getLogger(__name__)

STORY_MEMORY_SCHEMA = "rp-gateway.rp-story-memory.v2"
STORY_LIST_FIELDS = (
    "canon",
    "rules_and_abilities",
    "inventory_and_assets",
    "characters",
    "active_threads",
    "resolved_threads",
    "unresolved_hooks",
    "chronology",
)
STORY_FIELD_LIMITS = {
    "canon": 40,
    "rules_and_abilities": 30,
    "inventory_and_assets": 40,
    "characters": 60,
    "active_threads": 40,
    "resolved_threads": 40,
    "unresolved_hooks": 40,
    "chronology": 80,
}


@dataclass(frozen=True)
class RPStoryMemoryPlan:
    previous_memory: dict[str, Any] | None
    turns: list[dict[str, Any]]
    from_turn_id: int
    to_turn_id: int
    state_version: int
    model: str


class RPStoryMemoryUpdater:
    """Maintains a bounded, cumulative RP continuity ledger without mutating state."""

    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store
        self.update_turns = max(settings.rp_story_memory_update_turns, 1)
        self.batch_token_budget = max(settings.rp_story_memory_batch_tokens, 1)

    def stats(self) -> dict[str, Any]:
        latest = self.store.effective_rp_story_memory()
        covered_through = int(latest["to_turn_id"]) if latest else 0
        pending = self.story_memory_turns(after_turn_id=covered_through)
        enabled = self.settings.scenario_type == "rp"
        pending_turn_threshold_exceeded = enabled and len(pending) >= self.update_turns
        hard_overflow = self.latest_request_hard_overflow() if enabled else False
        force_refresh = self.latest_force_refresh_diagnostics() if enabled else None
        return {
            "enabled": enabled,
            "scenario_type": self.settings.scenario_type,
            "update_every_turns": self.update_turns,
            "pending_turn_threshold": self.update_turns,
            "batch_token_budget": self.batch_token_budget,
            "prompt_max_chars": self.settings.rp_story_memory_prompt_max_chars,
            "reserved_prompt_tokens": self.settings.rp_story_memory_reserve_tokens if enabled else 0,
            "latest_revision": latest["revision"] if latest else None,
            "covered_through_turn_id": covered_through or None,
            "pending_turns": len(pending),
            "pending_tokens": turns_token_count(pending),
            "pending_turn_threshold_exceeded": pending_turn_threshold_exceeded,
            "update_pending": pending_turn_threshold_exceeded,
            "hard_overflow": hard_overflow,
            "force_refresh_attempted": force_refresh is not None,
            "force_refresh_request_id": (
                force_refresh.get("request_id") if force_refresh is not None else None
            ),
            "force_refresh_batches": (
                force_refresh.get("batches") if force_refresh is not None else 0
            ),
            "force_refresh_terminal_result": (
                force_refresh.get("terminal_result") if force_refresh is not None else None
            ),
            "force_refresh_coverage_before": (
                force_refresh.get("coverage_before") if force_refresh is not None else None
            ),
            "force_refresh_coverage_after": (
                force_refresh.get("coverage_after") if force_refresh is not None else None
            ),
            "operator_status": (
                "overflow"
                if hard_overflow
                else "lagging"
                if pending_turn_threshold_exceeded
                else "normal"
            ),
        }

    def latest_request_hard_overflow(self) -> bool:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT status, error
                FROM turn_requests
                WHERE campaign_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.store.campaign_id,),
            ).fetchone()
        return bool(
            row is not None
            and row["status"] == "failed"
            and "PromptBudgetExceeded" in str(row["error"] or "")
        )

    def latest_force_refresh_diagnostics(self) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT request_id, event_type, event_json
                FROM audit_events
                WHERE campaign_id = ?
                  AND event_type IN (
                      'rp_story_memory_force_refresh',
                      'rp_story_memory_force_refresh_failed'
                  )
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.store.campaign_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            event = json.loads(row["event_json"])
        except (TypeError, json.JSONDecodeError):
            event = {}
        if not isinstance(event, dict):
            event = {}

        def optional_int(key: str) -> int | None:
            value = event.get(key)
            return int(value) if isinstance(value, int) else None

        return {
            "request_id": str(row["request_id"] or event.get("request_id") or "") or None,
            "batches": optional_int("batches") or 0,
            "coverage_before": optional_int("coverage_before"),
            "coverage_after": optional_int("coverage_after"),
            "terminal_result": str(
                event.get("result")
                or (
                    "failed"
                    if row["event_type"] == "rp_story_memory_force_refresh_failed"
                    else "unknown"
                )
            ),
        }

    def build_plan(self, force: bool = False) -> tuple[RPStoryMemoryPlan | None, str]:
        if self.settings.scenario_type != "rp":
            return None, "not_rp"
        previous = self.store.effective_rp_story_memory()
        covered_through = int(previous["to_turn_id"]) if previous else 0
        pending = self.story_memory_turns(after_turn_id=covered_through)
        if not pending:
            return None, "up_to_date"
        has_user_correction = any(turn_story_memory_corrections(turn) for turn in pending)
        if not force and len(pending) < self.update_turns and not has_user_correction:
            return None, "waiting_for_batch"
        batch = oldest_turns_within_token_budget(pending, self.batch_token_budget)
        if not batch:
            return None, "no_turns_for_story_memory"
        runtime = self.service_settings()
        return (
            RPStoryMemoryPlan(
                previous_memory=previous,
                turns=batch,
                from_turn_id=int(batch[0]["id"]),
                to_turn_id=int(batch[-1]["id"]),
                state_version=self.store.current_version() or 1,
                model=runtime.narrative_model,
            ),
            "ready",
        )

    async def catch_up(
        self,
        authorization: str | None,
        *,
        force: bool = True,
        fail_open: bool = False,
        request_id: str | None = None,
        max_batches: int = 64,
        stop_when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        """Advance story memory in bounded batches and report terminal coverage."""

        if max_batches < 1:
            raise ValueError("max_batches must be at least 1")
        before = self.stats()
        batches = 0
        terminal_result = "max_batches_reached"
        for _ in range(max_batches):
            result = await self.update(
                authorization,
                force=force,
                fail_open=fail_open,
                request_id=request_id,
            )
            if not result.get("generated"):
                terminal_result = str(result.get("reason") or "stopped")
                break
            batches += 1
            if stop_when is not None and stop_when(
                {
                    "batches": batches,
                    "story_memory": self.store.effective_rp_story_memory(),
                    "stats": self.stats(),
                }
            ):
                terminal_result = "stop_condition_met"
                break
        after = self.stats()
        coverage_before = int(before.get("covered_through_turn_id") or 0)
        coverage_after = int(after.get("covered_through_turn_id") or 0)
        return {
            "generated": batches > 0,
            "reason": "generated" if batches > 0 else terminal_result,
            "terminal_result": terminal_result,
            "batches": batches,
            "force_refresh_attempted": bool(force),
            "force_refresh_batches": batches,
            "coverage_before": coverage_before,
            "coverage_after": coverage_after,
            "story_memory": self.store.effective_rp_story_memory(),
            "stats": after,
        }

    def validate_corrections(self, corrections: list[dict[str, Any]] | None) -> list[dict[str, str]]:
        if not corrections:
            return []
        if self.settings.scenario_type != "rp" or self.settings.rp_contract_revision < 2:
            raise ValueError("story-memory corrections require RP contract revision 2 or newer")
        return validate_story_memory_corrections(
            self.prompt_snapshot(),
            corrections,
            self.settings.rp_story_memory_max_chars,
        )

    def prompt_snapshot(
        self,
        corrections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Project committed corrections over the latest persisted service snapshot."""

        latest = self.store.effective_rp_story_memory()
        if latest is None:
            return None
        covered_through = int(latest["to_turn_id"])
        pending = self.story_memory_turns(after_turn_id=covered_through)
        has_pending_corrections = any(turn_story_memory_corrections(turn) for turn in pending)
        if not has_pending_corrections and not corrections:
            return latest
        projected = dict(latest)
        memory = latest.get("memory")
        if has_pending_corrections:
            memory = apply_user_story_memory_corrections(
                memory,
                pending,
                self.settings.rp_story_memory_max_chars,
            )
        if corrections:
            validated = validate_story_memory_corrections(
                {"memory": memory},
                corrections,
                self.settings.rp_story_memory_max_chars,
            )
            memory = apply_validated_story_memory_corrections(
                memory,
                validated,
                None,
                self.settings.rp_story_memory_max_chars,
            )
        projected["memory"] = memory
        return projected

    async def update(
        self,
        authorization: str | None,
        *,
        force: bool = False,
        fail_open: bool = True,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        _ = authorization  # Service jobs always use stack-managed credentials.
        plan, reason = self.build_plan(force=force)
        if plan is None:
            return {
                "generated": False,
                "reason": reason,
                "story_memory": self.store.effective_rp_story_memory(),
                "stats": self.stats(),
            }
        try:
            generated = await self.generate(plan, request_id=request_id)
            if self.settings.rp_contract_revision >= 2:
                previous_memory = plan.previous_memory.get("memory") if plan.previous_memory else None
                trusted_memory = apply_user_story_memory_corrections(
                    previous_memory,
                    plan.turns,
                    self.settings.rp_story_memory_max_chars,
                )
                service_candidate = service_story_memory_candidate(
                    previous_memory,
                    generated["memory"],
                    [
                        int(turn["id"])
                        for turn in plan.turns
                        if not turn.get("noncanonical_safe_fallback")
                    ],
                    self.settings.rp_story_memory_max_chars,
                )
                generated["memory"] = reconcile_story_memory(
                    trusted_memory,
                    service_candidate,
                    self.settings.rp_story_memory_max_chars,
                )
            snapshot = self.store.record_rp_story_memory(
                from_turn_id=(
                    int(plan.previous_memory["from_turn_id"])
                    if plan.previous_memory
                    else plan.from_turn_id
                ),
                to_turn_id=plan.to_turn_id,
                state_version=plan.state_version,
                memory=generated["memory"],
                model=generated.get("model") or plan.model,
                contributing_turn_ids=[
                    int(turn["id"])
                    for turn in plan.turns
                    if not turn.get("noncanonical_safe_fallback")
                ],
                base_snapshot_id=(
                    int(plan.previous_memory["id"])
                    if plan.previous_memory
                    else None
                ),
            )
            if snapshot is None:
                self.store.audit(
                    "rp_story_memory_stale_plan",
                    {
                        "from_turn_id": plan.from_turn_id,
                        "to_turn_id": plan.to_turn_id,
                        "state_version": plan.state_version,
                    },
                    request_id,
                )
                return {
                    "generated": False,
                    "reason": "stale_plan",
                    "story_memory": self.store.effective_rp_story_memory(),
                    "stats": self.stats(),
                    "error": "stale_plan",
                }
            self.store.audit(
                "rp_story_memory_updated",
                {
                    "snapshot_id": snapshot["id"],
                    "revision": snapshot["revision"],
                    "from_turn_id": plan.from_turn_id,
                    "to_turn_id": plan.to_turn_id,
                    "state_version": plan.state_version,
                    "model": snapshot["model"],
                },
                request_id,
            )
            return {"generated": True, "reason": "generated", "story_memory": snapshot, "stats": self.stats()}
        except Exception as exc:  # noqa: BLE001 - background memory must not break gameplay
            logger.warning("rp_story_memory_failed campaign_id=%s error=%s", self.store.campaign_id, exc)
            self.store.audit(
                "rp_story_memory_failed",
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
                    "reason": "story_memory_failed",
                    "story_memory": self.store.effective_rp_story_memory(),
                    "stats": self.stats(),
                    "error": type(exc).__name__,
                }
            if isinstance(exc, PermissionError):
                raise
            raise RuntimeError("RP story-memory provider failed") from exc

    async def generate(
        self,
        plan: RPStoryMemoryPlan,
        *,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        runtime = self.service_settings()
        if runtime.llm_api_base.startswith("mock://"):
            return {"memory": self.mock_memory(plan), "model": plan.model}
        payload = self.update_payload(plan)
        attempts = self.model_attempts(plan.model, runtime)
        client = ServiceModelClient(runtime)
        for index, model in enumerate(attempts):
            payload["model"] = model
            started = time.perf_counter()
            try:
                completion = await client.complete(
                    role="rp_story_memory",
                    provider=runtime.llm_provider,
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
                memory = self.parse_memory(completion_text(data))
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.info(
                    "rp_story_memory_success campaign_id=%s model=%s turns=%s-%s elapsed_ms=%s",
                    self.store.campaign_id,
                    data.get("model") or model,
                    plan.from_turn_id,
                    plan.to_turn_id,
                    elapsed_ms,
                )
                return {"memory": memory, "model": data.get("model") or model}
            except (httpx.TimeoutException, httpx.HTTPStatusError, RuntimeError):
                if index < len(attempts) - 1:
                    continue
                raise
        raise RuntimeError("No model attempts configured for RP story memory")

    def update_payload(self, plan: RPStoryMemoryPlan) -> dict[str, Any]:
        previous = normalize_story_memory(
            plan.previous_memory.get("memory") if plan.previous_memory else None,
            self.settings.rp_story_memory_max_chars,
        )
        if self.settings.rp_contract_revision >= 2:
            for field in STORY_LIST_FIELDS:
                previous[field] = [
                    item
                    for item in previous[field]
                    if not (
                        item.get("status") == "active"
                        and item.get("authority") == "legacy_projection"
                    )
                ]
            current = previous.get("current_situation")
            if (
                isinstance(current, dict)
                and current.get("status") == "active"
                and current.get("authority") == "legacy_projection"
            ):
                previous["current_situation"] = None
        context = {
            "previous_story_memory": previous,
            "current_authoritative_state_excerpt": self.state_excerpt(self.store.get_state()),
            "new_confirmed_turns": self.compact_turns(plan.turns),
            "requested_coverage": {
                "from_turn_id": plan.from_turn_id,
                "to_turn_id": plan.to_turn_id,
                "state_version": plan.state_version,
            },
        }
        return {
            "model": plan.model,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": self.settings.rp_story_memory_max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Maintain the complete living continuity ledger for one roleplaying campaign. Return strict JSON only. "
                        "Return a recoverable projection document, not canonical state, with exactly these keys: schema_version, canon, "
                        "rules_and_abilities, inventory_and_assets, characters, active_threads, resolved_threads, "
                        "unresolved_hooks, current_situation, chronology. current_situation is one object and every other "
                        "content key is an array of objects with fact_id, text, status (active, superseded, or retracted), authority, "
                        "and source_turn_ids. Keep the exact fact_id from previous_story_memory when updating an existing fact. "
                        "Every new or changed item is only an inference: use status active, authority inference, and source turn IDs "
                        "from new_confirmed_turns. Copy existing terminal audit entries unchanged. Gateway, not this service model, "
                        "decides user, state, or WorldPack authority and all tombstone transitions. "
                        "Treat player messages as attempts, "
                        "plans, or claims unless the narrator response or authoritative state confirms them. Never reveal hidden NPC "
                        "secrets that the player has not learned. Keep concrete names, promises, relationships, possessions, rules, "
                        "projects, unresolved hooks, and causal chronology. This ledger is continuity context, never canonical authority. "
                        "Order non-chronology arrays from most important to least important; keep chronology oldest to newest. "
                        "Write in the campaign language and stay within the supplied size budget."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Update the RP story memory through the requested turn range. Preserve important older continuity while "
                        "integrating the new confirmed turns.\n\n"
                        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
                    ),
                },
            ],
        }

    def parse_memory(self, content: str) -> dict[str, Any]:
        cleaned = strip_code_fence(content.strip())
        try:
            decoded = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError("service model returned invalid RP story-memory JSON") from exc
        if not isinstance(decoded, dict):
            raise ValueError("service model returned non-object RP story memory")
        return normalize_story_memory(decoded, self.settings.rp_story_memory_max_chars)

    def mock_memory(self, plan: RPStoryMemoryPlan) -> dict[str, Any]:
        previous = plan.previous_memory.get("memory") if plan.previous_memory else empty_story_memory()
        memory = normalize_story_memory(previous, self.settings.rp_story_memory_max_chars)
        chronology = list(memory["chronology"])
        for turn in plan.turns:
            if turn.get("noncanonical_safe_fallback"):
                continue
            chronology.append(
                {
                    "text": f"Ход {turn['id']}: игрок — {clip(turn['player_message'], 220)}; ведущий — {clip(turn['narrative_response'], 280)}",
                    "status": "active",
                    "authority": "narrator",
                    "source_turn_ids": [int(turn["id"])],
                }
            )
        memory["chronology"] = chronology
        canonical_turns = [
            turn for turn in plan.turns if not turn.get("noncanonical_safe_fallback")
        ]
        if canonical_turns:
            memory["current_situation"] = {
                "text": clip(canonical_turns[-1]["narrative_response"], 1200),
                "status": "active",
                "authority": "narrator",
                "source_turn_ids": [int(canonical_turns[-1]["id"])],
            }
        return normalize_story_memory(memory, self.settings.rp_story_memory_max_chars)

    def state_excerpt(self, state: dict[str, Any]) -> str:
        characters = state.get("characters", {})
        completed_threads = state.get("completed_threads", [])
        if not isinstance(completed_threads, list):
            completed_threads = []
        visible_characters: dict[str, Any] = {}
        if isinstance(characters, dict):
            for character_id, character in list(characters.items())[:40]:
                if not isinstance(character, dict):
                    continue
                visible_characters[str(character_id)] = {
                    key: character.get(key)
                    for key in (
                        "name",
                        "status",
                        "location",
                        "current_goal",
                        "attitude_to_player",
                        "loyalty",
                        "trust",
                        "fear",
                        "knowledge",
                        "obligations",
                        "hard_constraints",
                    )
                    if character.get(key) not in (None, "", [])
                }
        excerpt = {
            "meta": state.get("meta", {}),
            "player": state.get("player", {}),
            "relationships": state.get("relationships", {}),
            "characters_without_secrets": visible_characters,
            "active_threads": state.get("active_threads", []),
            "completed_threads": completed_threads[-20:],
            "world_constraints": state.get("world_constraints", []),
            "uncertain_facts": state.get("uncertain_facts", []),
        }
        return clip(json.dumps(excerpt, ensure_ascii=False), 8_000)

    def compact_turns(self, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "turn_id": turn["id"],
                "state_version": turn["state_version"],
                "player_message": clip(turn["player_message"], 1400),
                "narrative_response": clip(turn["narrative_response"], 1800),
                "story_memory_canonical": not bool(turn.get("noncanonical_safe_fallback")),
            }
            for turn in turns
        ]

    def story_memory_turns(self, *, after_turn_id: int) -> list[dict[str, Any]]:
        turns = self.store.turns_for_memory(
            after_turn_id=after_turn_id,
            include_noncanonical_fallback=self.settings.rp_contract_revision >= 7,
        )
        if self.settings.rp_contract_revision < 7:
            return turns
        return unresolved_noncanonical_fallback_turns(self.store.get_state(), turns)

    def service_settings(self) -> Settings:
        return service_model_settings(self.settings)

    @staticmethod
    def model_attempts(primary_model: str, runtime: Settings) -> list[str]:
        disabled = set(runtime.llm_disabled_models)
        candidates = [primary_model, *runtime.llm_fallback_models]
        attempts: list[str] = []
        for model in candidates:
            if model and model not in disabled and model not in attempts:
                attempts.append(model)
        return attempts or [primary_model]


def empty_story_memory() -> dict[str, Any]:
    return {
        "schema_version": STORY_MEMORY_SCHEMA,
        "canon": [],
        "rules_and_abilities": [],
        "inventory_and_assets": [],
        "characters": [],
        "active_threads": [],
        "resolved_threads": [],
        "unresolved_hooks": [],
        "current_situation": None,
        "chronology": [],
    }


def normalize_story_memory(value: Any, max_chars: int) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    normalized = empty_story_memory()
    current_items = story_item_list(source.get("current_situation"), limit=1, item_chars=2_000)
    normalized["current_situation"] = current_items[0] if current_items else None
    for field in STORY_LIST_FIELDS:
        normalized[field] = story_item_list(
            source.get(field),
            limit=STORY_FIELD_LIMITS[field],
            item_chars=600,
        )
    return fit_story_memory(normalized, max(max_chars, 1))


def service_story_memory_candidate(
    previous_value: Any,
    proposed_value: Any,
    batch_turn_ids: list[int],
    max_chars: int,
) -> dict[str, Any]:
    """Convert untrusted service output into Gateway-owned inference candidates."""
    previous = normalize_story_memory(previous_value, max_chars)
    proposed = normalize_story_memory(proposed_value, max_chars)
    source_turn_ids: list[int] = []
    for turn_id in batch_turn_ids:
        normalized_turn_id = int(turn_id)
        if normalized_turn_id >= 0 and normalized_turn_id not in source_turn_ids:
            source_turn_ids.append(normalized_turn_id)
    source_turn_ids = source_turn_ids[-20:]

    candidate = empty_story_memory()
    for field in STORY_LIST_FIELDS:
        candidate[field] = service_story_items(
            previous[field],
            proposed[field],
            source_turn_ids,
        )

    previous_current = [previous["current_situation"]] if previous.get("current_situation") else []
    proposed_current = [proposed["current_situation"]] if proposed.get("current_situation") else []
    current_items = service_story_items(previous_current, proposed_current, source_turn_ids)
    candidate["current_situation"] = current_items[0] if current_items else None
    return fit_story_memory(candidate, max(max_chars, 1))


def turn_story_memory_corrections(turn: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = turn.get("metadata")
    if not isinstance(metadata, dict):
        return []
    corrections = metadata.get("story_memory_corrections")
    if not isinstance(corrections, list):
        return []
    return [dict(item) for item in corrections if isinstance(item, dict)]


def story_item_is_safely_removable(item: dict[str, Any]) -> bool:
    return str(item.get("authority") or "legacy_projection") in {
        "inference",
        "narrator",
        "legacy_projection",
    }


def validate_story_memory_corrections(
    snapshot: dict[str, Any] | None,
    corrections: list[dict[str, Any]],
    max_chars: int,
) -> list[dict[str, str]]:
    memory = normalize_story_memory(snapshot.get("memory") if snapshot else None, max_chars)
    normalized: list[dict[str, str]] = []
    seen_targets: set[tuple[str, str]] = set()
    protected_targets = {
        (str(item.get("field") or ""), str(item.get("fact_id") or ""))
        for item in corrections
        if isinstance(item, dict)
    }
    replace_counts: dict[str, int] = {}
    for correction in corrections:
        if not isinstance(correction, dict):
            raise ValueError("story-memory correction must be an object")
        field = str(correction.get("field") or "")
        fact_id = str(correction.get("fact_id") or "")
        action = str(correction.get("action") or "")
        if field not in STORY_LIST_FIELDS:
            raise ValueError(f"unsupported story-memory field: {field or '<blank>'}")
        if action not in {"retract", "replace"}:
            raise ValueError(f"unsupported story-memory correction action: {action or '<blank>'}")
        target_key = (field, fact_id)
        if target_key in seen_targets:
            raise ValueError(f"duplicate story-memory correction target: {field}/{fact_id}")
        target = next(
            (
                item
                for item in memory[field]
                if str(item.get("fact_id") or "") == fact_id and item.get("status") == "active"
            ),
            None,
        )
        if target is None:
            raise ValueError(f"active story-memory fact not found: {field}/{fact_id}")
        replacement_text = str(correction.get("replacement_text") or "").strip()
        if action == "replace":
            if not replacement_text:
                raise ValueError("replacement_text is required for replace")
            replacement_fingerprint = story_fact_fingerprint(replacement_text)
            if replacement_fingerprint == story_fact_fingerprint(str(target.get("text") or "")):
                raise ValueError("replacement_text must differ from the replaced fact")
            if any(
                str(item.get("fact_id") or "") != fact_id
                and story_fact_fingerprint(str(item.get("text") or "")) == replacement_fingerprint
                for item in memory[field]
            ):
                raise ValueError("replacement_text already exists in the story-memory field")
            replace_counts[field] = replace_counts.get(field, 0) + 1
            required_slots = max(
                len(memory[field]) + replace_counts[field] - STORY_FIELD_LIMITS[field],
                0,
            )
            removable_slots = sum(
                1
                for item in memory[field]
                if story_item_is_safely_removable(item)
                and (field, str(item.get("fact_id") or "")) not in protected_targets
            )
            if removable_slots < required_slots:
                raise ValueError(
                    f"story-memory field is full and has no safely removable weak entry: {field}"
                )
        elif correction.get("replacement_text") is not None:
            raise ValueError("replacement_text is only allowed for replace")
        item = {"field": field, "fact_id": fact_id, "action": action}
        if replacement_text:
            item["replacement_text"] = replacement_text
        normalized.append(item)
        seen_targets.add(target_key)
    projected = apply_validated_story_memory_corrections(
        memory,
        normalized,
        None,
        max_chars,
    )
    oversized_fields = [
        field
        for field in STORY_LIST_FIELDS
        if len(projected[field]) > STORY_FIELD_LIMITS[field]
    ]
    if oversized_fields:
        raise ValueError(
            "story-memory correction exceeds field capacity: " + ", ".join(oversized_fields)
        )
    if len(json.dumps(projected, ensure_ascii=False)) > max(max_chars, 1):
        raise ValueError("story-memory correction exceeds max_chars capacity")
    return normalized


def apply_user_story_memory_corrections(
    memory_value: Any,
    turns: list[dict[str, Any]],
    max_chars: int,
) -> dict[str, Any]:
    memory = normalize_story_memory(memory_value, max_chars)
    for turn in turns:
        corrections = turn_story_memory_corrections(turn)
        if not corrections:
            continue
        validated = validate_story_memory_corrections(
            {"memory": memory},
            corrections,
            max_chars,
        )
        memory = apply_validated_story_memory_corrections(
            memory,
            validated,
            int(turn["id"]),
            max_chars,
        )
    return memory


def apply_validated_story_memory_corrections(
    memory_value: Any,
    corrections: list[dict[str, str]],
    source_turn_id: int | None,
    max_chars: int,
) -> dict[str, Any]:
    """Apply already validated Gateway corrections; None provenance is prompt-only."""

    memory = normalize_story_memory(memory_value, max_chars)
    source_turn_ids = [source_turn_id] if source_turn_id is not None else []
    protected_targets: dict[str, set[str]] = {}
    for correction in corrections:
        protected_targets.setdefault(correction["field"], set()).add(correction["fact_id"])
    for correction in corrections:
        field = correction["field"]
        fact_id = correction["fact_id"]
        if correction["action"] == "replace" and len(memory[field]) >= STORY_FIELD_LIMITS[field]:
            removable_index = next(
                (
                    index
                    for index in range(len(memory[field]) - 1, -1, -1)
                    if story_item_is_safely_removable(memory[field][index])
                    and str(memory[field][index].get("fact_id") or "")
                    not in protected_targets[field]
                ),
                None,
            )
            if removable_index is None:
                raise ValueError(
                    f"story-memory field is full and has no safely removable weak entry: {field}"
                )
            memory[field].pop(removable_index)
        target_index = next(
            index
            for index, item in enumerate(memory[field])
            if str(item["fact_id"]) == fact_id and item["status"] == "active"
        )
        target = memory[field][target_index]
        memory[field][target_index] = {
            **target,
            "status": "retracted" if correction["action"] == "retract" else "superseded",
            "authority": "user_correction",
            "source_turn_ids": source_turn_ids,
        }
        if correction["action"] == "replace":
            replacement_text = correction["replacement_text"]
            memory[field].append(
                {
                    "fact_id": story_fact_id(None, replacement_text),
                    "text": replacement_text,
                    "status": "active",
                    "authority": "user_correction",
                    "source_turn_ids": source_turn_ids,
                }
            )
    return fit_story_memory(memory, max(max_chars, 1))


def service_story_items(
    previous: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
    source_turn_ids: list[int],
) -> list[dict[str, Any]]:
    previous_by_id = {str(item["fact_id"]): item for item in previous}
    previous_by_text = {
        story_fact_fingerprint(str(item.get("text") or "")): item
        for item in previous
    }
    candidates: list[dict[str, Any]] = []
    seen_fact_ids: set[str] = set()
    seen_text: set[str] = set()
    for proposed_item in proposed:
        text = str(proposed_item["text"])
        fingerprint = story_fact_fingerprint(text)
        exact_previous = previous_by_text.get(fingerprint)
        referenced_previous = previous_by_id.get(str(proposed_item["fact_id"]))
        if exact_previous is not None:
            sanitized = dict(exact_previous)
        else:
            sanitized = {
                "fact_id": (
                    str(referenced_previous["fact_id"])
                    if referenced_previous is not None
                    else story_fact_id(None, text)
                ),
                "text": text,
                "status": "active",
                "authority": "inference",
                "source_turn_ids": list(source_turn_ids),
            }
        sanitized_fact_id = str(sanitized["fact_id"])
        sanitized_text = story_fact_fingerprint(str(sanitized["text"]))
        if sanitized_fact_id in seen_fact_ids or sanitized_text in seen_text:
            continue
        candidates.append(sanitized)
        seen_fact_ids.add(sanitized_fact_id)
        seen_text.add(sanitized_text)
    return candidates


def reconcile_story_memory(previous_value: Any, proposed_value: Any, max_chars: int) -> dict[str, Any]:
    """Merge a service-model proposal without letting weak summaries erase or revive facts."""
    previous = normalize_story_memory(previous_value, max_chars)
    proposed = normalize_story_memory(proposed_value, max_chars)
    result = empty_story_memory()
    for field in STORY_LIST_FIELDS:
        result[field] = reconcile_story_items(previous[field], proposed[field])

    previous_current = [previous["current_situation"]] if previous.get("current_situation") else []
    proposed_current = [proposed["current_situation"]] if proposed.get("current_situation") else []
    current_items = reconcile_story_items(previous_current, proposed_current)
    active_current = [item for item in current_items if item["status"] == "active"]
    result["current_situation"] = (active_current or current_items or [None])[-1]
    return fit_story_memory(result, max(max_chars, 1))


def reconcile_story_items(
    previous: list[dict[str, Any]],
    proposed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stronger = {"worldpack", "state", "user_correction"}
    merged = [dict(item) for item in previous]
    index = {str(item["fact_id"]): position for position, item in enumerate(merged)}
    tombstoned_text = {
        story_fact_fingerprint(str(item.get("text") or ""))
        for item in previous
        if item.get("status") in {"superseded", "retracted"}
    }
    for candidate in proposed:
        fact_id = str(candidate["fact_id"])
        current_position = index.get(fact_id)
        if current_position is None:
            if candidate["status"] != "active" or (
                story_fact_fingerprint(str(candidate["text"])) in tombstoned_text
            ):
                continue
            index[fact_id] = len(merged)
            merged.append(dict(candidate))
            continue

        current = merged[current_position]
        candidate_turn = max(candidate.get("source_turn_ids") or [0])
        current_turn = max(current.get("source_turn_ids") or [0])
        authority = str(candidate.get("authority") or "inference")
        current_authority = str(current.get("authority") or "inference")
        if current_authority == "legacy_projection" and authority not in stronger:
            continue
        if candidate["status"] in {"superseded", "retracted"}:
            if authority not in stronger or candidate_turn <= current_turn:
                continue
            tombstoned_text.add(story_fact_fingerprint(str(current.get("text") or "")))
            merged[current_position] = dict(candidate)
            continue
        if current.get("status") in {"superseded", "retracted"}:
            if authority not in stronger or candidate_turn <= current_turn:
                continue
        if current_authority in stronger and (
            authority not in stronger or candidate_turn <= current_turn
        ):
            continue
        merged[current_position] = dict(candidate)
    return merged


def fit_story_memory(memory: dict[str, Any], max_chars: int) -> dict[str, Any]:
    fitted = json.loads(json.dumps(memory, ensure_ascii=False))
    drop_order = (
        ("chronology", 12, True),
        ("resolved_threads", 8, True),
        ("inventory_and_assets", 10, False),
        ("characters", 12, False),
        ("rules_and_abilities", 10, False),
        ("canon", 12, False),
        ("unresolved_hooks", 10, False),
        ("active_threads", 12, False),
    )
    for field, _minimum, drop_oldest in drop_order:
        while len(fitted[field]) > STORY_FIELD_LIMITS[field]:
            positions = (
                range(len(fitted[field]))
                if drop_oldest
                else range(len(fitted[field]) - 1, -1, -1)
            )
            removable = next(
                (
                    position
                    for position in positions
                    if story_item_is_safely_removable(fitted[field][position])
                ),
                None,
            )
            if removable is None:
                break
            fitted[field].pop(removable)
    while len(json.dumps(fitted, ensure_ascii=False)) > max_chars:
        changed = False
        for field, minimum, drop_oldest in drop_order:
            items = fitted[field]
            if len(items) <= minimum:
                continue
            positions = range(len(items)) if drop_oldest else range(len(items) - 1, -1, -1)
            removable = next(
                (
                    position
                    for position in positions
                    if story_item_is_safely_removable(items[position])
                ),
                None,
            )
            if removable is None:
                continue
            items.pop(removable)
            changed = True
            break
        if not changed:
            for field, _minimum, drop_oldest in drop_order:
                items = fitted[field]
                positions = range(len(items)) if drop_oldest else range(len(items) - 1, -1, -1)
                removable = next(
                    (
                        position
                        for position in positions
                        if story_item_is_safely_removable(items[position])
                    ),
                    None,
                )
                if removable is None:
                    continue
                items.pop(removable)
                changed = True
                break
            if changed:
                continue
            current = fitted.get("current_situation")
            if (
                isinstance(current, dict)
                and current.get("text")
                and story_item_is_safely_removable(current)
            ):
                shortened = clip(current["text"], max(len(str(current["text"])) - 300, 0))
                if shortened:
                    current["text"] = shortened
                else:
                    fitted["current_situation"] = None
                continue
            break
    return fitted


def story_memory_prompt_text(
    snapshot: dict[str, Any],
    max_chars: int,
    rp_contract_revision: int = 0,
) -> str:
    memory = normalize_story_memory(snapshot.get("memory"), max_chars)
    include_legacy_projection = rp_contract_revision < 2
    sections: list[tuple[str, list[str]]] = [
        (
            "СОСТОЯНИЕ НА МОМЕНТ ПАУЗЫ",
            active_story_texts(
                [memory["current_situation"]],
                include_legacy_projection=include_legacy_projection,
            )
            if memory["current_situation"]
            else [],
        ),
        ("КАНОН", active_story_texts(memory["canon"], include_legacy_projection=include_legacy_projection)),
        ("АКТИВНЫЕ СЮЖЕТНЫЕ ЛИНИИ", active_story_texts(memory["active_threads"], include_legacy_projection=include_legacy_projection)),
        ("НЕРАСКРЫТЫЕ ЗАЦЕПКИ", active_story_texts(memory["unresolved_hooks"], include_legacy_projection=include_legacy_projection)),
        ("ПЕРСОНАЖИ", active_story_texts(memory["characters"], include_legacy_projection=include_legacy_projection)),
        ("ПРАВИЛА И СПОСОБНОСТИ", active_story_texts(memory["rules_and_abilities"], include_legacy_projection=include_legacy_projection)),
        ("ИНВЕНТАРЬ И АКТИВЫ", active_story_texts(memory["inventory_and_assets"], include_legacy_projection=include_legacy_projection)),
        ("ХРОНОЛОГИЯ", active_story_texts(memory["chronology"], include_legacy_projection=include_legacy_projection)[-24:]),
        ("РАЗРЕШЁННЫЕ ЛИНИИ", active_story_texts(memory["resolved_threads"], include_legacy_projection=include_legacy_projection)[-16:]),
    ]
    lines = [
        f"revision={snapshot.get('revision')} covered_turns={snapshot.get('from_turn_id')}-{snapshot.get('to_turn_id')}",
    ]
    for title, items in sections:
        if not items:
            continue
        candidate = [*lines, "", f"## {title}", *[f"- {item}" for item in items]]
        if len("\n".join(candidate)) <= max_chars:
            lines = candidate
            continue
        for item in items:
            candidate = [*lines, "", f"## {title}", f"- {item}"] if f"## {title}" not in lines else [*lines, f"- {item}"]
            if len("\n".join(candidate)) > max_chars:
                break
            lines = candidate
    return "\n".join(lines)


def story_item_list(value: Any, *, limit: int, item_chars: int) -> list[dict[str, Any]]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, dict):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    items: list[dict[str, Any]] = []
    for item in values:
        source = item if isinstance(item, dict) else {"text": item}
        text = clip(source.get("text"), item_chars).strip()
        if not text:
            continue
        status = str(source.get("status") or "active")
        if status not in {"active", "superseded", "retracted"}:
            status = "active"
        authority = str(source.get("authority") or "legacy_projection")
        if authority not in {
            "worldpack",
            "user_correction",
            "state",
            "narrator",
            "inference",
            "legacy_projection",
        }:
            authority = "inference"
        raw_turn_ids = source.get("source_turn_ids")
        turn_ids = []
        if isinstance(raw_turn_ids, list):
            for turn_id in raw_turn_ids:
                try:
                    normalized_turn_id = int(turn_id)
                except (TypeError, ValueError):
                    continue
                if normalized_turn_id >= 0 and normalized_turn_id not in turn_ids:
                    turn_ids.append(normalized_turn_id)
        normalized = {
            "fact_id": story_fact_id(source.get("fact_id"), text),
            "text": text,
            "status": status,
            "authority": authority,
            "source_turn_ids": turn_ids[:20],
        }
        if any(existing["text"] == text and existing["status"] == status for existing in items):
            continue
        items.append(normalized)
        if len(items) >= limit:
            break
    return items


def active_story_texts(
    items: list[dict[str, Any]],
    *,
    include_legacy_projection: bool = True,
) -> list[str]:
    return [
        str(item.get("text") or "")
        for item in items
        if isinstance(item, dict)
        and item.get("status") == "active"
        and (include_legacy_projection or item.get("authority") != "legacy_projection")
        and str(item.get("text") or "").strip()
    ]


def story_fact_id(value: Any, text: str) -> str:
    supplied = str(value or "").strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{7,79}", supplied):
        return supplied
    digest = hashlib.sha256(story_fact_fingerprint(text).encode("utf-8")).hexdigest()[:20]
    return f"fact:{digest}"


def story_fact_fingerprint(text: str) -> str:
    return " ".join(re.findall(r"[\w]+", text.casefold(), flags=re.UNICODE))


def completion_text(response: dict[str, Any]) -> str:
    return str(response.get("choices", [{}])[0].get("message", {}).get("content", ""))


def strip_code_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if len(lines) >= 3 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content


def clip(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[: max(limit, 0)]
    return text[: limit - 3] + "..."
