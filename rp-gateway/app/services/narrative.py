"""Narrative LLM client and OpenAI-compatible response helpers."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, Outcome


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
    ) -> dict[str, Any]:
        if self.settings.nvidia_api_base.startswith("mock://"):
            return self.mock_completion(outcome, repair_instruction)

        authorization = inbound_authorization
        if self.settings.nvidia_api_key:
            authorization = f"Bearer {self.settings.nvidia_api_key}"
        if not authorization:
            raise PermissionError("NVIDIA API key is required in Authorization header or NVIDIA_API_KEY env")

        payload = request.model_dump(exclude_none=True)
        payload["model"] = self.settings.narrative_model
        payload["messages"] = self.narrative_messages(request, state, outcome, repair_instruction)
        payload["stream"] = False

        timeout = httpx.Timeout(self.settings.request_timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.settings.nvidia_api_base.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": authorization, "Content-Type": "application/json"},
            )
        if response.status_code == 429:
            raise RuntimeError("NVIDIA API returned 429 rate limit")
        response.raise_for_status()
        return response.json()

    def narrative_messages(
        self,
        request: ChatCompletionRequest,
        state: dict[str, Any],
        outcome: Outcome,
        repair_instruction: str | None,
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
            {"role": "system", "content": f"Relevant state summary: {state_summary}"},
            {"role": "system", "content": outcome.authoritative_block},
        ]
        for message in request.messages[-12:]:
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
