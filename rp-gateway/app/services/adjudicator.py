"""One-turn gateway orchestration."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest
from app.services.intent_parser import IntentParser
from app.services.narrative import NarrativeClient, response_text, with_text
from app.services.rule_engine import RuleEngine
from app.services.state_store import StateStore
from app.services.validator import OutputValidator, safe_fallback
from app.services.world_instructor import WorldInstructor


class Adjudicator:
    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store
        self.intent_parser = IntentParser()
        self.rule_engine = RuleEngine()
        self.validator = OutputValidator()
        self.narrative = NarrativeClient(settings)
        self.world = WorldInstructor(settings, store)

    async def handle_chat(
        self,
        request: ChatCompletionRequest,
        authorization: str | None,
        idempotency_key: str | None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or f"req_{uuid.uuid4().hex}"
        idempotency_key = idempotency_key or request_id
        existing = self.store.get_turn_by_idempotency(idempotency_key)
        if existing:
            return existing

        started = time.perf_counter()
        latest = self.latest_user_message(request)
        if self.world.is_world_command(latest):
            response = await self.world.handle_chat_command(
                latest,
                authorization,
                request.model or self.settings.narrative_model,
                request_id,
            )
            text = response_text(response)
            state_version = self.store.current_version() or 1
            self.store.record_turn(idempotency_key, request_id, latest, text, response, state_version)
            return response

        state = self.store.get_state()
        intent = self.intent_parser.parse(latest)
        outcome, patch = self.rule_engine.resolve(state, intent, request_id)
        updated_state = self.store.apply_state_patch(patch, reason=f"turn:{request_id}")
        self.store.record_check(None, outcome)

        llm_calls = 0
        repaired = False
        try:
            raw = await self.narrative.complete(request, state, outcome, authorization)
            llm_calls += 1
            text = response_text(raw)
            validation = self.validator.validate(text, outcome)
            if not validation.valid and self.settings.max_repair_attempts > 0:
                repaired = True
                raw = await self.narrative.complete(request, state, outcome, authorization, validation.repair_instruction)
                llm_calls += 1
                text = response_text(raw)
                validation = self.validator.validate(text, outcome)
            if not validation.valid:
                text = safe_fallback(outcome)
                raw = with_text(raw, text)
        except PermissionError:
            raise
        except httpx.TimeoutException as exc:
            self.store.audit("llm_timeout", {"request_id": request_id, "model": self.settings.narrative_model}, request_id)
            raise RuntimeError("Narrative provider timeout") from exc

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response = self.normalize_response(raw, request.model or self.settings.narrative_model)
        text = response_text(response)
        version = int(updated_state.get("meta", {}).get("state_version", 0))
        turn_id = self.store.record_turn(idempotency_key, request_id, latest, text, response, version)
        self.store.record_check(turn_id, outcome)
        self.store.audit(
            "turn_complete",
            {
                "request_id": request_id,
                "turn_id": turn_id,
                "campaign_id": self.settings.campaign_id,
                "duration_ms": duration_ms,
                "llm_calls": llm_calls,
                "model": self.settings.narrative_model,
                "validator_valid": self.validator.validate(text, outcome).valid,
                "repair": repaired,
                "check_id": outcome.check_id,
                "result": outcome.result,
            },
            request_id,
        )
        return response

    def latest_user_message(self, request: ChatCompletionRequest) -> str:
        for message in reversed(request.messages):
            if message.role == "user" and isinstance(message.content, str):
                return message.content
        return ""

    def normalize_response(self, raw: dict[str, Any], requested_model: str) -> dict[str, Any]:
        response = dict(raw)
        response.setdefault("id", f"chatcmpl-{uuid.uuid4().hex[:24]}")
        response.setdefault("object", "chat.completion")
        response.setdefault("created", int(time.time()))
        response["model"] = response.get("model") or requested_model
        response.setdefault("choices", [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}])
        return response
