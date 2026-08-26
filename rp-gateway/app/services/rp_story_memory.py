"""RP-only cumulative story memory maintained by the global service model."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Callable

import httpx

from app.core.config import Settings
from app.services.context_budget import oldest_turns_within_token_budget, turns_token_count
from app.services.rp_history import (
    RP_MEMORY_SECTION_KEYS,
    eligible_rp_turns,
    rp_turn_messages,
    story_memory_safe_coverage,
)
from app.services.scene_state import unresolved_noncanonical_fallback_turns
from app.services.service_model_client import ServiceModelClient, service_prompt_text
from app.services.service_models import service_model_settings
from app.services.state_store import StateStore


logger = logging.getLogger(__name__)

STORY_MEMORY_SCHEMA = "rp-gateway.rp-story-memory.v2"
SECTIONED_STORY_MEMORY_SCHEMA = "rp-gateway.rp-story-memory.v3"
STORY_MEMORY_SECTION_FIELDS = {
    "situation": ("current_situation", "canon"),
    "threads": ("active_threads", "resolved_threads"),
    "characters": ("characters",),
    "assets_and_rules": ("inventory_and_assets", "rules_and_abilities"),
    "chronology_and_hooks": ("chronology", "unresolved_hooks"),
}
STORY_MEMORY_SECTION_BATCH_TURNS = 8
STORY_MEMORY_SECTION_INPUT_CHARS = 20_000
STORY_MEMORY_ITEM_KEYS = {
    "fact_id",
    "text",
    "status",
    "authority",
    "source_turn_ids",
}
STORY_MEMORY_ITEM_STATUSES = {"active", "superseded", "retracted"}
STORY_MEMORY_ITEM_AUTHORITIES = {
    "worldpack",
    "user",
    "state",
    "narrator",
    "inference",
    "legacy_projection",
}
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


def story_memory_fact_response_schema(*, text_limit: int) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(STORY_MEMORY_ITEM_KEYS),
        "properties": {
            "fact_id": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9_.:-]{7,79}$",
            },
            "text": {"type": "string", "minLength": 1, "maxLength": text_limit},
            "status": {"type": "string", "enum": sorted(STORY_MEMORY_ITEM_STATUSES)},
            "authority": {"type": "string", "enum": sorted(STORY_MEMORY_ITEM_AUTHORITIES)},
            "source_turn_ids": {
                "type": "array",
                "maxItems": 20,
                "uniqueItems": True,
                "items": {"type": "integer", "minimum": 0},
            },
        },
    }


def story_memory_section_response_schema(section_key: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for field in STORY_MEMORY_SECTION_FIELDS[section_key]:
        if field == "current_situation":
            item_schema = story_memory_fact_response_schema(text_limit=2_000)
            item_schema["type"] = ["object", "null"]
            properties[field] = item_schema
        else:
            properties[field] = {
                "type": "array",
                "maxItems": STORY_FIELD_LIMITS[field],
                "items": story_memory_fact_response_schema(text_limit=600),
            }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(STORY_MEMORY_SECTION_FIELDS[section_key]),
        "properties": properties,
    }


def story_memory_response_format(section_key: str | None = None) -> dict[str, Any]:
    if section_key is None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": list(RP_MEMORY_SECTION_KEYS),
            "properties": {
                key: story_memory_section_response_schema(key)
                for key in RP_MEMORY_SECTION_KEYS
            },
        }
        name = "rp_story_memory_sections"
    else:
        schema = story_memory_section_response_schema(section_key)
        name = f"rp_story_memory_{section_key}"
    return {
        "type": "json_schema",
        "json_schema": {
            "name": name,
            "strict": True,
            "schema": schema,
        },
    }


@dataclass(frozen=True)
class RPStoryMemoryPlan:
    previous_memory: dict[str, Any] | None
    turns: list[dict[str, Any]]
    from_turn_id: int
    to_turn_id: int
    state_version: int
    model: str


@dataclass(frozen=True)
class RPStoryMemorySectionPlan:
    previous_memory: dict[str, Any] | None
    section_turns: dict[str, list[dict[str, Any]]]
    section_coverage: dict[str, int]
    state_version: int
    model: str
    update_id: str


class RPStoryMemoryUpdater:
    """Maintains a bounded, cumulative RP continuity ledger without mutating state."""

    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store
        self.update_turns = max(settings.rp_story_memory_update_turns, 1)
        self.batch_token_budget = max(settings.rp_story_memory_batch_tokens, 1)

    @property
    def revision_eight(self) -> bool:
        return self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 8

    def stats(self) -> dict[str, Any]:
        latest = self.store.effective_rp_story_memory()
        covered_through = (
            story_memory_safe_coverage(latest)
            if self.revision_eight
            else int(latest["to_turn_id"])
            if latest
            else 0
        )
        pending = self.story_memory_turns(after_turn_id=covered_through)
        enabled = self.settings.scenario_type == "rp"
        all_memory_turns = self.story_memory_turns(after_turn_id=0) if enabled else []
        raw_turn_count = len(all_memory_turns)
        memory = latest.get("memory") if latest else {}
        observed_through = (
            int(memory.get("observed_through_turn_id") or 0)
            if isinstance(memory, dict)
            else 0
        )
        newly_observed = sum(
            1 for turn in all_memory_turns if int(turn["id"]) > observed_through
        )
        pending_turn_threshold_exceeded = (
            enabled
            and (
                len(pending) >= self.update_turns
                if not self.revision_eight
                else raw_turn_count > self.settings.effective_rp_raw_history_window_turns
                and (latest is None or newly_observed >= self.update_turns)
            )
        )
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
            "section_status": (
                normalize_section_status(latest.get("memory") if latest else None)
                if self.revision_eight
                else None
            ),
            "pending_turns": len(pending),
            "observed_through_turn_id": observed_through or None,
            "newly_observed_turns": newly_observed,
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
        if self.revision_eight:
            raise RuntimeError("revision 8 uses build_section_plan()")
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

    def build_section_plan(
        self,
        force: bool = False,
        request_id: str | None = None,
    ) -> tuple[RPStoryMemorySectionPlan | None, str]:
        """Build one independent oldest-first batch for each rev-8 memory section."""

        if self.settings.scenario_type != "rp":
            return None, "not_rp"
        if not self.revision_eight:
            return None, "legacy_revision"
        all_turns = self.story_memory_turns(after_turn_id=0)
        if len(all_turns) <= self.settings.effective_rp_raw_history_window_turns:
            return None, "waiting_for_raw_window"

        previous = self.store.effective_rp_story_memory()
        previous_memory = normalize_sectioned_story_memory(
            previous.get("memory") if previous else None,
            self.settings.rp_story_memory_max_chars,
        )
        statuses = normalize_section_status(previous_memory)
        observed_through = int(previous_memory.get("observed_through_turn_id") or 0)
        newly_observed = [turn for turn in all_turns if int(turn["id"]) > observed_through]
        cadence_ready = force or previous is None or len(newly_observed) >= self.update_turns
        retry_same_run = bool(
            request_id
            and str(previous_memory.get("last_update_request_id") or "") == request_id
        )
        section_turns: dict[str, list[dict[str, Any]]] = {}
        section_coverage: dict[str, int] = {}
        for section_key in RP_MEMORY_SECTION_KEYS:
            coverage = int(statuses[section_key]["coverage"])
            pending = [turn for turn in all_turns if int(turn["id"]) > coverage]
            section_coverage[section_key] = coverage
            retry_section = retry_same_run and statuses[section_key]["status"] in {"stale", "failed"}
            if (
                not pending
                or (retry_same_run and not retry_section)
                or (not retry_same_run and not cadence_ready)
            ):
                section_turns[section_key] = []
                continue
            section_turns[section_key] = pending[:STORY_MEMORY_SECTION_BATCH_TURNS]

        if not any(section_turns.values()):
            return None, "up_to_date" if not any(
                int(turn["id"]) > int(statuses[key]["coverage"])
                for key in RP_MEMORY_SECTION_KEYS
                for turn in all_turns
            ) else "waiting_for_batch"

        state_version = self.store.current_version() or 1
        plan_fingerprint = {
            "campaign_id": self.store.campaign_id,
            "base_snapshot_id": int(previous["id"]) if previous else None,
            "state_version": state_version,
            "sections": {
                key: [int(turn["id"]) for turn in section_turns[key]]
                for key in RP_MEMORY_SECTION_KEYS
            },
        }
        update_id = "smu:" + hashlib.sha256(
            json.dumps(plan_fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        return (
            RPStoryMemorySectionPlan(
                previous_memory=previous,
                section_turns=section_turns,
                section_coverage=section_coverage,
                state_version=state_version,
                model=self.settings.rp_story_memory_model,
                update_id=update_id,
            ),
            "ready",
        )

    def should_enqueue(self) -> bool:
        """Avoid legacy and heavy story-memory jobs before the rev-8 RAW window is full."""

        if not self.revision_eight:
            return self.settings.scenario_type == "rp"
        plan, _reason = self.build_section_plan(force=False, request_id=None)
        return plan is not None

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
        covered_through = (
            story_memory_safe_coverage(latest)
            if self.revision_eight
            else int(latest["to_turn_id"])
        )
        pending = self.story_memory_turns(after_turn_id=covered_through)
        has_pending_corrections = any(turn_story_memory_corrections(turn) for turn in pending)
        if not has_pending_corrections and not corrections:
            return latest
        correction_authority = "user" if self.revision_eight else "user_correction"
        projected = dict(latest)
        memory = latest.get("memory")
        if has_pending_corrections:
            memory = apply_user_story_memory_corrections(
                memory,
                pending,
                self.settings.rp_story_memory_max_chars,
                authority=correction_authority,
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
                authority=correction_authority,
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
        if self.revision_eight:
            return await self.update_sections(
                force=force,
                fail_open=fail_open,
                request_id=request_id,
            )
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

    async def update_sections(
        self,
        *,
        force: bool,
        fail_open: bool,
        request_id: str | None,
    ) -> dict[str, Any]:
        """Persist a combined rev-8 update plus structural section retries."""

        player_correction = self.player_correction_for_request(request_id)
        if player_correction is not None:
            return await self.update_player_correction(
                player_correction,
                fail_open=fail_open,
                request_id=request_id,
            )

        plan, reason = self.build_section_plan(force=force, request_id=request_id)
        if plan is None:
            return {
                "generated": False,
                "reason": reason,
                "story_memory": self.store.effective_rp_story_memory(),
                "stats": self.stats(),
            }

        previous_value = plan.previous_memory.get("memory") if plan.previous_memory else None
        memory = normalize_sectioned_story_memory(
            previous_value,
            self.settings.rp_story_memory_max_chars,
        )
        correction_turns = {
            int(turn["id"]): turn
            for turns in plan.section_turns.values()
            for turn in turns
            if turn_story_memory_corrections(turn)
        }
        if correction_turns:
            memory = normalize_sectioned_story_memory(
                apply_user_story_memory_corrections(
                    memory,
                    list(correction_turns.values()),
                    self.settings.rp_story_memory_max_chars,
                    authority="user",
                ),
                self.settings.rp_story_memory_max_chars,
            )
        statuses = normalize_section_status(memory)
        succeeded_turn_ids: set[int] = set()
        failures: dict[str, str] = {}
        used_models: list[str] = []

        scheduled_sections = [
            section_key
            for section_key in RP_MEMORY_SECTION_KEYS
            if plan.section_turns[section_key]
        ]
        generated_sections: dict[str, dict[str, Any]] = {}
        retry_same_update = bool(
            request_id
            and str(memory.get("last_update_request_id") or "") == request_id
        )

        if retry_same_update:
            for section_key in scheduled_sections:
                try:
                    generated_sections[section_key] = await self.generate_section(
                        plan,
                        section_key,
                        plan.section_turns[section_key],
                        memory,
                        request_id=request_id,
                    )
                except Exception as exc:  # noqa: BLE001 - durable retry remains section-scoped
                    failures[section_key] = f"{type(exc).__name__}: {exc}"
        else:
            try:
                combined = await self.generate_sections(
                    plan,
                    plan.section_turns,
                    memory,
                    request_id=request_id,
                )
            except Exception as exc:  # noqa: BLE001 - transport/input failure affects the main call
                error = f"{type(exc).__name__}: {exc}"
                failures.update({section_key: error for section_key in scheduled_sections})
            else:
                combined_sections = combined.get("sections") or {}
                combined_turns = combined.get("turns") or {}
                combined_errors = combined.get("errors") or {}
                for section_key in scheduled_sections:
                    if section_key in combined_sections and combined_turns.get(section_key):
                        generated_sections[section_key] = {
                            "section": combined_sections[section_key],
                            "model": combined.get("model") or plan.model,
                            "turns": combined_turns[section_key],
                        }
                        continue
                    validation_error = str(
                        combined_errors.get(section_key)
                        or "combined response omitted the section"
                    )
                    try:
                        generated_sections[section_key] = await self.generate_section(
                            plan,
                            section_key,
                            plan.section_turns[section_key],
                            memory,
                            request_id=request_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - one retry cannot cancel valid sections
                        failures[section_key] = (
                            f"combined validation: {validation_error}; "
                            f"section retry: {type(exc).__name__}: {exc}"
                        )

        for section_key in scheduled_sections:
            generated = generated_sections.get(section_key)
            if generated is not None:
                memory = merge_story_memory_section(
                    memory,
                    generated["section"],
                    section_key,
                    [
                        int(turn["id"])
                        for turn in generated["turns"]
                        if not turn.get("noncanonical_safe_fallback")
                    ],
                    self.settings.rp_story_memory_max_chars,
                )
                statuses[section_key] = {
                    "coverage": int(generated["turns"][-1]["id"]),
                    "status": "fresh",
                }
                succeeded_turn_ids.update(
                    int(turn["id"])
                    for turn in generated["turns"]
                    if not turn.get("noncanonical_safe_fallback")
                )
                used_models.append(str(generated.get("model") or plan.model))
            else:
                failures.setdefault(section_key, "section was not generated")
                statuses[section_key] = {
                    "coverage": int(plan.section_coverage[section_key]),
                    "status": "stale" if int(plan.section_coverage[section_key]) > 0 else "failed",
                }
                logger.warning(
                    "rp_story_memory_section_failed campaign_id=%s section=%s update_id=%s error=%s",
                    self.store.campaign_id,
                    section_key,
                    plan.update_id,
                    failures[section_key],
                )

        memory["schema_version"] = SECTIONED_STORY_MEMORY_SCHEMA
        memory["section_status"] = statuses
        all_turns = self.story_memory_turns(after_turn_id=0)
        memory["observed_through_turn_id"] = max(
            (int(turn["id"]) for turn in all_turns),
            default=int(memory.get("observed_through_turn_id") or 0),
        )
        memory["last_update_request_id"] = request_id
        memory = normalize_sectioned_story_memory(
            memory,
            self.settings.rp_story_memory_max_chars,
        )
        coverage_values = [int(statuses[key]["coverage"]) for key in RP_MEMORY_SECTION_KEYS]
        outer_coverage = max(coverage_values, default=0)
        safe_coverage = min(coverage_values, default=0)
        previous_from = int(plan.previous_memory.get("from_turn_id") or 0) if plan.previous_memory else 0
        from_turn_id = previous_from or min(succeeded_turn_ids, default=0)

        try:
            snapshot = self.store.record_rp_story_memory(
                from_turn_id=from_turn_id,
                to_turn_id=outer_coverage,
                state_version=plan.state_version,
                memory=memory,
                model=used_models[-1] if used_models else plan.model,
                contributing_turn_ids=sorted(succeeded_turn_ids),
                base_snapshot_id=(int(plan.previous_memory["id"]) if plan.previous_memory else None),
                update_id=plan.update_id,
                allow_same_coverage=True,
            )
        except TypeError:
            # Kept only so source remains importable while an older store is upgraded
            # in the same revision; the repository migration supplies these arguments.
            raise RuntimeError("rev-8 story-memory persistence migration is missing")
        if snapshot is None:
            return {
                "generated": False,
                "reason": "stale_plan",
                "story_memory": self.store.effective_rp_story_memory(),
                "stats": self.stats(),
                "error": "stale_plan",
            }

        event = {
            "snapshot_id": snapshot["id"],
            "revision": snapshot["revision"],
            "update_id": plan.update_id,
            "safe_coverage": safe_coverage,
            "section_status": statuses,
            "failed_sections": sorted(failures),
            "model": snapshot["model"],
        }
        self.store.audit(
            "rp_story_memory_partial" if failures else "rp_story_memory_updated",
            event,
            request_id,
        )
        return {
            "generated": True,
            "reason": "partial" if failures else "generated",
            "story_memory": snapshot,
            "stats": self.stats(),
            "failed_sections": sorted(failures),
            "error": "section_failure" if failures else None,
            "retry_required": bool(failures),
        }

    def player_correction_for_request(self, request_id: str | None) -> dict[str, Any] | None:
        if not request_id or self.settings.rp_contract_revision < 9:
            return None
        matches = [
            item
            for item in self.store.player_correction_records()
            if item.get("request_id") == request_id
            and item.get("status") == "active"
            and item.get("target_kind") in {"memory", "raw"}
        ]
        return matches[-1] if matches else None

    async def update_player_correction(
        self,
        artifact: dict[str, Any],
        *,
        fail_open: bool,
        request_id: str | None,
    ) -> dict[str, Any]:
        """Run exactly one affected section, then absorb only after both gates pass."""

        previous = self.store.effective_rp_story_memory()
        memory = normalize_sectioned_story_memory(
            previous.get("memory") if previous else None,
            self.settings.rp_story_memory_max_chars,
        )
        section_key = str(artifact.get("section_key") or "")
        field = str(artifact.get("field") or "")
        if section_key not in RP_MEMORY_SECTION_KEYS or field not in STORY_MEMORY_SECTION_FIELDS[section_key]:
            raise ValueError("player correction targets an invalid story-memory section")
        correction = artifact.get("story_memory_correction")
        if not isinstance(correction, dict):
            raise ValueError("player correction is missing its typed story-memory correction")
        source_turn_id = int(artifact.get("source_turn_id") or 0)
        target_turn_id = int(artifact.get("target_turn_id") or 0)
        if source_turn_id <= 0:
            raise ValueError("player correction is missing its GM turn provenance")

        if artifact.get("target_kind") == "raw":
            synthetic = artifact.get("synthetic_before_fact")
            if not isinstance(synthetic, dict):
                raise ValueError("RAW player correction is missing its synthetic target")
            fact_id = str(synthetic.get("fact_id") or "")
            if not any(str(item.get("fact_id") or "") == fact_id for item in memory[field]):
                if len(memory[field]) >= STORY_FIELD_LIMITS[field]:
                    removable = next(
                        (
                            index
                            for index in range(len(memory[field]) - 1, -1, -1)
                            if story_item_is_safely_removable(memory[field][index])
                        ),
                        None,
                    )
                    if removable is None:
                        raise ValueError(
                            f"story-memory field is full and has no weak slot for RAW correction: {field}"
                        )
                    memory[field].pop(removable)
                memory[field].append(
                    {
                        "fact_id": fact_id,
                        "text": str(synthetic.get("text") or "").strip(),
                        "status": "active",
                        "authority": "narrator",
                        "source_turn_ids": [target_turn_id] if target_turn_id > 0 else [],
                    }
                )

        validated = validate_story_memory_corrections(
            {"memory": memory},
            [correction],
            self.settings.rp_story_memory_max_chars,
        )
        update_id = f"smc:{str(artifact.get('correction_id') or '')}"
        try:
            generated = await self.generate_player_correction_section(
                section_key,
                memory,
                artifact,
                request_id=request_id,
                update_id=update_id,
            )
            target_turn = self.store.turn_record(target_turn_id) if target_turn_id > 0 else None
            contributing = (
                [target_turn_id]
                if target_turn is not None and not target_turn.get("excluded_from_memory")
                else []
            )
            merged = merge_story_memory_section(
                memory,
                generated["section"],
                section_key,
                contributing,
                self.settings.rp_story_memory_max_chars,
            )
            merged = apply_validated_story_memory_corrections(
                merged,
                validated,
                source_turn_id,
                self.settings.rp_story_memory_max_chars,
                authority="user",
            )
            statuses = normalize_section_status(merged)
            statuses[section_key] = {
                "coverage": max(int(statuses[section_key]["coverage"]), target_turn_id),
                "status": "fresh",
            }
            merged["schema_version"] = SECTIONED_STORY_MEMORY_SCHEMA
            merged["section_status"] = statuses
            merged["observed_through_turn_id"] = max(
                int(merged.get("observed_through_turn_id") or 0),
                target_turn_id,
            )
            merged["last_update_request_id"] = request_id
            merged = normalize_sectioned_story_memory(
                merged,
                self.settings.rp_story_memory_max_chars,
            )
            coverage_values = [int(statuses[key]["coverage"]) for key in RP_MEMORY_SECTION_KEYS]
            outer_coverage = max(coverage_values, default=0)
            previous_from = int(previous.get("from_turn_id") or 0) if previous else 0
            snapshot = self.store.record_rp_story_memory(
                from_turn_id=previous_from or target_turn_id,
                to_turn_id=outer_coverage,
                state_version=self.store.current_version() or 1,
                memory=merged,
                model=str(generated.get("model") or self.settings.rp_story_memory_model),
                contributing_turn_ids=contributing,
                base_snapshot_id=int(previous["id"]) if previous else None,
                update_id=update_id,
                allow_same_coverage=True,
            )
            if snapshot is None:
                raise RuntimeError("player-correction story-memory plan became stale")
            persisted_memory = normalize_sectioned_story_memory(
                snapshot.get("memory"),
                self.settings.rp_story_memory_max_chars,
            )
            persisted_statuses = normalize_section_status(persisted_memory)
            applied = story_memory_correction_already_applied(
                persisted_memory,
                correction,
                source_turn_id,
                "user",
            )
            covered = int(persisted_statuses[section_key]["coverage"]) >= target_turn_id
            if applied and covered:
                self.store.mark_player_correction_absorbed(
                    str(artifact.get("correction_id") or ""),
                    snapshot_id=int(snapshot["id"]),
                    section_key=section_key,
                    coverage=int(persisted_statuses[section_key]["coverage"]),
                    request_id=request_id,
                )
            self.store.audit(
                "rp_story_memory_player_correction_updated",
                {
                    "snapshot_id": snapshot["id"],
                    "correction_id": artifact.get("correction_id"),
                    "section_key": section_key,
                    "section_coverage": persisted_statuses[section_key]["coverage"],
                    "authority_user_persisted": applied,
                    "absorbed": bool(applied and covered),
                },
                request_id,
            )
            return {
                "generated": True,
                "reason": "player_correction",
                "story_memory": snapshot,
                "stats": self.stats(),
                "failed_sections": [],
                "retry_required": False,
            }
        except Exception as exc:  # noqa: BLE001 - overlay keeps the correction authoritative
            logger.warning(
                "rp_story_memory_player_correction_failed campaign_id=%s correction_id=%s error=%s",
                self.store.campaign_id,
                artifact.get("correction_id"),
                exc,
            )
            self.store.audit(
                "rp_story_memory_player_correction_failed",
                {
                    "correction_id": artifact.get("correction_id"),
                    "section_key": section_key,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                request_id,
            )
            if fail_open:
                return {
                    "generated": False,
                    "reason": "player_correction_failed",
                    "story_memory": previous,
                    "stats": self.stats(),
                    "error": "player_correction_failed",
                    "retry_required": True,
                }
            raise

    async def generate_player_correction_section(
        self,
        section_key: str,
        memory: dict[str, Any],
        artifact: dict[str, Any],
        *,
        request_id: str | None,
        update_id: str,
    ) -> dict[str, Any]:
        """Make one structural memory call for the affected correction section."""

        runtime = self.service_settings()
        fields = STORY_MEMORY_SECTION_FIELDS[section_key]
        if runtime.openrouter_api_base.startswith("mock://"):
            return {
                "section": {
                    field: json.loads(json.dumps(memory.get(field), ensure_ascii=False))
                    for field in fields
                },
                "model": self.settings.rp_story_memory_model,
            }
        target_turn_id = int(artifact.get("target_turn_id") or 0)
        target_turn = self.store.turn_record(target_turn_id) if target_turn_id > 0 else None
        target_raw = None
        if target_turn is not None:
            target_raw = {
                "turn_id": target_turn_id,
                "player": str(target_turn.get("player_message") or "")[:1_200],
                "narrator": str(target_turn.get("narrative_response") or "")[:1_800],
            }
        payload = {
            "model": self.settings.rp_story_memory_model,
            "stream": False,
            "temperature": 0.1,
            "response_format": story_memory_response_format(section_key),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Пересобери только указанную секцию памяти с учётом типизированного "
                        "исправления игрока. Верни JSON ровно с полями секции. Пустые значения "
                        "валидны. Модель назначает новым фактам только authority inference; "
                        "authority user и terminal transitions применит Gateway."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "section_key": section_key,
                            "previous_section": bounded_story_memory_section(memory, fields, 6_000),
                            "player_correction": {
                                key: artifact.get(key)
                                for key in (
                                    "target_kind",
                                    "target_turn_id",
                                    "field",
                                    "action",
                                    "before",
                                    "after",
                                )
                            },
                            "target_raw": target_raw,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
        }
        prompt = service_prompt_text(payload)
        if len(prompt) > STORY_MEMORY_SECTION_INPUT_CHARS:
            raise ValueError("player-correction section prompt exceeds 20000 characters")
        completion = await ServiceModelClient(runtime).complete(
            role="rp_story_memory_section",
            provider=self.settings.rp_story_memory_provider,
            model=self.settings.rp_story_memory_model,
            party_id=self.store.campaign_id,
            turn_id=int(artifact.get("source_turn_id") or 0),
            request_id=request_id,
            party_turn=artifact.get("party_turn"),
            attempt=1,
            section_key=section_key,
            update_id=update_id,
            prompt=prompt,
            payload=payload,
        )
        if completion_finish_reason(completion.data) == "length":
            raise ValueError("player-correction section response finish_reason=length")
        return {
            "section": self.parse_section(
                completion_text(completion.data),
                section_key,
                memory,
            ),
            "model": completion.data.get("model") or self.settings.rp_story_memory_model,
        }

    async def generate_sections(
        self,
        plan: RPStoryMemorySectionPlan,
        section_turns: dict[str, list[dict[str, Any]]],
        memory: dict[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        """Generate all five sections in one normal OpenRouter completion."""

        runtime = self.service_settings()
        if runtime.openrouter_api_base.startswith("mock://"):
            return {
                "sections": {
                    section_key: self.mock_section(
                        section_key,
                        section_turns.get(section_key, []),
                        memory,
                    )
                    for section_key in RP_MEMORY_SECTION_KEYS
                },
                "errors": {},
                "model": plan.model,
                "turns": {
                    section_key: list(section_turns.get(section_key, []))
                    for section_key in RP_MEMORY_SECTION_KEYS
                },
            }
        payload, included_turns = self.sections_payload(
            plan,
            section_turns,
            memory,
        )
        prompt = service_prompt_text(payload)
        if len(prompt) > STORY_MEMORY_SECTION_INPUT_CHARS:
            raise ValueError("RP story-memory combined prompt exceeds 20000 characters")
        last_turn = max(
            (
                turn
                for turns in included_turns.values()
                for turn in turns
            ),
            key=lambda turn: int(turn["id"]),
        )
        completion = await ServiceModelClient(runtime).complete(
            role="rp_story_memory_sections",
            provider=self.settings.rp_story_memory_provider,
            model=plan.model,
            party_id=self.store.campaign_id,
            turn_id=int(last_turn["id"]),
            request_id=request_id,
            party_turn=last_turn.get("party_turn"),
            section_key="all",
            update_id=plan.update_id,
            prompt=prompt,
            payload=payload,
        )
        if completion_finish_reason(completion.data) == "length":
            error = "combined response finish_reason=length"
            return {
                "sections": {},
                "errors": {section_key: error for section_key in RP_MEMORY_SECTION_KEYS},
                "model": completion.data.get("model") or plan.model,
                "turns": included_turns,
            }
        sections, errors = self.parse_sections(
            completion_text(completion.data),
            memory,
        )
        return {
            "sections": sections,
            "errors": errors,
            "model": completion.data.get("model") or plan.model,
            "turns": included_turns,
        }

    async def generate_section(
        self,
        plan: RPStoryMemorySectionPlan,
        section_key: str,
        turns: list[dict[str, Any]],
        memory: dict[str, Any],
        *,
        request_id: str | None,
    ) -> dict[str, Any]:
        runtime = self.service_settings()
        if runtime.openrouter_api_base.startswith("mock://"):
            return {
                "section": self.mock_section(section_key, turns, memory),
                "model": plan.model,
                "turns": turns,
            }
        payload, included_turns = self.section_payload(plan, section_key, turns, memory)
        prompt = service_prompt_text(payload)
        if len(prompt) > STORY_MEMORY_SECTION_INPUT_CHARS:
            raise ValueError("RP story-memory section prompt exceeds 20000 characters")
        completion = await ServiceModelClient(runtime).complete(
            role="rp_story_memory_section",
            provider=self.settings.rp_story_memory_provider,
            model=plan.model,
            party_id=self.store.campaign_id,
            turn_id=int(included_turns[-1]["id"]),
            request_id=request_id,
            party_turn=included_turns[-1].get("party_turn"),
            section_key=section_key,
            update_id=plan.update_id,
            prompt=prompt,
            payload=payload,
        )
        if completion_finish_reason(completion.data) == "length":
            raise ValueError("RP story-memory section response finish_reason=length")
        return {
            "section": self.parse_section(
                completion_text(completion.data),
                section_key,
                memory,
            ),
            "model": completion.data.get("model") or plan.model,
            "turns": included_turns,
        }

    def sections_payload(
        self,
        plan: RPStoryMemorySectionPlan,
        section_turns: dict[str, list[dict[str, Any]]],
        memory: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        included = {
            section_key: list(section_turns.get(section_key, []))
            for section_key in RP_MEMORY_SECTION_KEYS
        }
        if not any(included.values()):
            raise ValueError("combined story-memory update has no turns")
        schema_text = "; ".join(
            f"{section_key}: {', '.join(STORY_MEMORY_SECTION_FIELDS[section_key])}"
            for section_key in RP_MEMORY_SECTION_KEYS
        )
        system_message = {
            "role": "system",
            "content": (
                "Обнови пять секций памяти ролевой партии одним JSON-объектом. "
                "Верхний уровень содержит ровно ключи situation, threads, characters, "
                "assets_and_rules, chronology_and_hooks. Поля секций: "
                f"{schema_text}. current_situation — объект факта или null; остальные поля — "
                "массивы фактов. Каждый факт содержит ровно fact_id, text, status, authority, "
                "source_turn_ids. Сохраняй прежний fact_id того же факта. Пустые массивы и null "
                "валидны и означают, что в секции нечего менять; не выдумывай содержимое ради "
                "непустого ответа. Учитывай только подтверждённое ответом нарратора, не раскрывай "
                "неизвестные игроку секреты. Новый факт имеет authority inference; модель не "
                "назначает authority user, state или worldpack. Пиши на языке партии."
            ),
        }

        while True:
            for previous_limit in (1_600, 800, 0):
                unique_turns = {
                    int(turn["id"]): turn
                    for turns in included.values()
                    for turn in turns
                }
                ordered_turns = [unique_turns[key] for key in sorted(unique_turns)]
                context = {
                    "previous_sections": {
                        section_key: bounded_story_memory_section(
                            memory,
                            STORY_MEMORY_SECTION_FIELDS[section_key],
                            previous_limit,
                        )
                        for section_key in RP_MEMORY_SECTION_KEYS
                    },
                    "section_turn_ids": {
                        section_key: [int(turn["id"]) for turn in included[section_key]]
                        for section_key in RP_MEMORY_SECTION_KEYS
                    },
                    "new_confirmed_turns": self.compact_section_turns(ordered_turns),
                    "requested_coverage": {
                        section_key: {
                            "from_turn_id": (
                                int(included[section_key][0]["id"])
                                if included[section_key]
                                else int(plan.section_coverage[section_key])
                            ),
                            "to_turn_id": (
                                int(included[section_key][-1]["id"])
                                if included[section_key]
                                else int(plan.section_coverage[section_key])
                            ),
                            "state_version": plan.state_version,
                        }
                        for section_key in RP_MEMORY_SECTION_KEYS
                    },
                }
                payload = {
                    "model": plan.model,
                    "stream": False,
                    "temperature": 0.1,
                    "response_format": story_memory_response_format(),
                    "messages": [
                        system_message,
                        {
                            "role": "user",
                            "content": json.dumps(
                                context,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                }
                if len(service_prompt_text(payload)) <= STORY_MEMORY_SECTION_INPUT_CHARS:
                    return payload, included

            shrinkable = [
                section_key
                for section_key in RP_MEMORY_SECTION_KEYS
                if len(included[section_key]) > 1
            ]
            if not shrinkable:
                break
            section_to_shrink = max(
                shrinkable,
                key=lambda section_key: int(included[section_key][-1]["id"]),
            )
            included[section_to_shrink].pop()
        raise ValueError("one complete RP turn per section cannot fit the combined input contract")

    def section_payload(
        self,
        plan: RPStoryMemorySectionPlan,
        section_key: str,
        turns: list[dict[str, Any]],
        memory: dict[str, Any],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        fields = STORY_MEMORY_SECTION_FIELDS[section_key]
        system_message = {
            "role": "system",
            "content": (
                "Обнови только одну секцию памяти ролевой партии. Верни только JSON-объект "
                f"ровно с полями: {', '.join(fields)}. Сохраняй важные прежние факты, "
                "учитывай только подтверждённое ответом нарратора, не раскрывай неизвестные "
                "игроку секреты. Каждый новый факт является inference; модель не назначает "
                "authority user, state или worldpack. Для элементов сохраняй прежний fact_id, "
                "если обновляешь тот же факт. Пустые массивы и null валидны: не добавляй факты "
                "ради непустого ответа. Каждый факт содержит ровно fact_id, text, status, "
                "authority, source_turn_ids. Пиши на языке партии."
            ),
        }
        for previous_limit in (4_000, 2_000, 0):
            included_turns = list(turns)
            while included_turns:
                context = {
                    "section_key": section_key,
                    "previous_section": bounded_story_memory_section(
                        memory,
                        fields,
                        previous_limit,
                    ),
                    "new_confirmed_turns": self.compact_section_turns(included_turns),
                    "requested_coverage": {
                        "from_turn_id": int(included_turns[0]["id"]),
                        "to_turn_id": int(included_turns[-1]["id"]),
                        "state_version": plan.state_version,
                    },
                }
                payload = {
                    "model": plan.model,
                    "stream": False,
                    "temperature": 0.1,
                    "response_format": story_memory_response_format(section_key),
                    "messages": [
                        system_message,
                        {
                            "role": "user",
                            "content": json.dumps(
                                context,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                    ],
                }
                if len(service_prompt_text(payload)) <= STORY_MEMORY_SECTION_INPUT_CHARS:
                    return payload, included_turns
                included_turns.pop()
        raise ValueError("one complete RP turn cannot fit the 20000 character section input contract")

    def compact_section_turns(
        self,
        turns: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        for turn in turns:
            rendered = rp_turn_messages(turn)
            compacted.append(
                {
                    "turn_id": int(turn["id"]),
                    "player": next(
                        (content for role, content in rendered if role == "user"),
                        "",
                    ),
                    "narrator": next(
                        (content for role, content in rendered if role == "assistant"),
                        "",
                    ),
                    "story_memory_canonical": not bool(turn.get("noncanonical_safe_fallback")),
                }
            )
        return compacted

    def parse_sections(
        self,
        content: str,
        memory: dict[str, Any],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        try:
            decoded = json.loads(strip_code_fence(content.strip()))
        except json.JSONDecodeError as exc:
            error = f"invalid combined JSON: {exc.msg}"
            return {}, {section_key: error for section_key in RP_MEMORY_SECTION_KEYS}
        if not isinstance(decoded, dict):
            error = "combined story-memory response is not an object"
            return {}, {section_key: error for section_key in RP_MEMORY_SECTION_KEYS}
        sections: dict[str, dict[str, Any]] = {}
        errors: dict[str, str] = {}
        for section_key in RP_MEMORY_SECTION_KEYS:
            if section_key not in decoded:
                errors[section_key] = "combined response omitted the section"
                continue
            try:
                sections[section_key] = self.parse_section_value(
                    decoded[section_key],
                    section_key,
                    memory,
                )
            except ValueError as exc:
                errors[section_key] = str(exc)
        return sections, errors

    def parse_section(
        self,
        content: str,
        section_key: str,
        memory: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            decoded = json.loads(strip_code_fence(content.strip()))
        except json.JSONDecodeError as exc:
            raise ValueError("service model returned invalid RP story-memory section JSON") from exc
        return self.parse_section_value(decoded, section_key, memory or empty_story_memory())

    def parse_section_value(
        self,
        decoded: Any,
        section_key: str,
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(decoded, dict):
            raise ValueError("service model returned non-object RP story-memory section")
        fields = STORY_MEMORY_SECTION_FIELDS[section_key]
        missing = [field for field in fields if field not in decoded]
        if missing:
            raise ValueError("service model omitted RP story-memory section fields: " + ", ".join(missing))
        unexpected = [field for field in decoded if field not in fields]
        if unexpected:
            raise ValueError(
                "service model added unknown RP story-memory section fields: "
                + ", ".join(unexpected)
            )
        previous = normalize_sectioned_story_memory(
            memory,
            self.settings.rp_story_memory_max_chars,
        )
        previous_ids_by_text: dict[str, str] = {}
        for field in fields:
            previous_value = previous.get(field)
            previous_items = (
                [previous_value]
                if field == "current_situation" and isinstance(previous_value, dict)
                else previous_value
                if isinstance(previous_value, list)
                else []
            )
            for item in previous_items:
                previous_ids_by_text[story_fact_fingerprint(str(item.get("text") or ""))] = str(
                    item.get("fact_id") or ""
                )

        candidate = empty_story_memory()
        seen_fact_ids: set[str] = set()
        for field in fields:
            raw_value = decoded[field]
            if field == "current_situation":
                if raw_value is None:
                    candidate[field] = None
                    continue
                raw_items = [raw_value]
                text_limit = 2_000
            else:
                if not isinstance(raw_value, list):
                    raise ValueError(f"service model field {field} must be an array")
                if len(raw_value) > STORY_FIELD_LIMITS[field]:
                    raise ValueError(f"service model field {field} exceeds its item limit")
                raw_items = raw_value
                text_limit = 600
            validated_items = []
            for raw_item in raw_items:
                validated_items.append(
                    validate_story_memory_item_structure(
                        raw_item,
                        text_limit=text_limit,
                        previous_ids_by_text=previous_ids_by_text,
                        seen_fact_ids=seen_fact_ids,
                    )
                )
            candidate[field] = (
                validated_items[0]
                if field == "current_situation" and validated_items
                else None
                if field == "current_situation"
                else validated_items
            )
        normalized = normalize_story_memory(candidate, self.settings.rp_story_memory_max_chars)
        return {field: normalized[field] for field in fields}

    def mock_section(
        self,
        section_key: str,
        turns: list[dict[str, Any]],
        memory: dict[str, Any],
    ) -> dict[str, Any]:
        fields = STORY_MEMORY_SECTION_FIELDS[section_key]
        result = {field: json.loads(json.dumps(memory.get(field), ensure_ascii=False)) for field in fields}
        if section_key == "situation":
            canonical = [turn for turn in turns if not turn.get("noncanonical_safe_fallback")]
            if canonical:
                result["current_situation"] = {
                    "text": clip(canonical[-1]["narrative_response"], 1200),
                    "status": "active",
                    "authority": "narrator",
                    "source_turn_ids": [int(canonical[-1]["id"])],
                }
        elif section_key == "chronology_and_hooks":
            chronology = list(result.get("chronology") or [])
            for turn in turns:
                if turn.get("noncanonical_safe_fallback"):
                    continue
                chronology.append(
                    {
                        "text": f"Ход {turn['id']}: {clip(turn['narrative_response'], 500)}",
                        "status": "active",
                        "authority": "narrator",
                        "source_turn_ids": [int(turn["id"])],
                    }
                )
            result["chronology"] = chronology
        return result

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
        if self.revision_eight:
            return eligible_rp_turns(turns)
        return unresolved_noncanonical_fallback_turns(self.store.get_state(), turns)

    def service_settings(self) -> Settings:
        if self.revision_eight:
            if self.settings.rp_story_memory_provider != "openrouter":
                raise ValueError("revision-8 story memory requires the explicit openrouter provider")
            return replace(
                self.settings,
                llm_provider="openrouter",
                llm_api_base=self.settings.openrouter_api_base,
                llm_api_key=self.settings.service_openrouter_api_key,
                narrative_model=self.settings.rp_story_memory_model,
                intent_model=self.settings.rp_story_memory_model,
                validator_model=self.settings.rp_story_memory_model,
                llm_fallback_models=(),
                llm_disabled_models=(),
            )
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


def empty_sectioned_story_memory() -> dict[str, Any]:
    memory = empty_story_memory()
    memory["schema_version"] = SECTIONED_STORY_MEMORY_SCHEMA
    memory["section_status"] = {
        section_key: {"coverage": 0, "status": "failed"}
        for section_key in RP_MEMORY_SECTION_KEYS
    }
    memory["observed_through_turn_id"] = 0
    memory["last_update_request_id"] = None
    return memory


def normalize_section_status(value: Any) -> dict[str, dict[str, Any]]:
    source = value if isinstance(value, dict) else {}
    raw_status = source.get("section_status")
    raw_status = raw_status if isinstance(raw_status, dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for section_key in RP_MEMORY_SECTION_KEYS:
        item = raw_status.get(section_key)
        item = item if isinstance(item, dict) else {}
        try:
            coverage = max(int(item.get("coverage") or 0), 0)
        except (TypeError, ValueError):
            coverage = 0
        status = str(item.get("status") or "failed")
        if status not in {"fresh", "stale", "failed"}:
            status = "failed"
        normalized[section_key] = {"coverage": coverage, "status": status}
    return normalized


def normalize_sectioned_story_memory(value: Any, max_chars: int) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    normalized = normalize_story_memory(source, max_chars)
    normalized["schema_version"] = SECTIONED_STORY_MEMORY_SCHEMA
    normalized["section_status"] = normalize_section_status(source)
    try:
        normalized["observed_through_turn_id"] = max(
            int(source.get("observed_through_turn_id") or 0),
            0,
        )
    except (TypeError, ValueError):
        normalized["observed_through_turn_id"] = 0
    normalized["last_update_request_id"] = (
        str(source.get("last_update_request_id"))
        if source.get("last_update_request_id")
        else None
    )
    for field in STORY_LIST_FIELDS:
        for item in normalized[field]:
            if item.get("authority") == "user_correction":
                item["authority"] = "user"
    current = normalized.get("current_situation")
    if isinstance(current, dict) and current.get("authority") == "user_correction":
        current["authority"] = "user"
    return fit_story_memory(normalized, max(max_chars, 1))


def bounded_story_memory_section(
    memory: dict[str, Any],
    fields: tuple[str, ...],
    max_chars: int,
) -> dict[str, Any]:
    """Select whole prior facts; reconciliation keeps facts omitted for input budget."""

    if max_chars <= 0:
        return {field: None if field == "current_situation" else [] for field in fields}
    result: dict[str, Any] = {}
    for field in fields:
        value = memory.get(field)
        if field == "current_situation":
            candidate = dict(value) if isinstance(value, dict) else None
            trial = {**result, field: candidate}
            result[field] = candidate if len(json.dumps(trial, ensure_ascii=False)) <= max_chars else None
            continue
        items = value if isinstance(value, list) else []
        selected: list[Any] = []
        candidates = list(reversed(items)) if field in {"chronology", "resolved_threads"} else items
        for item in candidates:
            trial_items = [*selected, item]
            trial = {**result, field: trial_items}
            if len(json.dumps(trial, ensure_ascii=False)) > max_chars:
                continue
            selected = trial_items
        if field in {"chronology", "resolved_threads"}:
            selected.reverse()
        result[field] = selected
    for field in fields:
        result.setdefault(field, None if field == "current_situation" else [])
    return result


def merge_story_memory_section(
    previous_value: Any,
    proposed_section: dict[str, Any],
    section_key: str,
    source_turn_ids: list[int],
    max_chars: int,
) -> dict[str, Any]:
    previous = normalize_sectioned_story_memory(previous_value, max_chars)
    proposed = empty_story_memory()
    for field in STORY_MEMORY_SECTION_FIELDS[section_key]:
        proposed[field] = proposed_section.get(field)
    candidate = service_story_memory_candidate(
        previous,
        proposed,
        source_turn_ids,
        max_chars,
    )
    merged = json.loads(json.dumps(previous, ensure_ascii=False))
    for field in STORY_MEMORY_SECTION_FIELDS[section_key]:
        if field == "current_situation":
            previous_items = [previous[field]] if previous.get(field) else []
            candidate_items = [candidate[field]] if candidate.get(field) else []
            items = reconcile_story_items(previous_items, candidate_items)
            active = [item for item in items if item.get("status") == "active"]
            merged[field] = (active or items or [None])[-1]
        else:
            merged[field] = reconcile_story_items(previous[field], candidate[field])
    return normalize_sectioned_story_memory(merged, max_chars)


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
    if source.get("schema_version") == SECTIONED_STORY_MEMORY_SCHEMA or isinstance(
        source.get("section_status"),
        dict,
    ):
        normalized["schema_version"] = SECTIONED_STORY_MEMORY_SCHEMA
        normalized["section_status"] = normalize_section_status(source)
        normalized["observed_through_turn_id"] = source.get("observed_through_turn_id", 0)
        normalized["last_update_request_id"] = source.get("last_update_request_id")
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
    *,
    authority: str = "user_correction",
) -> dict[str, Any]:
    memory = normalize_story_memory(memory_value, max_chars)
    for turn in turns:
        corrections = turn_story_memory_corrections(turn)
        if not corrections:
            continue
        source_turn_id = int(turn["id"])
        corrections = [
            correction
            for correction in corrections
            if not story_memory_correction_already_applied(
                memory,
                correction,
                source_turn_id,
                authority,
            )
        ]
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
            source_turn_id,
            max_chars,
            authority=authority,
        )
    return memory


def story_memory_correction_already_applied(
    memory: dict[str, Any],
    correction: dict[str, Any],
    source_turn_id: int,
    authority: str,
) -> bool:
    """Recognize the exact persisted result so a durable job retry is idempotent."""

    field = str(correction.get("field") or "")
    fact_id = str(correction.get("fact_id") or "")
    action = str(correction.get("action") or "")
    if field not in STORY_LIST_FIELDS or action not in {"retract", "replace"}:
        return False
    target = next(
        (
            item
            for item in memory[field]
            if str(item.get("fact_id") or "") == fact_id
        ),
        None,
    )
    expected_status = "retracted" if action == "retract" else "superseded"
    if not (
        isinstance(target, dict)
        and target.get("status") == expected_status
        and target.get("authority") == authority
        and target.get("source_turn_ids") == [source_turn_id]
    ):
        return False
    if action == "retract":
        return True
    replacement_text = str(correction.get("replacement_text") or "").strip()
    replacement_id = story_fact_id(None, replacement_text) if replacement_text else ""
    return any(
        str(item.get("fact_id") or "") == replacement_id
        and item.get("status") == "active"
        and item.get("authority") == authority
        and item.get("source_turn_ids") == [source_turn_id]
        for item in memory[field]
    )


def apply_validated_story_memory_corrections(
    memory_value: Any,
    corrections: list[dict[str, str]],
    source_turn_id: int | None,
    max_chars: int,
    *,
    authority: str = "user_correction",
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
            "authority": authority,
            "source_turn_ids": source_turn_ids,
        }
        if correction["action"] == "replace":
            replacement_text = correction["replacement_text"]
            memory[field].append(
                {
                    "fact_id": story_fact_id(None, replacement_text),
                    "text": replacement_text,
                    "status": "active",
                    "authority": authority,
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
    stronger = {"worldpack", "state", "user", "user_correction"}
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
    if rp_contract_revision >= 8:
        return sectioned_story_memory_prompt_text(snapshot, max_chars)
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


def sectioned_story_memory_prompt_text(snapshot: dict[str, Any], max_chars: int) -> str:
    memory = normalize_sectioned_story_memory(snapshot.get("memory"), max_chars)
    statuses = normalize_section_status(memory)
    safe_coverage = min(
        int(statuses[key]["coverage"])
        for key in RP_MEMORY_SECTION_KEYS
    )
    status_labels = {"fresh": "актуальна", "stale": "устарела", "failed": "не создана"}
    section_titles = {
        "situation": "СИТУАЦИЯ И КАНОН",
        "threads": "СЮЖЕТНЫЕ ЛИНИИ",
        "characters": "ПЕРСОНАЖИ",
        "assets_and_rules": "АКТИВЫ, ПРАВИЛА И СПОСОБНОСТИ",
        "chronology_and_hooks": "ХРОНОЛОГИЯ И ЗАЦЕПКИ",
    }
    lines = [f"безопасно покрыто до хода {safe_coverage}"]
    for section_key in RP_MEMORY_SECTION_KEYS:
        item = statuses[section_key]
        title = section_titles[section_key]
        mandatory = (
            f"## {title} — {status_labels[item['status']]}, "
            f"покрытие до хода {item['coverage']}"
        )
        candidate = [*lines, "", mandatory]
        if len("\n".join(candidate)) <= max_chars:
            lines = candidate
        values: list[str] = []
        for field in STORY_MEMORY_SECTION_FIELDS[section_key]:
            value = memory.get(field)
            if field == "current_situation":
                field_items = [value] if isinstance(value, dict) else []
            else:
                field_items = value if isinstance(value, list) else []
            texts = active_story_texts(field_items, include_legacy_projection=False)
            if field == "chronology":
                texts = texts[-24:]
            elif field == "resolved_threads":
                texts = texts[-16:]
            values.extend(texts)
        if not values and item["status"] in {"stale", "failed"}:
            values = ["Секция недоступна; опирайся на дословную историю ниже."]
        for value in values:
            candidate = [*lines, f"- {value}"]
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
            "user",
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


def validate_story_memory_item_structure(
    value: Any,
    *,
    text_limit: int,
    previous_ids_by_text: dict[str, str],
    seen_fact_ids: set[str],
) -> dict[str, Any]:
    """Validate shape only; empty sections remain valid and semantic judging stays out."""

    if not isinstance(value, dict):
        raise ValueError("story-memory fact must be an object")
    missing = sorted(STORY_MEMORY_ITEM_KEYS - set(value))
    unexpected = sorted(set(value) - STORY_MEMORY_ITEM_KEYS)
    if missing:
        raise ValueError("story-memory fact omitted fields: " + ", ".join(missing))
    if unexpected:
        raise ValueError("story-memory fact added fields: " + ", ".join(unexpected))

    fact_id = value["fact_id"]
    if not isinstance(fact_id, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9_.:-]{7,79}",
        fact_id,
    ):
        raise ValueError("story-memory fact_id does not match the stable ID schema")
    if fact_id in seen_fact_ids:
        raise ValueError("story-memory response repeated fact_id " + fact_id)

    text = value["text"]
    if not isinstance(text, str) or not text.strip():
        raise ValueError("story-memory fact text must be a non-empty string")
    if len(text) > text_limit:
        raise ValueError("story-memory fact text exceeds the field limit")
    prior_id = previous_ids_by_text.get(story_fact_fingerprint(text))
    if prior_id and prior_id != fact_id:
        raise ValueError("story-memory response changed an existing fact_id")

    status = value["status"]
    if status not in STORY_MEMORY_ITEM_STATUSES:
        raise ValueError("story-memory fact status is outside the schema")
    authority = value["authority"]
    if authority not in STORY_MEMORY_ITEM_AUTHORITIES:
        raise ValueError("story-memory fact authority is outside the schema")

    source_turn_ids = value["source_turn_ids"]
    if not isinstance(source_turn_ids, list) or len(source_turn_ids) > 20:
        raise ValueError("story-memory source_turn_ids must be an array of at most 20 IDs")
    normalized_turn_ids: list[int] = []
    for turn_id in source_turn_ids:
        if isinstance(turn_id, bool) or not isinstance(turn_id, int) or turn_id < 0:
            raise ValueError("story-memory source_turn_ids must contain non-negative integers")
        if turn_id in normalized_turn_ids:
            raise ValueError("story-memory source_turn_ids must not contain duplicates")
        normalized_turn_ids.append(turn_id)

    seen_fact_ids.add(fact_id)
    return {
        "fact_id": fact_id,
        "text": text,
        "status": status,
        "authority": authority,
        "source_turn_ids": normalized_turn_ids,
    }


def completion_finish_reason(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    return str(choices[0].get("finish_reason") or "").strip().casefold()


def completion_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("content") or "")


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
