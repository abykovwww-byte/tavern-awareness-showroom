"""One-turn gateway orchestration."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, Outcome
from app.services.intent_parser import IntentParser
from app.services.journal import JournalBuilder
from app.services.memory import MemorySummarizer
from app.services.narrative import NarrativeClient, response_text, with_text
from app.services.rule_engine import RuleEngine
from app.services.state_store import StateStore
from app.services.validator import OutputValidator, safe_fallback
from app.services.world_instructor import WorldInstructor


logger = logging.getLogger(__name__)


class RequestAlreadyRunning(RuntimeError):
    def __init__(self, request_id: str, idempotency_key: str):
        super().__init__("request is already running")
        self.request_id = request_id
        self.idempotency_key = idempotency_key


class Adjudicator:
    _post_turn_helper_campaigns: set[str] = set()

    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store
        self.intent_parser = IntentParser()
        self.rule_engine = RuleEngine()
        self.validator = OutputValidator()
        self.narrative = NarrativeClient(settings)
        self.memory = MemorySummarizer(settings, store)
        self.journal = JournalBuilder(settings, store)
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
        request_status = self.store.begin_turn_request(idempotency_key, request_id)
        if not request_status.get("acquired"):
            if request_status.get("status") == "completed" and request_status.get("response"):
                return request_status["response"]
            if request_status.get("status") == "running":
                raise RequestAlreadyRunning(
                    str(request_status.get("request_id") or request_id),
                    idempotency_key,
                )

        started = time.perf_counter()
        try:
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
                self.store.complete_turn_request(idempotency_key, response)
                await self.after_turn_recorded(authorization, request_id)
                return response

            if not authorization and not self.settings.nvidia_api_key:
                raise PermissionError("NVIDIA API key is required in Authorization header or NVIDIA_API_KEY env")

            state = self.store.get_state()
            intent = self.intent_parser.parse(latest)
            outcome, patch = self.rule_engine.resolve(state, intent, request_id)
            updated_state = self.store.apply_state_patch(patch, reason=f"turn:{request_id}")
            self.store.record_check(None, outcome)

            llm_calls = 0
            repaired = False
            provider_fallback_reason: str | None = None
            prompt_messages: list[dict[str, str]] | None = None
            try:
                memory_summary = self.store.latest_memory_summary()
                prompt_messages = self.narrative.narrative_messages(
                    request,
                    updated_state,
                    outcome,
                    repair_instruction=None,
                    memory_summary=memory_summary,
                )
                raw = await self.narrative.complete(
                    request,
                    updated_state,
                    outcome,
                    authorization,
                    memory_summary=memory_summary,
                    request_id=request_id,
                )
                llm_calls += 1
                text = response_text(raw)
                validation = self.validator.validate(text, outcome)
                if not validation.valid and self.settings.max_repair_attempts > 0:
                    repaired = True
                    raw = await self.narrative.complete(
                        request,
                        updated_state,
                        outcome,
                        authorization,
                        validation.repair_instruction,
                        memory_summary=memory_summary,
                        request_id=request_id,
                    )
                    llm_calls += 1
                    text = response_text(raw)
                    validation = self.validator.validate(text, outcome)
                if not validation.valid:
                    text = safe_fallback(outcome, updated_state, latest)
                    raw = with_text(raw, text)
            except PermissionError:
                raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                self.store.audit(
                    "llm_http_error",
                    {"request_id": request_id, "model": self.settings.narrative_model, "status": status},
                    request_id,
                )
                provider_fallback_reason = f"http_{status}"
                text = safe_fallback(outcome, updated_state, latest)
                raw = self.provider_fallback_response(outcome, text, provider_fallback_reason, request_id)
            except httpx.TimeoutException as exc:
                self.store.audit("llm_timeout", {"request_id": request_id, "model": self.settings.narrative_model}, request_id)
                provider_fallback_reason = "timeout"
                text = safe_fallback(outcome, updated_state, latest)
                raw = self.provider_fallback_response(outcome, text, provider_fallback_reason, request_id)
            except RuntimeError as exc:
                provider_fallback_reason = "runtime_error"
                self.store.audit(
                    "llm_runtime_error",
                    {"request_id": request_id, "model": self.settings.narrative_model, "error": str(exc)},
                    request_id,
                )
                text = safe_fallback(outcome, updated_state, latest)
                raw = self.provider_fallback_response(outcome, text, provider_fallback_reason, request_id)

            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response = self.normalize_response(raw, request.model or self.settings.narrative_model)
            text = response_text(response)
            version = int(updated_state.get("meta", {}).get("state_version", 0))
            turn_id = self.store.record_turn(idempotency_key, request_id, latest, text, response, version, prompt_messages)
            self.store.complete_turn_request(idempotency_key, response)
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
                    "provider_fallback_reason": provider_fallback_reason,
                    "check_id": outcome.check_id,
                    "result": outcome.result,
                },
                request_id,
            )
            await self.after_turn_recorded(authorization, request_id)
            return response
        except Exception as exc:
            self.store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            raise

    def provider_fallback_response(self, outcome: Outcome, text: str, reason: str, request_id: str) -> dict[str, Any]:
        self.store.audit(
            "llm_safe_fallback",
            {
                "request_id": request_id,
                "check_id": outcome.check_id,
                "model": self.settings.narrative_model,
                "reason": reason,
            },
            request_id,
        )
        return {
            "id": f"fallback-{outcome.check_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.settings.narrative_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "provider_fallback",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    async def after_turn_recorded(self, authorization: str | None, request_id: str) -> None:
        if self.settings.post_turn_helpers_inline:
            await self.run_post_turn_helpers(authorization, request_id)
            return
        campaign_id = self.store.campaign_id
        if campaign_id in self._post_turn_helper_campaigns:
            self.store.audit("post_turn_helpers_skipped", {"reason": "already_running"}, request_id)
            return
        self._post_turn_helper_campaigns.add(campaign_id)
        task = asyncio.create_task(self.run_post_turn_helpers(authorization, request_id))
        task.add_done_callback(lambda completed: self.post_turn_helpers_done(campaign_id, completed))

    async def run_post_turn_helpers(self, authorization: str | None, request_id: str) -> None:
        try:
            await self.memory.summarize(authorization, fail_open=True, request_id=request_id)
            await self.journal.summarize(authorization, fail_open=True, request_id=request_id)
        except Exception as exc:  # noqa: BLE001 - background helpers must never affect gameplay
            logger.warning(
                "post_turn_helpers_failed campaign_id=%s request_id=%s error=%s",
                self.store.campaign_id,
                request_id,
                exc,
            )

    def post_turn_helpers_done(self, campaign_id: str, completed: asyncio.Task[None]) -> None:
        self._post_turn_helper_campaigns.discard(campaign_id)
        if completed.cancelled():
            logger.warning("post_turn_helpers_cancelled campaign_id=%s", campaign_id)
            return
        exc = completed.exception()
        if exc:
            logger.warning("post_turn_helpers_task_failed campaign_id=%s error=%s", campaign_id, exc)

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
