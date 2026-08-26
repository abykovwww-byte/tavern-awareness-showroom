"""Narrative LLM client and OpenAI-compatible response helpers."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import json
import re
import time
from typing import Any, Callable

import httpx

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, Outcome
from app.services.character_retrieval import (
    latest_player_action,
    retrieve_relevant_characters,
    selected_character_relationships,
)
from app.services.context_budget import estimate_tokens
from app.services.provider_catalog import normalize_provider
from app.services.provider_auth import outbound_headers
from app.services.scene_state import initial_scene_state, scene_claim_baseline
from app.services.trace_redaction import redact_trace_value
from app.services.rp_story_memory import story_memory_prompt_text


logger = logging.getLogger(__name__)


class PromptBudgetExceeded(RuntimeError):
    """The required RP prompt cannot fit the provider input window."""

    def __init__(self, *, estimated_tokens: int, token_budget: int):
        self.estimated_tokens = estimated_tokens
        self.token_budget = token_budget
        super().__init__("Required RP continuity context exceeds the provider input budget")


PROMPT_ASSEMBLY_SCHEMA_VERSION = "rp-gateway.prompt-assembly.v1"
PROMPT_ASSEMBLY_REVISION = 7
PROMPT_AUTHORITY_ORDER = (
    "authoritative_outcome_current_action",
    "uncovered_raw_tail",
    "rp_story_memory",
    "archive",
)
PROMPT_SYSTEM_BLOCK_IDS = (
    ("PROMPT_AUTHORITY_HIERARCHY", "prompt_authority"),
    ("RP_STORY_MEMORY", "rp_story_memory"),
    ("LONG_TERM_PARTY_MEMORY", "long_term_memory"),
    ("WORLD_SYSTEM_PROMPT", "world_system_prompt"),
    ("PLAYER_CHARACTER", "player_character"),
    ("WORLD_AUTHORS_NOTE", "world_authors_note"),
    ("ACTIVE_TRAINING_TURN_CONTRACT", "training_turn_contract"),
    ("TRAINING_INTERACTION_CONTRACT", "training_interaction_contract"),
    ("RELEVANT_CHARACTERS", "relevant_characters"),
    ("RETRIEVED_ARCHIVE_SCENES", "retrieved_archive_scenes"),
    ("UNCOMPACTED_ARCHIVE_FALLBACK", "uncompacted_archive_fallback"),
    ("PARTY_LORE_CARDS", "party_lore_cards"),
    ("ИСПРАВЛЕНИЯ ИГРОКА", "player_corrections"),
    ("WORLD_ABSOLUTE_RULES", "world_absolute_rules"),
    ("RELATIONSHIP_PRESSURE", "relationship_pressure"),
    ("RELATIONSHIP_EVENT_RESOLUTION", "relationship_event_resolution"),
    ("СОБЫТИЯ МИРА", "world_events"),
    ("SCENE_STATE_CONTRACT", "scene_state_contract"),
    ("SCENE_REANCHOR_BASELINE", "scene_reanchor_baseline"),
)
REVISION_EIGHT_STABLE_SYSTEM_PREFIXES = (
    "WORLD_SYSTEM_PROMPT",
    "PLAYER_CHARACTER",
    "WORLD_ABSOLUTE_RULES",
)


def prompt_block_id(message: dict[str, Any], index: int) -> str:
    """Return one content-free stable identity for an assembled prompt block."""

    if message.get("role") != "system":
        return "raw_turns"
    content = str(message.get("content") or "")
    for prefix, block_id in PROMPT_SYSTEM_BLOCK_IDS:
        if content.startswith(prefix):
            return block_id
    if index == 0:
        return "system_rules"
    if content.startswith("Relevant state summary:"):
        return "state_summary"
    if "AUTHORITATIVE_OUTCOME" in content:
        return "authoritative_outcome"
    return "system_other"


def prompt_block_ids(messages: list[dict[str, str]]) -> list[str]:
    block_ids: list[str] = []
    for index, message in enumerate(messages):
        block_id = prompt_block_id(message, index)
        if block_id not in block_ids:
            block_ids.append(block_id)
    return block_ids


def prompt_lore_card_ids(messages: list[dict[str, str]]) -> list[int]:
    """Return only card IDs present in the final, post-budget prompt block."""

    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") != "system" or not content.startswith("PARTY_LORE_CARDS"):
            continue
        payload_start = content.find("[")
        if payload_start < 0:
            return []
        try:
            cards = json.loads(content[payload_start:])
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(cards, list):
            return []
        return [
            int(card["id"])
            for card in cards
            if isinstance(card, dict)
            and isinstance(card.get("id"), int)
            and not isinstance(card.get("id"), bool)
            and int(card["id"]) > 0
        ]
    return []


def prompt_assembly_diagnostics(
    messages: list[dict[str, str]],
    *,
    story_memory_covered_through_turn_id: int,
    raw_tail_turn_ids: list[int],
    omitted_blocks: list[dict[str, str]] | None = None,
    rp_contract_revision: int = PROMPT_ASSEMBLY_REVISION,
) -> dict[str, Any]:
    """Build the canonical content-free RP prompt assembly record."""

    diagnostics = {
        "schema_version": PROMPT_ASSEMBLY_SCHEMA_VERSION,
        "rp_contract_revision": int(rp_contract_revision),
        "authority_order": list(PROMPT_AUTHORITY_ORDER),
        "story_memory_covered_through_turn_id": int(
            story_memory_covered_through_turn_id or 0
        ),
        "included_block_ids": prompt_block_ids(messages),
        "raw_tail_turn_ids": [int(turn_id) for turn_id in raw_tail_turn_ids],
        "omitted_blocks": [
            {
                "block_id": str(item.get("block_id") or ""),
                "reason": str(item.get("reason") or ""),
            }
            for item in (omitted_blocks or [])
            if item.get("block_id") and item.get("reason")
        ],
    }
    if int(rp_contract_revision) >= 8:
        diagnostics["lore_card_ids"] = prompt_lore_card_ids(messages)
    return diagnostics


def record_prompt_omission(
    diagnostics: dict[str, Any] | None,
    *,
    block_id: str,
    reason: str,
) -> None:
    if diagnostics is None:
        return
    omitted = diagnostics.setdefault("omitted_blocks", [])
    item = {"block_id": block_id, "reason": reason}
    if isinstance(omitted, list) and item not in omitted:
        omitted.append(item)


def prompt_authority_block() -> str:
    return (
        "PROMPT_AUTHORITY_HIERARCHY\n"
        "authoritative_outcome_current_action > uncovered_raw_tail > rp_story_memory > archive\n"
        "The current action is intent, not an automatic fact."
    )


def meaningful_rp_outcome_block(outcome: Outcome) -> str | None:
    """Render only turn-specific authority for revision 8; omit generic no-check prose."""

    generic_consequences = {
        "Continue the roleplaying scene from the player's stated intent.",
        "Apply active WorldPack rules, current state, character goals, relationships, and prior consequences.",
        "Leave consequential choices and the player character's inner decisions to the player.",
    }
    consequence_labels = {
        "Initial scene is introduced; no player decision has been resolved yet.": (
            "Покажи только начальную сцену; ни одно решение игрока ещё не совершилось"
        ),
    }
    consequences = []
    for item in outcome.consequences:
        value = str(item).strip()
        if value and value not in generic_consequences:
            consequences.append(consequence_labels.get(value, value))
    blocked = [str(item).strip() for item in outcome.blocked_reasons if str(item).strip()]
    target = str(outcome.target or "").strip()
    if target == "opening_scene":
        target = "начальная сцена"
    if not target and not consequences and not blocked:
        return None
    lines = ["AUTHORITATIVE_OUTCOME"]
    if target:
        lines.append(f"Цель текущего действия: {target}.")
    lines.extend(f"Обязательное ограничение: {item}." for item in blocked)
    lines.extend(f"Обязательное последствие: {item}." for item in consequences)
    return "\n".join(lines)


def player_character_block(state: dict[str, Any]) -> str | None:
    """Keep only the player's stable identity after rev-8 removes the full state dump."""

    player = state.get("player")
    if not isinstance(player, dict):
        return None
    name = str(player.get("name") or "").strip()
    description = str(player.get("description") or "").strip()
    if not name and not description:
        return None
    lines = ["PLAYER_CHARACTER"]
    if name:
        lines.append(f"Имя: {name}")
    if description:
        lines.append(f"Описание: {description}")
    lines.append(
        "Сохраняй эти факты о персонаже игрока, но не придумывай за него действия, "
        "реплики, мысли, чувства или выбор."
    )
    return "\n".join(lines)


