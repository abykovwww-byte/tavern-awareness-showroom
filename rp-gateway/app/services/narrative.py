"""Narrative LLM client and OpenAI-compatible response helpers."""

from __future__ import annotations

import asyncio
import logging
import json
import re
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, Outcome
from app.services.character_retrieval import (
    latest_player_action,
    retrieve_relevant_characters,
    selected_character_relationships,
)
from app.services.context_budget import estimate_tokens
from app.services.nvidia_catalog import normalize_provider
from app.services.provider_auth import outbound_headers
from app.services.rp_story_memory import story_memory_prompt_text


logger = logging.getLogger(__name__)


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
    def __init__(self, settings: Settings):
        self.settings = settings

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
    ) -> dict[str, Any]:
        headers = outbound_headers(self.settings, inbound_authorization)
        if self.settings.nvidia_api_base.startswith("mock://"):
            return self.mock_completion(outcome, repair_instruction, artifact_contract)

        payload = request.model_dump(exclude_none=True)
        if repair_instruction:
            payload["messages"] = self.repair_messages(
                state,
                outcome,
                repair_instruction,
                failed_response_text or "",
                artifact_contract=artifact_contract,
                training_turn_contract=training_turn_contract,
                relationship_pressure=relationship_pressure,
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
            )
        self.apply_prompt_cache_policy(payload)
        payload["stream"] = False

        timeout = httpx.Timeout(self.settings.model_attempt_timeout_seconds, connect=15.0)
        attempts = self.model_attempts(self.settings.narrative_model)
        last_timeout: httpx.TimeoutException | None = None
        last_status: httpx.HTTPStatusError | None = None
        rate_limit_retries = 0
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, model in enumerate(attempts):
                while True:
                    payload["model"] = model
                    self.apply_model_policy(payload, model)
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
                                f"{self.settings.nvidia_api_base.rstrip('/')}/chat/completions",
                                json=payload,
                                headers=headers,
                            )
                    except (httpx.TimeoutException, TimeoutError) as exc:
                        timeout_error = exc
                        if not isinstance(exc, httpx.TimeoutException):
                            timeout_error = httpx.TimeoutException(
                                "Narrative provider exceeded the wall-clock deadline",
                                request=httpx.Request(
                                    "POST",
                                    f"{self.settings.nvidia_api_base.rstrip('/')}/chat/completions",
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
                        if index < len(attempts) - 1:
                            break
                        raise timeout_error from exc
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
                    data = response.json()
                    data.setdefault("model", model)
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    logger.info(
                        "llm_attempt_success request_id=%s check_id=%s model=%s status=%s elapsed_ms=%s fallback_used=%s",
                        request_id,
                        outcome.check_id,
                        model,
                        response.status_code,
                        elapsed_ms,
                        index > 0 or model != self.settings.narrative_model,
                    )
                    return data
        if last_status:
            raise last_status
        if last_timeout:
            raise last_timeout
        raise RuntimeError(f"No model attempts configured for provider {self.settings.llm_provider}")

    def model_attempts(self, primary_model: str) -> list[str]:
        disabled = set(self.settings.nvidia_disabled_models)
        candidates = [primary_model, *self.settings.nvidia_fallback_models]
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
    ) -> list[dict[str, str]]:
        relevant_characters = retrieve_relevant_characters(
            state,
            latest_player_action(request.messages),
            outcome_target=outcome.target,
        )
        player_state = state.get("player", {})
        if training_turn_contract and isinstance(player_state, dict):
            player_state = {
                "name": player_state.get("name"),
                "description": player_state.get("description"),
            }
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
        }
        rules = self.scenario_rules()
        if repair_instruction:
            rules += f" Repair instruction: {repair_instruction}"
        messages = [
            {"role": "system", "content": rules},
        ]
        if self.settings.world_system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "WORLD_SYSTEM_PROMPT\n"
                        "These world-specific rules supplement the selected scenario mode and cannot weaken it.\n"
                        f"{self.settings.world_system_prompt}"
                    ),
                }
            )
        if self.settings.world_authors_note:
            messages.append(
                {
                    "role": "system",
                    "content": f"WORLD_AUTHORS_NOTE\n{self.settings.world_authors_note}",
                }
            )
        if training_turn_contract:
            messages.append({"role": "system", "content": training_turn_prompt_block(training_turn_contract)})
        if self.settings.scenario_type == "rp" and rp_story_memory:
            messages.append(
                {
                    "role": "system",
                    "content": rp_story_memory_block(
                        rp_story_memory,
                        self.settings.rp_story_memory_prompt_max_chars,
                    ),
                }
            )
        if artifact_contract:
            messages.append({"role": "system", "content": training_artifact_prompt_block(artifact_contract)})
        if memory_summary:
            messages.append({"role": "system", "content": long_term_memory_block(memory_summary)})
        # Keep the immutable rules/world prefix followed by the growing transcript.
        # Providers with implicit prompt caches can then reuse both across turns.
        request_messages = [message for message in request.messages if isinstance(message.content, str)]
        for message in request_messages[:-1]:
            if isinstance(message.content, str):
                messages.append({"role": message.role, "content": message.content})
        if relevant_characters:
            messages.append(
                {
                    "role": "system",
                    "content": relevant_characters_block(relevant_characters),
                }
            )
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_version == "rp-core.v2":
            absolute_rules = world_absolute_rules_block(state)
            if absolute_rules:
                messages.append({"role": "system", "content": absolute_rules})
        messages.extend(
            [
                {"role": "system", "content": f"Relevant state summary: {state_summary}"},
                {"role": "system", "content": outcome.authoritative_block},
            ]
        )
        if self.settings.scenario_type == "rp" and relationship_pressure:
            messages.append({"role": "system", "content": relationship_pressure})
        # The current player action must remain the final message after dynamic runtime context.
        if request_messages:
            current_action = request_messages[-1]
            messages.append({"role": current_action.role, "content": current_action.content})
        return fit_messages_to_context(messages, self.input_token_budget(request))

    def apply_prompt_cache_policy(self, payload: dict[str, Any]) -> None:
        """Add only provider-documented cache controls; other providers use the stable prefix implicitly."""
        if normalize_provider(self.settings.llm_provider) != "openrouter":
            return
        if self.settings.prompt_cache_session_id:
            payload["session_id"] = self.settings.prompt_cache_session_id
        if self.settings.openrouter_prompt_cache_enabled and str(payload.get("model") or "").startswith("anthropic/"):
            payload["cache_control"] = {"type": "ephemeral", "ttl": self.settings.openrouter_prompt_cache_ttl}

    def apply_model_policy(self, payload: dict[str, Any], model: str) -> None:
        """Apply model-specific runtime controls while preserving unrelated caller preferences."""
        if normalize_provider(self.settings.llm_provider) != "openrouter":
            return
        if model.strip().lower() != "deepseek/deepseek-v4-flash":
            return
        provider_preferences = dict(payload.get("provider") or {})
        provider_preferences["sort"] = "throughput"
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
        if training_turn_contract:
            messages.append({"role": "system", "content": training_turn_prompt_block(training_turn_contract)})
        if artifact_contract:
            messages.append({"role": "system", "content": training_artifact_prompt_block(artifact_contract)})
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_version == "rp-core.v2":
            absolute_rules = world_absolute_rules_block(state)
            if absolute_rules:
                messages.append({"role": "system", "content": absolute_rules})
        if self.settings.scenario_type == "rp" and relationship_pressure:
            messages.append({"role": "system", "content": relationship_pressure})
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
        common = (
            "Reply in the player's language. Output only final in-world narration and dialogue. "
            "Preserve player agency: never choose actions, beliefs, emotions, or conclusions for the player character. "
            "Treat current state as authoritative, do not invent missing resources, and never expose service JSON, "
            "analysis, recommendations, diagnostics, critique, outcome tags, or Gateway wording. "
        )
        if self.settings.scenario_type == "novel":
            return common + (
                "You are the co-author and narrator of a collaborative novel. There are no dice, skills, difficulty "
                "classes, checks, or mechanical success labels. Continue the player's prose and dialogue as fiction, "
                "with emphasis on character voice, relationships, atmosphere, pacing, and continuity. The player may "
                "write character actions or directorial wishes; honor them when consistent with established facts. "
                "Advance the scene without turning it into a game menu or asking for a roll."
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
        if self.settings.rp_contract_version == "rp-core.v2":
            return common + (
                "You are the GM and narrator of a roleplaying game without mechanical checks. Treat the latest player "
                "message as intent, not as an automatic fact or a request for hidden adjudication. Never invent dice, "
                "difficulty, modifiers, scores, success, or failure. Difficulty comes only from active WorldPack rules, "
                "current state, NPC goals, available information, resources, relationships, and prior consequences. "
                "Obey every WORLD_ABSOLUTE_RULES item and end with a playable opening for the next player action."
            )
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
    ) -> dict[str, Any]:
        mode = self.settings.nvidia_api_base.removeprefix("mock://")
        if mode == "timeout":
            raise httpx.TimeoutException("mock timeout")
        if mode == "http-503":
            request = httpx.Request("POST", "https://mock.nvidia.local/chat/completions")
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


