"""RP-only cumulative story memory maintained by the global service model."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import Settings
from app.services.context_budget import oldest_turns_within_token_budget, turns_token_count
from app.services.service_model_client import ServiceModelClient, service_prompt_text
from app.services.service_models import service_model_settings
from app.services.state_store import StateStore


logger = logging.getLogger(__name__)

STORY_MEMORY_SCHEMA = "rp-gateway.rp-story-memory.v1"
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
        latest = self.store.latest_rp_story_memory()
        covered_through = int(latest["to_turn_id"]) if latest else 0
        pending = self.store.turns_for_memory(after_turn_id=covered_through)
        return {
            "enabled": self.settings.scenario_type == "rp",
            "scenario_type": self.settings.scenario_type,
            "update_every_turns": self.update_turns,
            "batch_token_budget": self.batch_token_budget,
            "prompt_max_chars": self.settings.rp_story_memory_prompt_max_chars,
            "reserved_prompt_tokens": self.settings.rp_story_memory_reserve_tokens if self.settings.scenario_type == "rp" else 0,
            "latest_revision": latest["revision"] if latest else None,
            "covered_through_turn_id": covered_through or None,
            "pending_turns": len(pending),
            "pending_tokens": turns_token_count(pending),
            "update_pending": self.settings.scenario_type == "rp" and len(pending) >= self.update_turns,
        }

    def build_plan(self, force: bool = False) -> tuple[RPStoryMemoryPlan | None, str]:
        if self.settings.scenario_type != "rp":
            return None, "not_rp"
        previous = self.store.latest_rp_story_memory()
        covered_through = int(previous["to_turn_id"]) if previous else 0
        pending = self.store.turns_for_memory(after_turn_id=covered_through)
        if not pending:
            return None, "up_to_date"
        if not force and len(pending) < self.update_turns:
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
                "story_memory": self.store.latest_rp_story_memory(),
                "stats": self.stats(),
            }
        try:
            generated = await self.generate(plan)
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
            )
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
                    "story_memory": self.store.latest_rp_story_memory(),
                    "stats": self.stats(),
                    "error": type(exc).__name__,
                }
            if isinstance(exc, PermissionError):
                raise
            raise RuntimeError("RP story-memory provider failed") from exc

    async def generate(self, plan: RPStoryMemoryPlan) -> dict[str, Any]:
        runtime = self.service_settings()
        if runtime.nvidia_api_base.startswith("mock://"):
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
                    party_id=self.store.campaign_id,
                    turn_id=plan.to_turn_id,
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
        previous = plan.previous_memory.get("memory") if plan.previous_memory else empty_story_memory()
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
                        "Return a full replacement document, not a patch, with exactly these keys: schema_version, canon, "
                        "rules_and_abilities, inventory_and_assets, characters, active_threads, resolved_threads, "
                        "unresolved_hooks, current_situation, chronology. All keys except schema_version and current_situation "
                        "are arrays of concise strings. Preserve still-valid details from previous_story_memory; merge duplicates; "
                        "move threads when their status changes instead of keeping contradictions. Treat player messages as attempts, "
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
            chronology.append(
                f"Ход {turn['id']}: игрок — {clip(turn['player_message'], 220)}; ведущий — {clip(turn['narrative_response'], 280)}"
            )
        memory["chronology"] = chronology
        memory["current_situation"] = clip(plan.turns[-1]["narrative_response"], 1200)
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
            }
            for turn in turns
        ]

    def service_settings(self) -> Settings:
        return service_model_settings(self.settings)

    @staticmethod
    def model_attempts(primary_model: str, runtime: Settings) -> list[str]:
        disabled = set(runtime.nvidia_disabled_models)
        candidates = [primary_model, *runtime.nvidia_fallback_models]
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
        "current_situation": "",
        "chronology": [],
    }


def normalize_story_memory(value: Any, max_chars: int) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    normalized = empty_story_memory()
    normalized["current_situation"] = clip(source.get("current_situation"), 2_000).strip()
    for field in STORY_LIST_FIELDS:
        normalized[field] = string_list(
            source.get(field),
            limit=STORY_FIELD_LIMITS[field],
            item_chars=600,
        )
    return fit_story_memory(normalized, max(max_chars, 1))


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
    while len(json.dumps(fitted, ensure_ascii=False)) > max_chars:
        changed = False
        for field, minimum, drop_oldest in drop_order:
            items = fitted[field]
            if len(items) <= minimum:
                continue
            items.pop(0 if drop_oldest else -1)
            changed = True
            break
        if not changed:
            longest_field = max(STORY_LIST_FIELDS, key=lambda key: sum(len(item) for item in fitted[key]))
            if fitted[longest_field]:
                fitted[longest_field].pop(0 if longest_field in {"chronology", "resolved_threads"} else -1)
                continue
            current = fitted.get("current_situation", "")
            if current:
                fitted["current_situation"] = clip(current, max(len(current) - 300, 0))
                continue
            break
    return fitted


def story_memory_prompt_text(snapshot: dict[str, Any], max_chars: int) -> str:
    memory = normalize_story_memory(snapshot.get("memory"), max_chars)
    sections: list[tuple[str, list[str]]] = [
        ("СОСТОЯНИЕ НА МОМЕНТ ПАУЗЫ", [memory["current_situation"]] if memory["current_situation"] else []),
        ("КАНОН", memory["canon"]),
        ("АКТИВНЫЕ СЮЖЕТНЫЕ ЛИНИИ", memory["active_threads"]),
        ("НЕРАСКРЫТЫЕ ЗАЦЕПКИ", memory["unresolved_hooks"]),
        ("ПЕРСОНАЖИ", memory["characters"]),
        ("ПРАВИЛА И СПОСОБНОСТИ", memory["rules_and_abilities"]),
        ("ИНВЕНТАРЬ И АКТИВЫ", memory["inventory_and_assets"]),
        ("ХРОНОЛОГИЯ", memory["chronology"][-24:]),
        ("РАЗРЕШЁННЫЕ ЛИНИИ", memory["resolved_threads"][-16:]),
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


def string_list(value: Any, *, limit: int, item_chars: int) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = []
    items: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text or text in items:
            continue
        items.append(clip(text, item_chars))
        if len(items) >= limit:
            break
    return items


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