def revision_eight_stable_prefix_hash(
    messages: list[dict[str, str]],
    *,
    history_units: int = 50,
) -> str:
    """Hash the provider prefix that stays byte-stable inside one history anchor."""

    stable: list[dict[str, str]] = []
    index = 0
    if messages and messages[0].get("role") == "system":
        stable.append(dict(messages[0]))
        index = 1
    while index < len(messages) - 1:
        message = messages[index]
        content = str(message.get("content") or "")
        if message.get("role") != "system" or not content.startswith(
            REVISION_EIGHT_STABLE_SYSTEM_PREFIXES
        ):
            break
        stable.append(dict(message))
        index += 1

    raw_limit = max(len(messages) - 1, index)
    units = 0
    unit_limit = max(int(history_units), 0)
    while index < raw_limit and units < unit_limit:
        message = messages[index]
        role = message.get("role")
        if role == "assistant":
            stable.append(dict(message))
            index += 1
            units += 1
            continue
        if (
            role == "user"
            and index + 1 < raw_limit
            and messages[index + 1].get("role") == "assistant"
        ):
            stable.extend((dict(message), dict(messages[index + 1])))
            index += 2
            units += 1
            continue
        break

    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prompt_cache_observability(
    response: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    history_units: int = 50,
) -> dict[str, Any]:
    """Copy provider cache counters beside the hash of the reusable RP prefix."""

    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage.get("prompt_tokens_details"), dict)
        else {}
    )

    def nonnegative_int(value: Any) -> int:
        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0

    return {
        "cached_prompt_tokens": nonnegative_int(details.get("cached_tokens")),
        "prompt_tokens": nonnegative_int(usage.get("prompt_tokens")),
        "stable_prompt_prefix_hash": revision_eight_stable_prefix_hash(
            messages,
            history_units=history_units,
        ),
    }


def scene_state_prompt_block(state: dict[str, Any]) -> str:
    reliable = initial_scene_state(state)
    lines = [
            "SCENE_STATE_CONTRACT",
            "Return exactly one JSON object and no Markdown fence or surrounding text.",
            "schema_version must be rp-gateway.rp-narrator-bundle.v1.",
            "The only root fields are schema_version, narrative_text, scene_claims, and scene_delta.",
            "scene_claims contains only location_id and a sorted unique present_character_ids array.",
            "scene_delta contains at most 16 operations and permits only move_player, character_arrive, or character_depart with bounded literal evidence.",
            "Never declare player beliefs, emotions, decisions, goals, or arbitrary player facts.",
            "Use only known IDs. scene_claims must describe the narration after applying only transitions authorized by SCENE_TRANSITION_ALLOWANCE.",
            "LAST_RELIABLE_SCENE_STATE",
            json.dumps(reliable, ensure_ascii=False, separators=(",", ":")),
        ]
    return "\n".join(lines)