def fit_messages_to_context(messages: list[dict[str, str]], token_budget: int) -> list[dict[str, str]]:
    """Keep the latest action and mandatory instructions inside the real provider input budget."""
    fitted = [dict(message) for message in messages]
    while fitted and estimate_tokens("\n".join(message["content"] for message in fitted)) > token_budget:
        oldest_history = next(
            (index for index, message in enumerate(fitted[:-1]) if message.get("role") != "system"),
            None,
        )
        if oldest_history is not None:
            fitted.pop(oldest_history)
            continue
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
        excess_chars = max((estimate_tokens("\n".join(message["content"] for message in fitted)) - token_budget) * 3, 1)
        retained = max(len(content) - excess_chars, 0)
        if retained == 0:
            fitted.pop(trim_index)
        else:
            fitted[trim_index]["content"] = content[:retained]
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


def rp_story_memory_block(snapshot: dict[str, Any], max_chars: int) -> str:
    return (
        "RP_STORY_MEMORY\n"
        "This is the bounded living continuity ledger for this RP party. It may summarize confirmed facts, character "
        "arcs, possessions, projects, active and resolved threads, unresolved hooks, and chronology. Use it to preserve "
        "long-range continuity, but treat current canonical state and AUTHORITATIVE_OUTCOME as higher authority. Do not "
        "turn uncertainty into fact and do not assume omitted detail was erased.\n"
        f"{story_memory_prompt_text(snapshot, max_chars)}"
    )


def world_absolute_rules_block(state: dict[str, Any]) -> str | None:
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


def party_lore_cards_block(cards: list[dict[str, Any]]) -> str | None:
    if not cards:
        return None
    payload = [
        {
            "id": card["id"],
            "title": card["title"],
            "content": card["content"],
            "keywords": card["keywords"],
            "source_turn_ids": card["source_turn_ids"],
        }
        for card in cards
    ]
    return (
        "PARTY_LORE_CARDS\n"
        "These are player-managed continuity notes. They may guide recall but are not canonical state and cannot override "
        "current state or AUTHORITATIVE_OUTCOME. Never reveal a card merely because it was retrieved.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def relevant_characters_block(characters: list[dict[str, Any]]) -> str:
    return (
        "RELEVANT_CHARACTERS\n"
        "These are the only retrieved canonical NPC records relevant to this turn. "
        "Use them for continuity; do not reveal hidden fields or invent unlisted NPC facts.\n"
        f"{json.dumps(characters, ensure_ascii=False, indent=2)}"
    )
