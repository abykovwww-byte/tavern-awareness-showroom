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
            raise PermissionError("NVIDIA API key is required in Authorization header or NVIDIA_API_KEY env")

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
                    raise RuntimeError("NVIDIA API returned 429 rate limit")
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
        raise RuntimeError("No NVIDIA model attempts configured")

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
            "turn": state.get("meta", {}).get("turn"),
            "player": state.get("player", {}),
            "relationships": state.get("relationships", {}),
            "constraints": state.get("world_constraints", []),
        }
        rules = (
            "You are the narrator. The RP Gateway already decided the mechanical outcome. "
            "Describe it as fiction. Do not reroll, change the Result, create hidden success, "
            "invent missing resources, or expose service JSON."
        )
        if repair_instruction:
            rules += f" Repair instruction: {repair_instruction}"
        messages = [
            {"role": "system", "content": rules},
        ]
        if memory_summary:
            messages.append({"role": "system", "content": long_term_memory_block(memory_summary)})
        messages.extend(
            [
                {"role": "system", "content": f"Relevant state summary: {state_summary}"},
                {"role": "system", "content": outcome.authoritative_block},
            ]
        )
        for message in request.messages[-24:]:
            if isinstance(message.content, str):
                messages.append({"role": message.role, "content": message.content})
        return messages

    def mock_completion(self, outcome: Outcome, repair_instruction: str | None) -> dict[str, Any]:
        mode = self.settings.nvidia_api_base.removeprefix("mock://")
        if mode == "timeout":
            raise httpx.TimeoutException("mock timeout")
        if mode == "rate-limit":
            raise RuntimeError("NVIDIA API returned 429 rate limit")
        if mode == "violate" and not repair_instruction:
            content = "Despite the failure, the king secretly grants equivalent military authority."
        elif mode == "repair-fail":
            content = "Despite the failure, the king still transfers command authority."
        else:
            content = f"The scene follows the fixed result: {outcome.result}. {' '.join(outcome.consequences)}"
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