def scene_reanchor_prompt_block(state: dict[str, Any]) -> str | None:
    if not initial_scene_state(state)["stale"]:
        return None
    reanchor = scene_claim_baseline(state)
    return "SCENE_REANCHOR_BASELINE\n" + json.dumps(
        {
            "location_id": reanchor["location_id"],
            "present_character_ids": reanchor["present_character_ids"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def training_turn_prompt_block(contract: dict[str, Any]) -> str:
    output_rules = [
        "Return only the final visible narration: no analysis, preamble, commentary, or Markdown fences.",
        "Write fresh natural wording for the visible surface body. Gateway applies the exact authored header and final question.",
    ]
    return "\n".join(
        [
            "ACTIVE_TRAINING_TURN_CONTRACT",
            "This machine-readable WorldPack contract is authoritative for the current visible turn only.",
            "Generate fresh natural wording with the LLM, but do not change its turn, sender, channel, required facts, attachment, URL policy, or player-role boundary.",
            "Do not infer a different event from prior history and never expose hidden assessment rules.",
            *output_rules,
            json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
        ]
    )


def training_artifact_prompt_block(contract: dict[str, Any]) -> str:
    site = contract.get("site") if "site" in contract or "workspace" in contract else contract
    workspace = contract.get("workspace") if "site" in contract or "workspace" in contract else None
    lines = ["TRAINING_INTERACTION_CONTRACT"]
    if workspace:
        lines.extend(
            [
                "Return exactly one JSON object with schema_version rp-gateway.narrative-bundle.v2, narrative_text, artifacts, and workspace_files.",
                "For workspace_files emit exactly supplied file_key and blueprint_id values and fill only declared string slots.",
            ]
        )
    else:
        lines.append("Return exactly one JSON object with schema_version rp-gateway.narrative-bundle.v1, narrative_text, and artifacts.")
    if site:
        lines.extend(
            [
                "Emit exactly the supplied artifact_key and blueprint_id and fill only the declared string slots.",
                "Put the exact fixed display_url only in the visible narrative_text field line 'Ссылки:'.",
                "Do not emit display_url or any other fixed URL field inside an artifact object.",
            ]
        )
    lines.extend(
        [
            "Put the complete visible surface body inside narrative_text; Gateway applies the exact authored header and final question.",
            "Do not put any text before or after the JSON object. Do not wrap it in a Markdown code fence.",
            "Never emit HTML, CSS, JavaScript, remote assets, credentials, paths, MIME types, file classification, answer keys, scoring, correctness, or remediation.",
            json.dumps({"site": site, "workspace": workspace}, ensure_ascii=False, separators=(",", ":")),
        ]
    )
    return "\n".join(lines)


class ProviderRateLimitError(RuntimeError):
    def __init__(
        self,
        provider: str,
        model: str,
        retry_after_seconds: float | None,
        error_type: str | None,
        provider_code: str | None,
        response_message: str | None,
    ):
        self.details = {
            "provider": provider,
            "model": model,
            "status": 429,
            "retry_after_seconds": retry_after_seconds,
            "error_type": error_type,
            "provider_code": provider_code,
            "response_message": response_message,
        }
        retry_hint = f" Retry after {retry_after_seconds:g}s." if retry_after_seconds else ""
        super().__init__(f"{provider} API returned 429 rate limit for {model}.{retry_hint}")

    def public_detail(self) -> dict[str, Any]:
        return {
            "code": "provider_rate_limited",
            "message": "The selected model is temporarily rate limited.",
            "provider": self.details["provider"],
            "model": self.details["model"],
            "retry_after_seconds": self.details["retry_after_seconds"],
            "error_type": self.details["error_type"],
        }


class NarrativeClient:
    def __init__(
        self,
        settings: Settings,
        trace_recorder: Callable[[dict[str, Any]], Any] | None = None,
    ):
        self.settings = settings
        self.trace_recorder = trace_recorder

    async def complete(
        self,
        request: ChatCompletionRequest,
        state: dict[str, Any],
        outcome: Outcome,
        inbound_authorization: str | None,
        repair_instruction: str | None = None,
        failed_response_text: str | None = None,
        memory_summary: dict[str, Any] | list[dict[str, Any]] | None = None,
        rp_story_memory: dict[str, Any] | None = None,
        request_id: str | None = None,
        artifact_contract: dict[str, Any] | None = None,
        training_turn_contract: dict[str, Any] | None = None,
        relationship_pressure: str | None = None,
        world_events: str | None = None,
        opening_prompt: str | None = None,
    ) -> dict[str, Any]:
        headers = outbound_headers(
            self.settings.llm_provider,
            self.settings.llm_api_key,
            inbound_authorization,
        )
        payload = request.model_dump(exclude_none=True)
        if repair_instruction:
            player_corrections = next(
                (
                    str(message.content)
                    for message in request.messages
                    if message.role == "system"
                    and isinstance(message.content, str)
                    and message.content.startswith("ИСПРАВЛЕНИЯ ИГРОКА")
                ),
                None,
            )
            payload["messages"] = self.repair_messages(
                state,
                outcome,
                repair_instruction,
                failed_response_text or "",
                artifact_contract=artifact_contract,
                training_turn_contract=training_turn_contract,
                relationship_pressure=relationship_pressure,
                world_events=world_events,
                player_corrections=player_corrections,
                opening_prompt=opening_prompt,
            )
        else:
            payload["messages"] = self.narrative_messages(
                request,
                state,
                outcome,
                repair_instruction=None,
                memory_summary=memory_summary,
                rp_story_memory=rp_story_memory,
                artifact_contract=artifact_contract,
                training_turn_contract=training_turn_contract,
                relationship_pressure=relationship_pressure,
                world_events=world_events,
            )
        self.apply_prompt_cache_policy(payload)
        payload["stream"] = False
        narrator_settings_model = (request._narrator_settings_model or "").strip().lower()

        if self.settings.llm_api_base.startswith("mock://"):
            attempt_payload = copy.deepcopy(payload)
            attempt_payload["model"] = self.settings.narrative_model
            uses_narrator_settings = narrator_settings_model == self.settings.narrative_model.strip().lower()
            if narrator_settings_model and not uses_narrator_settings:
                for key in ("reasoning", "temperature", "top_p", "max_tokens"):
                    attempt_payload.pop(key, None)
            self.apply_model_policy(
                attempt_payload,
                self.settings.narrative_model,
                require_parameters=uses_narrator_settings,
            )
            started = time.perf_counter()
            try:
                data = self.mock_completion(outcome, repair_instruction, artifact_contract, state=state)
            except Exception as exc:
                self.record_trace_attempt(
                    request_id=request_id,
                    payload=attempt_payload,
                    model=self.settings.narrative_model,
                    attempt_index=1,
                    repair_instruction=repair_instruction,
                    status="failed",
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                    error=exc,
                )
                raise
            self.record_trace_attempt(
                request_id=request_id,
                payload=attempt_payload,
                model=self.settings.narrative_model,
                attempt_index=1,
                repair_instruction=repair_instruction,
                status="completed",
                elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
                raw_response=json.dumps(data, ensure_ascii=False),
                usage=data.get("usage") if isinstance(data, dict) else None,
                http_status=200,
            )
            return data

        timeout = httpx.Timeout(self.settings.model_attempt_timeout_seconds, connect=15.0)
        attempts = self.model_attempts(self.settings.narrative_model)
        last_timeout: httpx.TimeoutException | None = None
        last_status: httpx.HTTPStatusError | None = None
        last_request_error: httpx.RequestError | None = None
        rate_limit_retries = 0
        trace_attempt_index = 0
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, model in enumerate(attempts):
                attempt_payload = copy.deepcopy(payload)
                attempt_payload["model"] = model
                uses_narrator_settings = narrator_settings_model == model.strip().lower()
                if narrator_settings_model and not uses_narrator_settings:
                    for key in ("reasoning", "temperature", "top_p", "max_tokens"):
                        attempt_payload.pop(key, None)
                self.apply_model_policy(
                    attempt_payload,
                    model,
                    require_parameters=uses_narrator_settings,
                )
                empty_response_retry_used = False
                while True:
                    trace_attempt_index += 1
                    started = time.perf_counter()
                    logger.info(
                        "llm_attempt_start request_id=%s check_id=%s model=%s attempt=%s/%s timeout_seconds=%s repair=%s",
                        request_id,
                        outcome.check_id,
                        model,
                        index + 1,
                        len(attempts),
                        self.settings.model_attempt_timeout_seconds,
                        bool(repair_instruction),
                    )
                    try:
                        async with asyncio.timeout(self.settings.model_attempt_timeout_seconds):
                            response = await client.post(
                                f"{self.settings.llm_api_base.rstrip('/')}/chat/completions",
                                json=attempt_payload,
                                headers=headers,
                            )
                    except (httpx.TimeoutException, TimeoutError) as exc:
                        timeout_error = exc
                        if not isinstance(exc, httpx.TimeoutException):
                            timeout_error = httpx.TimeoutException(
                                "Narrative provider exceeded the wall-clock deadline",
                                request=httpx.Request(
                                    "POST",
                                    f"{self.settings.llm_api_base.rstrip('/')}/chat/completions",
                                ),
                            )
                        last_timeout = timeout_error
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        logger.warning(
                            "llm_attempt_timeout request_id=%s check_id=%s model=%s attempt=%s/%s elapsed_ms=%s fallback=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            index + 1,
                            len(attempts),
                            elapsed_ms,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            error=timeout_error,
                        )
                        if index < len(attempts) - 1:
                            break
                        raise timeout_error from exc
                    except httpx.RequestError as exc:
                        last_request_error = exc
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        logger.warning(
                            "llm_attempt_network_error request_id=%s check_id=%s model=%s "
                            "attempt=%s/%s elapsed_ms=%s error_type=%s fallback=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            index + 1,
                            len(attempts),
                            elapsed_ms,
                            type(exc).__name__,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            error=exc,
                        )
                        if index < len(attempts) - 1:
                            break
                        raise
                    if response.status_code == 429:
                        error = provider_rate_limit_error(response, self.settings.llm_provider, model)
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        retry_delay = error.details["retry_after_seconds"] or self.settings.rate_limit_retry_default_wait_seconds
                        can_retry = (
                            rate_limit_retries < self.settings.rate_limit_retry_attempts
                            and 0 < retry_delay <= self.settings.rate_limit_retry_max_wait_seconds
                        )
                        logger.warning(
                            "llm_attempt_rate_limited request_id=%s check_id=%s model=%s elapsed_ms=%s retry_after_seconds=%s error_type=%s provider_code=%s retry=%s fallback=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            elapsed_ms,
                            error.details["retry_after_seconds"],
                            error.details["error_type"],
                            error.details["provider_code"],
                            can_retry,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                            error=error,
                            http_status=response.status_code,
                        )
                        if can_retry:
                            rate_limit_retries += 1
                            await asyncio.sleep(retry_delay)
                            continue
                        if index < len(attempts) - 1:
                            break
                        raise error
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        last_status = exc
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        logger.warning(
                            "llm_attempt_http_error request_id=%s check_id=%s model=%s status=%s elapsed_ms=%s fallback=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            response.status_code,
                            elapsed_ms,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                            error=exc,
                            http_status=response.status_code,
                        )
                        if index < len(attempts) - 1 and response.status_code in {
                            400,
                            403,
                            404,
                            408,
                            410,
                            500,
                            502,
                            503,
                            504,
                        }:
                            break
                        raise
                    try:
                        data = response.json()
                        if not isinstance(data, dict):
                            raise RuntimeError("Narrative provider response must be a JSON object")
                    except Exception as exc:
                        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                            error=exc,
                            http_status=response.status_code,
                        )
                        raise
                    data.setdefault("model", model)
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    if not response_text(data).strip():
                        error = RuntimeError("Narrative provider returned an empty final response")
                        retry_same_model = not empty_response_retry_used
                        logger.warning(
                            "llm_attempt_empty_response request_id=%s check_id=%s model=%s "
                            "elapsed_ms=%s retry_same_model=%s fallback_after_retry=%s",
                            request_id,
                            outcome.check_id,
                            model,
                            elapsed_ms,
                            retry_same_model,
                            index < len(attempts) - 1,
                        )
                        self.record_trace_attempt(
                            request_id=request_id,
                            payload=attempt_payload,
                            model=model,
                            attempt_index=trace_attempt_index,
                            repair_instruction=repair_instruction,
                            status="failed",
                            elapsed_ms=elapsed_ms,
                            raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                            error=error,
                            http_status=response.status_code,
                        )
                        if retry_same_model:
                            empty_response_retry_used = True
                            continue
                        if index < len(attempts) - 1:
                            break
                        # Preserve the existing terminal empty-response audit and
                        # no-commit handling in the caller after the bounded retry.
                        return data
                    logger.info(
                        "llm_attempt_success request_id=%s check_id=%s model=%s status=%s elapsed_ms=%s fallback_used=%s",
                        request_id,
                        outcome.check_id,
                        model,
                        response.status_code,
                        elapsed_ms,
                        index > 0 or model != self.settings.narrative_model,
                    )
                    self.record_trace_attempt(
                        request_id=request_id,
                        payload=attempt_payload,
                        model=model,
                        attempt_index=trace_attempt_index,
                        repair_instruction=repair_instruction,
                        status="completed",
                        elapsed_ms=elapsed_ms,
                        raw_response=(response.text if self.trace_recorder is not None and request_id else None),
                        usage=data.get("usage") if isinstance(data, dict) else None,
                        http_status=response.status_code,
                    )
                    return data
        if last_status:
            raise last_status
        if last_timeout:
            raise last_timeout
        if last_request_error:
            raise last_request_error
        raise RuntimeError(f"No model attempts configured for provider {self.settings.llm_provider}")

    def record_trace_attempt(
        self,
        *,
        request_id: str | None,
        payload: dict[str, Any],
        model: str,
        attempt_index: int,
        repair_instruction: str | None,
        status: str,
        elapsed_ms: float,
        raw_response: str | None = None,
        usage: Any = None,
        error: Exception | None = None,
        http_status: int | None = None,
    ) -> None:
        if self.trace_recorder is None or not request_id:
            return
        event = {
            "request_id": request_id,
            "status": status,
            "provider": self.settings.llm_provider,
            "model": model,
            "attempt_index": attempt_index,
            "repair": repair_instruction is not None,
            "repair_instruction": repair_instruction,
            "latency_ms": elapsed_ms,
            "http_status": http_status,
            "usage": usage,
            "input": {"payload": payload},
            "output": {"raw_response": raw_response} if raw_response is not None else None,
            "error": (
                {"type": type(error).__name__, "message": str(error)[:1000]}
                if error is not None
                else None
            ),
        }
        safe_event = redact_trace_value(event, self.trace_secrets())
        try:
            self.trace_recorder(safe_event)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never break a turn
            logger.warning(
                "turn_trace_capture_failed request_id=%s error=%s",
                request_id,
                f"{type(exc).__name__}: {exc}",
            )

    def trace_secrets(self) -> tuple[str | None, ...]:
        return (
            self.settings.llm_api_key,
            self.settings.service_openrouter_api_key,
        )

    def model_attempts(self, primary_model: str) -> list[str]:
        disabled = set(self.settings.llm_disabled_models)
        candidates = [primary_model, *self.settings.llm_fallback_models]
        attempts: list[str] = []
        for model in candidates:
            if not model or model in disabled or model in attempts:
                continue
            attempts.append(model)
        return attempts or [primary_model]

    def narrative_messages(
        self,
        request: ChatCompletionRequest,
        state: dict[str, Any],
        outcome: Outcome,
        repair_instruction: str | None,
        memory_summary: dict[str, Any] | list[dict[str, Any]] | None = None,
        rp_story_memory: dict[str, Any] | None = None,
        artifact_contract: dict[str, Any] | None = None,
        training_turn_contract: dict[str, Any] | None = None,
        relationship_pressure: str | None = None,
        world_events: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        revision_seven = (
            self.settings.scenario_type == "rp"
            and self.settings.rp_contract_revision >= PROMPT_ASSEMBLY_REVISION
        )
        revision_eight = (
            self.settings.scenario_type == "rp"
            and self.settings.rp_contract_revision >= 8
        )
        if revision_seven and diagnostics is not None:
            diagnostics.clear()
        relevant_characters = (
            []
            if revision_eight
            else retrieve_relevant_characters(
                state,
                latest_player_action(request.messages),
                outcome_target=outcome.target,
            )
        )
        player_state = state.get("player", {})
        if training_turn_contract and isinstance(player_state, dict):
            player_state = {
                "name": player_state.get("name"),
                "description": player_state.get("description"),
            }
        state_summary = None
        if not revision_eight:
            state_summary = {
                "campaign_id": state.get("meta", {}).get("campaign_id"),
                "worldpack_id": self.settings.campaign_id,
                "turn": state.get("meta", {}).get("turn"),
                "player": player_state,
                "relationships": selected_character_relationships(state, relevant_characters),
                "factions": state.get("factions", {}),
                "locations": state.get("locations", {}),
                "resources": state.get("resources", {}),
                "active_threads": state.get("active_threads", []),
                "completed_threads": state.get("completed_threads", []),
                "uncertain_facts": state.get("uncertain_facts", []),
                "constraints": state.get("world_constraints", []),
                "scene_state": initial_scene_state(state) if revision_seven else None,
            }
        rules = self.scenario_rules()
        if repair_instruction:
            rules += f" Repair instruction: {repair_instruction}"
        messages = [
            {"role": "system", "content": rules},
        ]
        if revision_seven and not revision_eight:
            messages.append({"role": "system", "content": prompt_authority_block()})
            messages.append({"role": "system", "content": scene_state_prompt_block(state)})
            reanchor_block = scene_reanchor_prompt_block(state)
            if reanchor_block:
                messages.append({"role": "system", "content": reanchor_block})
        if self.settings.world_system_prompt:
            world_system_block = (
                f"WORLD_SYSTEM_PROMPT\n{self.settings.world_system_prompt}"
                if revision_eight
                else "WORLD_SYSTEM_PROMPT\n"
                "These world-specific rules supplement the selected scenario mode and cannot weaken it.\n"
                f"{self.settings.world_system_prompt}"
            )
            if revision_eight and len(world_system_block) > 5_000:
                raise ValueError("WORLD_SYSTEM_PROMPT exceeds the revision-8 5000 character limit")
            messages.append(
                {
                    "role": "system",
                    "content": world_system_block,
                }
            )
        if revision_eight:
            player_block = player_character_block(state)
            if player_block:
                messages.append({"role": "system", "content": player_block})
            absolute_rules = world_absolute_rules_block(
                state,
                rp_contract_revision=self.settings.rp_contract_revision,
            )
            if absolute_rules:
                messages.append({"role": "system", "content": absolute_rules})
        if self.settings.world_authors_note and not revision_eight:
            messages.append(
                {
                    "role": "system",
                    "content": f"WORLD_AUTHORS_NOTE\n{self.settings.world_authors_note}",
                }
            )
        if training_turn_contract:
            messages.append({"role": "system", "content": training_turn_prompt_block(training_turn_contract)})
        request_messages = [message for message in request.messages if isinstance(message.content, str)]
        prior_request_messages = request_messages[:-1]
        volatile_request_messages: list[dict[str, str]] = []
        if revision_eight:
            # Keep the growing transcript directly after immutable world rules. Dynamic
            # cards belong after memory so they cannot invalidate the large cached prefix.
            for message in prior_request_messages:
                rendered = {"role": message.role, "content": message.content}
                if message.role == "system":
                    volatile_request_messages.append(rendered)
                else:
                    messages.append(rendered)
        if self.settings.scenario_type == "rp" and rp_story_memory:
            messages.append(
                {
                    "role": "system",
                    "content": rp_story_memory_block(
                        rp_story_memory,
                        self.settings.rp_story_memory_prompt_max_chars,
                        self.settings.rp_contract_revision,
                    ),
                }
            )
        if artifact_contract:
            messages.append({"role": "system", "content": training_artifact_prompt_block(artifact_contract)})
        if memory_summary and revision_eight:
            record_prompt_omission(
                diagnostics,
                block_id="long_term_memory",
                reason="disabled_revision8",
            )
        elif memory_summary and not (revision_seven and rp_story_memory):
            messages.append({"role": "system", "content": long_term_memory_block(memory_summary)})
        elif memory_summary and revision_seven and rp_story_memory:
            record_prompt_omission(
                diagnostics,
                block_id="long_term_memory",
                reason="structural_deduplication",
            )
        if revision_eight:
            messages.extend(volatile_request_messages)
        else:
            for message in prior_request_messages:
                messages.append({"role": message.role, "content": message.content})
        if relevant_characters and not revision_eight:
            messages.append(
                {
                    "role": "system",
                    "content": relevant_characters_block(relevant_characters),
                }
            )
        if (
            self.settings.scenario_type == "rp"
            and self.settings.rp_contract_revision >= 3
            and not revision_eight
        ):
            absolute_rules = world_absolute_rules_block(
                state,
                rp_contract_revision=self.settings.rp_contract_revision,
            )
            if absolute_rules:
                messages.append({"role": "system", "content": absolute_rules})
        if revision_eight:
            outcome_block = meaningful_rp_outcome_block(outcome)
            if outcome_block:
                messages.append({"role": "system", "content": outcome_block})
        else:
            messages.extend(
                [
                    {"role": "system", "content": f"Relevant state summary: {state_summary}"},
                    {"role": "system", "content": outcome.authoritative_block},
                ]
            )
        if self.settings.scenario_type == "rp" and relationship_pressure:
            messages.append({"role": "system", "content": relationship_pressure})
        if revision_eight and world_events:
            if len(world_events) > 800 or not world_events.startswith("СОБЫТИЯ МИРА"):
                raise ValueError("invalid revision-10 world events prompt block")
            messages.append({"role": "system", "content": world_events})
        if self.settings.world_authors_note and revision_eight:
            authors_note_block = f"WORLD_AUTHORS_NOTE\n{self.settings.world_authors_note}"
            if len(authors_note_block) > 1_500:
                raise ValueError("WORLD_AUTHORS_NOTE exceeds the revision-8 1500 character limit")
            messages.append(
                {
                    "role": "system",
                    "content": authors_note_block,
                }
            )
        # The current player action must remain the final message after dynamic runtime context.
        if request_messages:
            current_action = request_messages[-1]
            messages.append({"role": current_action.role, "content": current_action.content})
        max_prompt_chars = None
        raw_transcript_chars = request._raw_transcript_chars
        if (
            self.settings.scenario_type == "rp"
            and self.settings.rp_contract_revision >= 6
            and self.settings.rp_contract_revision < 8
            and raw_transcript_chars
        ):
            max_prompt_chars = raw_transcript_chars // 2
        return fit_messages_to_context(
            messages,
            self.input_token_budget(request),
            max_prompt_chars=max_prompt_chars,
            protect_history=(
                self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 7
            ),
            fail_on_token_overflow=(
                self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 7
            ),
            diagnostics=diagnostics if revision_seven else None,
            history_removable_units=(
                request._rp_raw_history_removable_units if revision_eight else None
            ),
            raw_history_turn_ids=(
                request._rp_raw_history_turn_ids if revision_eight else None
            ),
        )

    def apply_prompt_cache_policy(self, payload: dict[str, Any]) -> None:
        """Add only provider-documented cache controls; other providers use the stable prefix implicitly."""
        if normalize_provider(self.settings.llm_provider) != "openrouter":
            return
        if self.settings.prompt_cache_session_id:
            payload["session_id"] = self.settings.prompt_cache_session_id
        if self.settings.openrouter_prompt_cache_enabled and str(payload.get("model") or "").startswith("anthropic/"):
            payload["cache_control"] = {"type": "ephemeral", "ttl": self.settings.openrouter_prompt_cache_ttl}

    def apply_model_policy(
        self,
        payload: dict[str, Any],
        model: str,
        *,
        require_parameters: bool = False,
    ) -> None:
        """Apply model-specific runtime controls while preserving unrelated caller preferences."""
        if normalize_provider(self.settings.llm_provider) != "openrouter":
            return
        provider_preferences = dict(payload.get("provider") or {})
        if model.strip().lower() == "deepseek/deepseek-v4-flash":
            provider_preferences["sort"] = "throughput"
        if require_parameters:
            provider_preferences["require_parameters"] = True
        if provider_preferences:
            payload["provider"] = provider_preferences

    def repair_messages(
        self,
        state: dict[str, Any],
        outcome: Outcome,
        repair_instruction: str,
        failed_response_text: str,
        artifact_contract: dict[str, Any] | None = None,
        training_turn_contract: dict[str, Any] | None = None,
        relationship_pressure: str | None = None,
        world_events: str | None = None,
        player_corrections: str | None = None,
        opening_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """Build a compact correction request instead of replaying the full party prompt."""
        player_resources = state.get("player", {}).get("resources", {})
        repair_context = {
            "campaign_id": state.get("meta", {}).get("campaign_id"),
            "turn": state.get("meta", {}).get("turn"),
            "current_turn_window": player_resources.get("current-turn-window"),
            "authoritative_outcome": outcome.authoritative_block,
            "repair_instruction": repair_instruction,
            "failed_response": failed_response_text,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    self.scenario_rules()
                    + " Correct only the supplied failed response. Do not continue the story, redo the turn, "
                    "or introduce new facts. Return only the corrected narration or required narrative bundle."
                ),
            }
        ]
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision == 7:
            messages.append({"role": "system", "content": prompt_authority_block()})
            messages.append({"role": "system", "content": scene_state_prompt_block(state)})
            reanchor_block = scene_reanchor_prompt_block(state)
            if reanchor_block:
                messages.append({"role": "system", "content": reanchor_block})
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 11:
            world_system_block = f"WORLD_SYSTEM_PROMPT\n{self.settings.world_system_prompt}"
            if len(world_system_block) > 5_000:
                raise ValueError("WORLD_SYSTEM_PROMPT exceeds the revision-8 5000 character limit")
            messages.append({"role": "system", "content": world_system_block})
        if training_turn_contract:
            messages.append({"role": "system", "content": training_turn_prompt_block(training_turn_contract)})
        if artifact_contract:
            messages.append({"role": "system", "content": training_artifact_prompt_block(artifact_contract)})
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 8:
            player_block = player_character_block(state)
            if player_block:
                messages.append({"role": "system", "content": player_block})
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 3:
            absolute_rules = world_absolute_rules_block(
                state,
                rp_contract_revision=self.settings.rp_contract_revision,
            )
            if absolute_rules:
                messages.append({"role": "system", "content": absolute_rules})
        if self.settings.scenario_type == "rp" and player_corrections:
            messages.append({"role": "system", "content": player_corrections})
        if self.settings.scenario_type == "rp" and relationship_pressure:
            messages.append({"role": "system", "content": relationship_pressure})
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 10 and world_events:
            messages.append({"role": "system", "content": world_events})
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 11:
            authors_note_block = f"WORLD_AUTHORS_NOTE\n{self.settings.world_authors_note}"
            if len(authors_note_block) > 1_500:
                raise ValueError("WORLD_AUTHORS_NOTE exceeds the revision-8 1500 character limit")
            messages.append({"role": "system", "content": authors_note_block})
            if opening_prompt is not None:
                messages.append({"role": "system", "content": f"OPENING_PROMPT\n{opening_prompt}"})
        messages.append(
            {
                "role": "user",
                "content": json.dumps(repair_context, ensure_ascii=False, separators=(",", ":")),
            }
        )
        return messages

    def input_token_budget(self, request: ChatCompletionRequest) -> int:
        reserve = max(
            self.settings.party_context_completion_reserve_tokens,
            int(request.max_tokens or 0),
        )
        return max(self.settings.effective_party_context_limit_tokens - reserve, 1)

    def scenario_rules(self) -> str:
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 8:
            return (
                "Ты ведущий и рассказчик ролевой игры без механических проверок. Отвечай на языке игрока "
                "только художественным текстом сцены и диалогом. Реплика игрока — намерение, а не уже "
                "случившийся факт. Не придумывай броски, сложность, модификаторы, очки, успех или провал. "
                "Сохраняй свободу игрока: не решай за его персонажа, что тот делает, думает, чувствует или "
                "выбирает. Не пиши от первого лица персонажа игрока и не создавай за него реплики. "
                "Соблюдай факты истории, правила мира, цели персонажей, отношения, ресурсы и прежние "
                "последствия. Не показывай служебные данные, анализ и формулировки шлюза. Завершай сцену ясной "
                "возможностью для следующего действия игрока."
            )
        common = (
            "Reply in the player's language. Output only final in-world narration and dialogue. "
            "Preserve player agency: never choose actions, beliefs, emotions, or conclusions for the player character. "
            "Treat current state as authoritative, do not invent missing resources, and never expose service JSON, "
            "analysis, recommendations, diagnostics, critique, outcome tags, or Gateway wording. "
        )
        if self.settings.scenario_type == "training":
            return common + (
                "You are the runtime narrator for a deterministic training scenario. There are no random rolls or "
                "skill checks. Follow the authored scenario structure, schedule, presentation templates, and completion "
                "conditions exactly. Resolve only actions explicitly stated by the player and advance exactly one "
                "scenario turn. Do not coach, hint, assess, explain best practice, reveal hidden scoring, or announce "
                "whether an item is safe or suspicious unless the authored scenario explicitly schedules a final debrief. "
                "If player.resources.current-turn-window is present, begin with that exact scheduled turn as a Russian "
                "player-facing header and never remain in the previous time window."
            )
        if self.settings.rp_contract_revision >= 1:
            rules = common + (
                "You are the GM and narrator of a roleplaying game without mechanical checks. Treat the latest player "
                "message as intent, not as an automatic fact or a request for hidden adjudication. Never invent dice, "
                "difficulty, modifiers, scores, success, or failure. Difficulty comes only from active WorldPack rules, "
                "current state, NPC goals, available information, resources, relationships, and prior consequences. "
                "Obey every WORLD_ABSOLUTE_RULES item and end with a playable opening for the next player action."
            )
            if self.settings.rp_contract_revision == 7:
                rules += (
                    " Keep the complete player-visible prose inside narrative_text and return the private revision-7 "
                    "scene bundle required by SCENE_STATE_CONTRACT."
                )
            return rules
        return common + (
            "You are the GM and narrator of a roleplaying game. The RP Gateway has already resolved the D20 check. "
            "Describe that fixed result as fiction. Do not reroll, change the result, create an equivalent hidden "
            "success after failure, bypass hard constraints, or expose roll calculations unless the player-facing "
            "world rules explicitly require them. End with a playable opening for the next player action."
        )

    def mock_completion(
        self,
        outcome: Outcome,
        repair_instruction: str | None,
        artifact_contract: dict[str, Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mode = self.settings.llm_api_base.removeprefix("mock://")
        if mode == "timeout":
            raise httpx.TimeoutException("mock timeout")
        if mode == "http-503":
            request = httpx.Request("POST", "https://mock.provider.local/chat/completions")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("mock provider unavailable", request=request, response=response)
        if mode == "rate-limit":
            raise ProviderRateLimitError(
                provider=self.settings.llm_provider,
                model=self.settings.narrative_model,
                retry_after_seconds=3,
                error_type="rate_limit_exceeded",
                provider_code="mock_rate_limited",
                response_message="mock rate limit",
            )
        if mode == "violate" and not repair_instruction:
            content = "Despite the failure, the king secretly grants equivalent military authority."
        elif mode == "meta-leak" and not repair_instruction:
            content = "— Анализ: игроку нужен таймскип.\nРекомендация: перейти к событию.\n\nТы идешь к мосту."
        elif mode == "meta-leak":
            content = "Телефон гаснет в кармане. Дорога к мосту сжимается до нескольких шагов, и впереди уже слышны голоса."
        elif mode == "repair-fail":
            content = "Despite the failure, the king still transfers command authority."
        else:
            content = "The scene shifts around the attempt, leaving the next opening clear without taking control from the player."
        if artifact_contract:
            site_contract = artifact_contract.get("site") if "site" in artifact_contract or "workspace" in artifact_contract else artifact_contract
            workspace_contract = artifact_contract.get("workspace") if "site" in artifact_contract or "workspace" in artifact_contract else None
            slot_values = {
                slot_id: ("Продолжить" if slot_id.endswith("label") else "Учебная страница")
                for slot_id in (site_contract or {}).get("slots", {})
            }
            workspace_files = []
            for file_contract in (workspace_contract or {}).get("files", []):
                workspace_files.append(
                    {
                        "file_key": file_contract["file_key"],
                        "blueprint_id": file_contract["blueprint_id"],
                        "slots": {slot_id: "Учебный документ" for slot_id in file_contract.get("slots", {})},
                    }
                )
            narrative_text = content
            artifacts = []
            if site_contract:
                narrative_text = f"{content}\n\nСсылка: {site_contract['display_url']}"
                artifacts = [
                    {
                        "artifact_key": site_contract["artifact_key"],
                        "blueprint_id": site_contract["blueprint_id"],
                        "slots": slot_values,
                    }
                ]
            content = json.dumps(
                {
                    "schema_version": "rp-gateway.narrative-bundle.v2" if workspace_contract else "rp-gateway.narrative-bundle.v1",
                    "narrative_text": narrative_text,
                    "artifacts": artifacts,
                    **({"workspace_files": workspace_files} if workspace_contract else {}),
                },
                ensure_ascii=False,
            )
        elif self.settings.scenario_type == "rp" and self.settings.rp_contract_revision == 7:
            scene = initial_scene_state(state or {})
            content = json.dumps(
                {
                    "schema_version": "rp-gateway.rp-narrator-bundle.v1",
                    "narrative_text": content,
                    "scene_claims": {
                        "location_id": scene["location_id"],
                        "present_character_ids": scene["present_character_ids"],
                    },
                    "scene_delta": [],
                },
                ensure_ascii=False,
            )
        return {
            "id": f"mock-{outcome.check_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.settings.narrative_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }


def response_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    return str(content) if content is not None else ""


def json_object_content(value: str) -> str:
    """Extract one provider-wrapped JSON object without accepting mixed JSON payloads."""
    text = value.strip()
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if len(fenced) == 1:
        return fenced[0].strip()
    return text


def provider_rate_limit_error(response: httpx.Response, provider: str, model: str) -> ProviderRateLimitError:
    payload: dict[str, Any] = {}
    try:
        decoded = response.json()
        if isinstance(decoded, dict):
            payload = decoded
    except (ValueError, json.JSONDecodeError):
        pass
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    metadata = error.get("metadata") if isinstance(error.get("metadata"), dict) else {}
    message = str(error.get("message") or payload.get("message") or "").strip()[:500] or None
    return ProviderRateLimitError(
        provider=provider,
        model=model,
        retry_after_seconds=parse_retry_after(response.headers.get("Retry-After")),
        error_type=str(metadata.get("error_type") or error.get("error_type") or "").strip() or None,
        provider_code=str(metadata.get("provider_code") or "").strip() or None,
        response_message=message,
    )


def parse_retry_after(value: str | None) -> float | None:
    try:
        seconds = float(value or "")
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def fit_messages_to_context(
    messages: list[dict[str, str]],
    token_budget: int,
    *,
    max_prompt_chars: int | None = None,
    protect_history: bool = False,
    fail_on_token_overflow: bool = False,
    diagnostics: dict[str, Any] | None = None,
    history_removable_units: int | None = None,
    raw_history_turn_ids: list[int] | None = None,
) -> list[dict[str, str]]:
    """Keep the latest action and mandatory instructions inside the real provider input budget."""
    fitted = [dict(message) for message in messages]
    remaining_history_removals = (
        max(int(history_removable_units), 0)
        if history_removable_units is not None
        else None
    )
    fitted_raw_turn_ids = [int(turn_id) for turn_id in (raw_history_turn_ids or [])]
    while fitted:
        prompt_text = "\n".join(message["content"] for message in fitted)
        over_token_budget = estimate_tokens(prompt_text) > token_budget
        over_prompt_chars = max_prompt_chars is not None and len(prompt_text) > max_prompt_chars
        if not over_token_budget and not over_prompt_chars:
            break
        if protect_history:
            # Revision 7 keeps the legacy percentage target best-effort and
            # evicts whole optional blocks only for the provider's hard budget.
            if not over_token_budget:
                break
            optional_prefixes = (
                ("PARTY_LORE_CARDS",)
                if remaining_history_removals is not None
                else (
                    "RETRIEVED_ARCHIVE_SCENES",
                    "LONG_TERM_PARTY_MEMORY",
                    "PARTY_LORE_CARDS",
                    "RELEVANT_CHARACTERS",
                )
            )
            trim_index = next(
                (
                    index
                    for prefix in optional_prefixes
                    for index, message in enumerate(fitted)
                    if message.get("role") == "system"
                    and message.get("content", "").startswith(prefix)
                ),
                None,
            )
            if trim_index is not None:
                removed = fitted.pop(trim_index)
                record_prompt_omission(
                    diagnostics,
                    block_id=prompt_block_id(removed, trim_index),
                    reason="hard_input_budget",
                )
                continue
            if remaining_history_removals is not None and remaining_history_removals > 0:
                history_indices = [
                    index
                    for index, message in enumerate(fitted[:-1])
                    if message.get("role") != "system"
                ]
                oldest_history = history_indices[0] if history_indices else None
                if oldest_history is None:
                    remaining_history_removals = 0
                elif fitted[oldest_history].get("role") == "assistant":
                    fitted.pop(oldest_history)
                    remaining_history_removals -= 1
                    if fitted_raw_turn_ids:
                        fitted_raw_turn_ids.pop(0)
                    continue
                elif (
                    fitted[oldest_history].get("role") == "user"
                    and oldest_history + 1 < len(fitted) - 1
                    and fitted[oldest_history + 1].get("role") == "assistant"
                ):
                    fitted.pop(oldest_history + 1)
                    fitted.pop(oldest_history)
                    remaining_history_removals -= 1
                    if fitted_raw_turn_ids:
                        fitted_raw_turn_ids.pop(0)
                    continue
            if fail_on_token_overflow:
                raise PromptBudgetExceeded(
                    estimated_tokens=estimate_tokens(prompt_text),
                    token_budget=token_budget,
                )
            break
        history_indices = [
            index
            for index, message in enumerate(fitted[:-1])
            if message.get("role") != "system"
        ]
        if (
            over_prompt_chars
            and not over_token_budget
            and [fitted[index].get("role") for index in history_indices] == ["user", "assistant"]
        ):
            break
        oldest_history = history_indices[0] if history_indices else None
        if oldest_history is not None:
            if (
                over_prompt_chars
                and fitted[oldest_history].get("role") == "user"
                and oldest_history + 1 < len(fitted) - 1
                and fitted[oldest_history + 1].get("role") == "assistant"
            ):
                fitted.pop(oldest_history + 1)
            fitted.pop(oldest_history)
            continue
        if over_prompt_chars and not over_token_budget:
            break
        has_rp_story_memory = any(
            message.get("content", "").startswith("RP_STORY_MEMORY") for message in fitted
        )
        trim_prefixes = (
            (
                "RETRIEVED_ARCHIVE_SCENES",
                "UNCOMPACTED_ARCHIVE_FALLBACK",
                "LONG_TERM_PARTY_MEMORY",
                "PARTY_LORE_CARDS",
                "RP_STORY_MEMORY",
            )
            if has_rp_story_memory
            else ("LONG_TERM_PARTY_MEMORY",)
        )
        trim_index = next(
            (
                index
                for prefix in trim_prefixes
                for index, message in enumerate(fitted)
                if message.get("content", "").startswith(prefix)
            ),
            None,
        )
        if trim_index is None:
            trim_index = next((index for index, message in enumerate(fitted[:-1]) if message.get("role") == "system"), None)
        if trim_index is None:
            trim_index = len(fitted) - 1
        content = fitted[trim_index].get("content", "")
        excess_chars = max((estimate_tokens(prompt_text) - token_budget) * 3, 1)
        retained = max(len(content) - excess_chars, 0)
        if retained == 0:
            fitted.pop(trim_index)
        else:
            fitted[trim_index]["content"] = content[:retained]
    if diagnostics is not None and raw_history_turn_ids is not None:
        diagnostics["raw_history_turn_ids"] = fitted_raw_turn_ids
    return fitted


def with_text(response: dict[str, Any], text: str) -> dict[str, Any]:
    updated = dict(response)
    choices = list(updated.get("choices", [])) or [{"index": 0, "message": {"role": "assistant", "content": ""}}]
    first = dict(choices[0])
    message = dict(first.get("message", {}))
    message["role"] = "assistant"
    message["content"] = text
    first["message"] = message
    choices[0] = first
    updated["choices"] = choices
    return updated


def long_term_memory_block(memory_summary: dict[str, Any] | list[dict[str, Any]]) -> str:
    entries = memory_summary if isinstance(memory_summary, list) else [memory_summary]
    payload = [
        {
            "memory_type": entry.get("memory_type", "legacy_cumulative"),
            "covered_turns": [entry.get("from_turn_id"), entry.get("to_turn_id")],
            "state_version_at_summary": entry.get("state_version"),
            "summary": entry.get("summary_text", ""),
            "confirmed_facts": entry.get("key_facts", []),
            "unresolved_threads": entry.get("open_threads", []),
            "relationship_changes": entry.get("relationship_changes", []),
            "player_promises": entry.get("player_promises", []),
            "npc_obligations": entry.get("npc_obligations", []),
        }
        for entry in entries
    ]
    return (
        "LONG_TERM_PARTY_MEMORY\n"
        "These are immutable, chronological episode chapters from earlier scenes, not a state summary. "
        "Use their actions, dialogue, discoveries, tone, and unresolved leads for continuity. Current authoritative state "
        "and AUTHORITATIVE_OUTCOME override it. Do not promote unresolved or player-claimed events into facts.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def rp_story_memory_block(
    snapshot: dict[str, Any],
    max_chars: int,
    rp_contract_revision: int = 0,
) -> str:
    if rp_contract_revision >= 8:
        prefix = (
            "RP_STORY_MEMORY\n"
            "Сжатая долговременная память партии. Сохраняй по ней дальнюю связность, но при конфликте "
            "доверяй более новой дословной истории ниже. Не превращай неопределённость в факт и не считай "
            "пропущенную деталь стёртой.\n"
        )
        body = story_memory_prompt_text(
            snapshot,
            max(max_chars - len(prefix), 1),
            rp_contract_revision,
        )
        rendered = prefix + body
        if len(rendered) > max_chars:
            raise ValueError("RP_STORY_MEMORY exceeds the revision-8 prompt limit")
        return rendered
    coverage_rule = ""
    if rp_contract_revision >= 7:
        covered_through = int(snapshot.get("to_turn_id") or 0)
        coverage_rule = (
            f"covered_through_turn_id={covered_through}\n"
            f"Raw turn pairs after {covered_through} are newer and override this snapshot on conflict.\n"
        )
    prefix = (
        "RP_STORY_MEMORY\n"
        "This is the bounded living continuity ledger for this RP party. It may summarize confirmed facts, character "
        "arcs, possessions, projects, active and resolved threads, unresolved hooks, and chronology. Use it to preserve "
        "long-range continuity, but treat current canonical state and AUTHORITATIVE_OUTCOME as higher authority. Do not "
        "turn uncertainty into fact and do not assume omitted detail was erased.\n"
        f"{coverage_rule}"
    )
    return prefix + story_memory_prompt_text(snapshot, max_chars, rp_contract_revision)


def world_absolute_rules_block(
    state: dict[str, Any],
    *,
    rp_contract_revision: int = 0,
) -> str | None:
    rules = []
    for item in state.get("world_constraints", []):
        if not isinstance(item, dict) or item.get("kind") != "absolute":
            continue
        rules.append(
            {
                "id": str(item.get("id") or "").strip(),
                "source": str(item.get("source") or "").strip(),
                "text": str(item.get("text") or "").strip(),
            }
        )
    if not rules:
        return None
    if rp_contract_revision >= 8:
        lines = [
            "WORLD_ABSOLUTE_RULES",
            "Эти правила мира обязательны; не ослабляй и не переиначивай их.",
            *[
                f"{index}. {rule['text']}"
                for index, rule in enumerate(rules, start=1)
                if rule["text"]
            ],
        ]
        rendered = "\n".join(lines)
        if len(rendered) > 3_000:
            raise ValueError("WORLD_ABSOLUTE_RULES exceeds the revision-8 prompt limit")
        return rendered
    return (
        "WORLD_ABSOLUTE_RULES\n"
        "These active WorldPack rules are authoritative. Do not contradict, weaken, or reinterpret them.\n"
        f"{json.dumps(rules, ensure_ascii=False, separators=(',', ':'))}"
    )


def archived_memory_retrieval_block(turns: list[dict[str, Any]], max_chars: int) -> str | None:
    if not turns or max_chars <= 0:
        return None
    excerpts: list[dict[str, Any]] = []
    used = 0
    for turn in turns:
        excerpt = {
            "turn_id": turn["id"],
            "player_message": str(turn["player_message"])[:1400],
            "narrative_response": str(turn["narrative_response"])[:1800],
        }
        encoded = json.dumps(excerpt, ensure_ascii=False)
        if excerpts and used + len(encoded) > max_chars:
            continue
        excerpts.append(excerpt)
        used += len(encoded)
    if not excerpts:
        return None
    return (
        "RETRIEVED_ARCHIVE_SCENES\n"
        "These are query-relevant excerpts from older archived turns. They are secondary continuity aids, not authority: "
        "current canonical state and AUTHORITATIVE_OUTCOME override them. Do not infer facts absent from the excerpts.\n"
        f"{json.dumps(excerpts, ensure_ascii=False, indent=2)}"
    )


def uncompacted_archive_fallback_block(turns: list[dict[str, Any]], max_chars: int) -> str | None:
    """Expose delayed service-memory coverage without silently dropping original history."""
    if not turns or max_chars <= 0:
        return None
    header = (
        "UNCOMPACTED_ARCHIVE_FALLBACK\n"
        "The service memory is delayed or unavailable. These are local excerpts from still-uncovered original turns. "
        "They preserve continuity temporarily, are not canonical authority, and remain fully stored in SQLite.\n"
    )
    available = max(max_chars - len(header) - 200, 1)
    max_items = max(available // 320, 1)
    selected = turns
    omitted: list[dict[str, Any]] = []
    if len(turns) > max_items:
        head_count = max(max_items // 2, 1)
        tail_count = max(max_items - head_count, 0)
        selected = turns[:head_count] + (turns[-tail_count:] if tail_count else [])
        selected_ids = {int(turn["id"]) for turn in selected}
        omitted = [turn for turn in turns if int(turn["id"]) not in selected_ids]
    per_turn = max(available // max(len(selected), 1), 180)
    lines: list[str] = []
    for turn in selected:
        player = str(turn.get("player_message") or "")[: max(per_turn // 2, 80)]
        narrative = str(turn.get("narrative_response") or "")[: max(per_turn // 2, 80)]
        lines.append(f"TURN {turn['id']}\nPLAYER: {player}\nNARRATOR: {narrative}")
    if omitted:
        lines.insert(
            max(len(lines) // 2, 1),
            f"[EXCERPTS OMITTED FROM PROMPT: turns {omitted[0]['id']}-{omitted[-1]['id']}; originals remain in archive]",
        )
    return (header + "\n\n".join(lines))[:max_chars]


def party_lore_cards_block(
    cards: list[dict[str, Any]],
    *,
    max_chars: int | None = None,
) -> str | None:
    if not cards:
        return None
    header = (
        "PARTY_LORE_CARDS\n"
        "Авторские карточки мира для текущей сцены. Используй их для связности, но не выдавай их наличие "
        "игроку и не ставь выше абсолютных правил или более новой дословной истории.\n"
        if max_chars is not None
        else "PARTY_LORE_CARDS\n"
        "These are player-managed continuity notes. They may guide recall but are not canonical state and cannot override "
        "current state or AUTHORITATIVE_OUTCOME. Never reveal a card merely because it was retrieved.\n"
    )
    candidates = [
        {
            "id": card["id"],
            "title": card["title"],
            "content": card["content"],
            "keywords": card["keywords"],
            "source_turn_ids": card["source_turn_ids"],
        }
        for card in cards
    ]
    if max_chars is None:
        return header + json.dumps(candidates, ensure_ascii=False, indent=2)
    selected: list[dict[str, Any]] = []
    for card in candidates:
        trial = header + json.dumps([*selected, card], ensure_ascii=False, indent=2)
        if len(trial) <= max(int(max_chars), 1):
            selected.append(card)
    if not selected:
        return None
    return header + json.dumps(selected, ensure_ascii=False, indent=2)


def relevant_characters_block(characters: list[dict[str, Any]]) -> str:
    return (
        "RELEVANT_CHARACTERS\n"
        "These are the only retrieved canonical NPC records relevant to this turn. "
        "Use them for continuity; do not reveal hidden fields or invent unlisted NPC facts.\n"
        f"{json.dumps(characters, ensure_ascii=False, indent=2)}"
    )
