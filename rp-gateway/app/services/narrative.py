"""Narrative LLM client and OpenAI-compatible response helpers."""

from __future__ import annotations

import logging
import json
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, Outcome


logger = logging.getLogger(__name__)


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
        memory_summary: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if self.settings.nvidia_api_base.startswith("mock://"):
            return self.mock_completion(outcome, repair_instruction)

        authorization = inbound_authorization
        if self.settings.nvidia_api_key:
            authorization = f"Bearer {self.settings.nvidia_api_key}"
        if not authorization:
            raise PermissionError(f"API key is required for provider {self.settings.llm_provider}")

        payload = request.model_dump(exclude_none=True)
        payload["messages"] = self.narrative_messages(request, state, outcome, repair_instruction, memory_summary)
        payload["stream"] = False

        timeout = httpx.Timeout(self.settings.model_attempt_timeout_seconds, connect=15.0)
        attempts = self.model_attempts(self.settings.narrative_model)
        last_timeout: httpx.TimeoutException | None = None
        last_status: httpx.HTTPStatusError | None = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, model in enumerate(attempts):
                payload["model"] = model
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
                    response = await client.post(
                        f"{self.settings.nvidia_api_base.rstrip('/')}/chat/completions",
                        json=payload,
                        headers={"Authorization": authorization, "Content-Type": "application/json"},
                    )
                except httpx.TimeoutException as exc:
                    last_timeout = exc
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
                        continue
                    raise
                if response.status_code == 429:
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    logger.warning(
                        "llm_attempt_rate_limited request_id=%s check_id=%s model=%s elapsed_ms=%s",
                        request_id,
                        outcome.check_id,
                        model,
                        elapsed_ms,
                    )
                    raise RuntimeError(f"{self.settings.llm_provider} API returned 429 rate limit")
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
                    if index < len(attempts) - 1 and response.status_code in {400, 404, 408, 500, 502, 503, 504}:
                        continue
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
        memory_summary: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        state_summary = {
            "campaign_id": state.get("meta", {}).get("campaign_id"),
            "worldpack_id": self.settings.campaign_id,
            "turn": state.get("meta", {}).get("turn"),
            "player": state.get("player", {}),
            "relationships": state.get("relationships", {}),
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
        if memory_summary:
            messages.append({"role": "system", "content": long_term_memory_block(memory_summary)})
        messages.extend(
            [
                {"role": "system", "content": f"Relevant state summary: {state_summary}"},
                {"role": "system", "content": outcome.authoritative_block},
            ]
        )
        for message in request.messages:
            if isinstance(message.content, str):
                messages.append({"role": message.role, "content": message.content})
        return messages

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
        return common + (
            "You are the GM and narrator of a roleplaying game. The RP Gateway has already resolved the D20 check. "
            "Describe that fixed result as fiction. Do not reroll, change the result, create an equivalent hidden "
            "success after failure, bypass hard constraints, or expose roll calculations unless the player-facing "
            "world rules explicitly require them. End with a playable opening for the next player action."
        )

    def mock_completion(self, outcome: Outcome, repair_instruction: str | None) -> dict[str, Any]:
        mode = self.settings.nvidia_api_base.removeprefix("mock://")
        if mode == "timeout":
            raise httpx.TimeoutException("mock timeout")
        if mode == "http-503":
            request = httpx.Request("POST", "https://mock.nvidia.local/chat/completions")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("mock provider unavailable", request=request, response=response)
        if mode == "rate-limit":
            raise RuntimeError(f"{self.settings.llm_provider} API returned 429 rate limit")
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
        return {
            "id": f"mock-{outcome.check_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.settings.narrative_model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        }


def response_text(response: dict[str, Any]) -> str:
    return str(response.get("choices", [{}])[0].get("message", {}).get("content", ""))


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


def long_term_memory_block(memory_summary: dict[str, Any]) -> str:
    payload = {
        "covered_turns": [memory_summary.get("from_turn_id"), memory_summary.get("to_turn_id")],
        "state_version_at_summary": memory_summary.get("state_version"),
        "summary": memory_summary.get("summary_text", ""),
        "confirmed_facts": memory_summary.get("key_facts", []),
        "unresolved_threads": memory_summary.get("open_threads", []),
        "relationship_changes": memory_summary.get("relationship_changes", []),
        "player_promises": memory_summary.get("player_promises", []),
        "npc_obligations": memory_summary.get("npc_obligations", []),
    }
    return (
        "LONG_TERM_PARTY_MEMORY\n"
        "Use this as campaign context only. Current authoritative state and AUTHORITATIVE_OUTCOME override it. "
        "Do not promote unresolved or player-claimed events into facts.\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
