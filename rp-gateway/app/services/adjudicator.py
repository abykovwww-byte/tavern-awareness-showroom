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
from app.services.memory import MemorySummarizer
from app.services.narrative import ProviderRateLimitError, NarrativeClient, response_text, with_text
from app.services.rp_story_memory import RPStoryMemoryUpdater
from app.services.relationship_extraction import RelationshipExtractionService
from app.services.relationships import RelationshipMechanics
from app.services.rule_engine import RuleEngine
from app.services.state_store import StateStore
from app.services.training_artifacts import ArtifactMaterialization, TrainingArtifactService
from app.services.training_runtime import TrainingRuntimeService
from app.services.training_workspace import TrainingWorkspaceService, WorkspaceMaterialization
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
    _service_tasks: dict[str, asyncio.Task[None]] = {}

    def __init__(
        self,
        settings: Settings,
        store: StateStore,
        training_artifacts: TrainingArtifactService | None = None,
        training_workspace: TrainingWorkspaceService | None = None,
        training_runtime: TrainingRuntimeService | None = None,
        relationship_model: dict[str, Any] | None = None,
    ):
        self.settings = settings
        self.store = store
        self.intent_parser = IntentParser()
        self.rule_engine = RuleEngine()
        self.validator = OutputValidator()
        self.narrative = NarrativeClient(settings)
        self.memory = MemorySummarizer(settings, store)
        self.rp_story_memory = RPStoryMemoryUpdater(settings, store) if settings.scenario_type == "rp" else None
        self.world = WorldInstructor(settings, store)
        self.training_artifacts = training_artifacts
        self.training_workspace = training_workspace
        self.training_runtime = training_runtime
        self.relationship_model = relationship_model if settings.scenario_type == "rp" else None
        self.relationship_mechanics = (
            RelationshipMechanics(store, self.relationship_model)
            if self.relationship_model is not None
            else None
        )
        self.relationship_extraction = (
            RelationshipExtractionService(settings, store, self.relationship_model)
            if self.relationship_model is not None
            else None
        )

    async def handle_chat(
        self,
        request: ChatCompletionRequest,
        authorization: str | None,
        idempotency_key: str | None,
        request_id: str | None = None,
        allow_gateway_fallback: bool = True,
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
                self.store.record_turn(
                    idempotency_key,
                    request_id,
                    latest,
                    text,
                    response,
                    state_version,
                    metadata=self.turn_metadata(
                        turn_kind="world_command",
                        validator_valid=None,
                        repaired=False,
                        fallback_reason=None,
                        transport_status="ok",
                    ),
                )
                self.store.complete_turn_request(idempotency_key, response)
                await self.after_turn_recorded(authorization, request_id)
                return response

            state = self.store.get_state()
            intent = self.intent_parser.parse(latest)
            artifact_evidence = self.training_artifacts.pending_evidence() if self.training_artifacts else []
            workspace_evidence = self.training_workspace.pending_evidence() if self.training_workspace else []
            interaction_evidence = [*artifact_evidence, *workspace_evidence]
            outcome, patch = self.rule_engine.resolve(
                state,
                intent,
                request_id,
                campaign_id=self.settings.campaign_id,
                scenario_type=self.settings.scenario_type,
                interaction_evidence=interaction_evidence,
                training_runtime=self.training_runtime,
            )
            narrative_state = self.preview_applied_state(patch)
            artifact_contract = (
                self.training_artifacts.contract_for_state(narrative_state)
                if self.training_artifacts
                else None
            )
            workspace_contract = (
                self.training_workspace.contract_for_state(narrative_state)
                if self.training_workspace
                else None
            )
            interaction_contract = (
                {"site": artifact_contract, "workspace": workspace_contract}
                if artifact_contract or workspace_contract
                else None
            )
            training_turn_contract = (
                self.training_runtime.prompt_contract(narrative_state, interaction_contract)
                if self.training_runtime and self.training_runtime.enabled
                else None
            )

            llm_calls = 0
            repaired = False
            provider_fallback_reason: str | None = None
            gateway_fallback_reason: str | None = None
            transport_status = "ok"
            prompt_messages: list[dict[str, str]] | None = None
            artifact_result: ArtifactMaterialization | None = None
            workspace_result: WorkspaceMaterialization | None = None
            try:
                relationship_pressure = self.relationship_pressure(narrative_state)
                memory_summary = self.store.memory_for_prompt(self.settings.party_memory_prompt_max_chars)
                rp_story_memory = self.store.latest_rp_story_memory() if self.settings.scenario_type == "rp" else None
                prompt_messages = self.narrative.narrative_messages(
                    request,
                    narrative_state,
                    outcome,
                    repair_instruction=None,
                    memory_summary=memory_summary,
                    rp_story_memory=rp_story_memory,
                    artifact_contract=interaction_contract,
                    training_turn_contract=training_turn_contract,
                    relationship_pressure=relationship_pressure,
                )
                llm_calls += 1
                raw = await self.narrative.complete(
                    request,
                    narrative_state,
                    outcome,
                    authorization,
                    memory_summary=memory_summary,
                    rp_story_memory=rp_story_memory,
                    request_id=request_id,
                    artifact_contract=interaction_contract,
                    training_turn_contract=training_turn_contract,
                    relationship_pressure=relationship_pressure,
                )
                text = response_text(raw)
                if self.training_artifacts:
                    artifact_result = self.training_artifacts.materialize_response(raw, artifact_contract)
                    if artifact_result.valid:
                        text = artifact_result.text
                if self.training_workspace:
                    workspace_result = self.training_workspace.materialize_response(raw, workspace_contract)
                    if workspace_result.valid:
                        text = workspace_result.text
                if self.training_runtime and self.training_runtime.enabled:
                    text = self.training_runtime.normalize_narrative(text, narrative_state, interaction_contract)
                if (artifact_result is None or artifact_result.valid) and (workspace_result is None or workspace_result.valid):
                    raw = self.merge_interaction_response(raw, text, artifact_result, workspace_result)
                if self.settings.scenario_type == "rp" and not text.strip():
                    raise RuntimeError("Narrative provider returned an invalid response")
                validation = None if self.settings.scenario_type == "rp" else self.validator.validate(
                    text,
                    outcome,
                    narrative_state,
                    campaign_id=self.settings.campaign_id,
                    latest_user_message=latest,
                    scenario_type=self.settings.scenario_type,
                    training_runtime=self.training_runtime,
                    interaction_contract=interaction_contract,
                )
                interaction_valid = (artifact_result.valid if artifact_result else True) and (
                    workspace_result.valid if workspace_result else True
                )
                training_runtime_enabled = bool(self.training_runtime and self.training_runtime.enabled)
                repair_attempts = (
                    self.settings.training_repair_attempts
                    if training_runtime_enabled
                    else self.settings.max_repair_attempts
                )
                training_repair_allowed = True
                if training_runtime_enabled and self.training_runtime:
                    runtime_violations = self.training_runtime.validate_narrative(
                        text, narrative_state, interaction_contract
                    )
                    runtime_violation_set = set(runtime_violations)
                    training_repair_allowed = not self.training_runtime.hard_violations(
                        text, narrative_state, interaction_contract
                    ) and not any(
                        violation not in runtime_violation_set for violation in validation.violations
                    )
                if validation is not None and (
                    (not validation.valid or not interaction_valid)
                    and repair_attempts > 0
                    and training_repair_allowed
                ):
                    repaired = True
                    repair_instruction = (
                        self.training_runtime.repair_instruction(text, narrative_state, interaction_contract)
                        if training_runtime_enabled and self.training_runtime
                        else validation.repair_instruction
                    )
                    if artifact_result and not artifact_result.valid:
                        repair_instruction = " ".join(
                            [
                                repair_instruction,
                                "Верни корректный JSON bundle: объект artifact должен содержать только разрешённые ключи и slots; fixed display_url оставь только в строке «Ссылки:» видимого narrative_text.",
                            ]
                        ).strip()
                    if workspace_result and not workspace_result.valid:
                        repair_instruction = " ".join(
                            [
                                repair_instruction,
                                "Верни корректный workspace_files только с разрешёнными file_key, blueprint_id и строковыми slots.",
                            ]
                        ).strip()
                    llm_calls += 1
                    raw = await self.narrative.complete(
                        request,
                        narrative_state,
                        outcome,
                        authorization,
                        repair_instruction,
                        failed_response_text=text,
                        memory_summary=memory_summary,
                        rp_story_memory=rp_story_memory,
                        request_id=request_id,
                        artifact_contract=interaction_contract,
                        training_turn_contract=training_turn_contract,
                    )
                    text = response_text(raw)
                    if self.training_artifacts:
                        artifact_result = self.training_artifacts.materialize_response(raw, artifact_contract)
                        if artifact_result.valid:
                            text = artifact_result.text
                    if self.training_workspace:
                        workspace_result = self.training_workspace.materialize_response(raw, workspace_contract)
                        if workspace_result.valid:
                            text = workspace_result.text
                    if self.training_runtime and self.training_runtime.enabled:
                        text = self.training_runtime.normalize_narrative(text, narrative_state, interaction_contract)
                    if (artifact_result is None or artifact_result.valid) and (workspace_result is None or workspace_result.valid):
                        raw = self.merge_interaction_response(raw, text, artifact_result, workspace_result)
                    validation = self.validator.validate(
                        text,
                        outcome,
                        narrative_state,
                        campaign_id=self.settings.campaign_id,
                        latest_user_message=latest,
                        scenario_type=self.settings.scenario_type,
                        training_runtime=self.training_runtime,
                        interaction_contract=interaction_contract,
                    )
                interaction_valid = (artifact_result.valid if artifact_result else True) and (
                    workspace_result.valid if workspace_result else True
                )
                if validation is not None and (not validation.valid or not interaction_valid):
                    gateway_fallback_reason = "validation_failed"
                    transport_status = "invalid_response"
                    self.store.audit(
                        "llm_validation_failed",
                        {
                            "request_id": request_id,
                            "model": self.settings.narrative_model,
                            "violations": [
                                *validation.violations,
                                *(artifact_result.violations if artifact_result else []),
                                *(workspace_result.violations if workspace_result else []),
                            ],
                        },
                        request_id,
                    )
                    if not allow_gateway_fallback:
                        raise RuntimeError("LLM response failed narrative validation")
                    text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                    raw = self.provider_fallback_response(
                        outcome,
                        text,
                        gateway_fallback_reason,
                        request_id,
                    )
            except PermissionError:
                raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                self.store.audit(
                    "llm_http_error",
                    {"request_id": request_id, "model": self.settings.narrative_model, "status": status},
                    request_id,
                )
                if self.settings.scenario_type == "rp" or not allow_gateway_fallback:
                    raise RuntimeError(f"Narrative provider HTTP {status}") from exc
                provider_fallback_reason = f"http_{status}"
                transport_status = "provider_error"
                text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                raw = self.provider_fallback_response(outcome, text, provider_fallback_reason, request_id)
            except httpx.TimeoutException as exc:
                self.store.audit("llm_timeout", {"request_id": request_id, "model": self.settings.narrative_model}, request_id)
                if self.settings.scenario_type == "rp" or not allow_gateway_fallback:
                    raise RuntimeError("Narrative provider timed out") from exc
                provider_fallback_reason = "timeout"
                transport_status = "provider_timeout"
                text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                raw = self.provider_fallback_response(outcome, text, provider_fallback_reason, request_id)
            except ProviderRateLimitError as exc:
                self.store.audit("llm_rate_limited", {"request_id": request_id, **exc.details}, request_id)
                if self.settings.scenario_type == "rp" or not allow_gateway_fallback:
                    raise
                provider_fallback_reason = "rate_limited"
                transport_status = "provider_error"
                text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                raw = self.provider_fallback_response(outcome, text, provider_fallback_reason, request_id)
            except RuntimeError as exc:
                if self.settings.scenario_type == "rp" or not allow_gateway_fallback:
                    raise
                provider_fallback_reason = "runtime_error"
                transport_status = "provider_error"
                self.store.audit(
                    "llm_runtime_error",
                    {"request_id": request_id, "model": self.settings.narrative_model, "error": str(exc)},
                    request_id,
                )
                text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                raw = self.provider_fallback_response(outcome, text, provider_fallback_reason, request_id)

            if self.training_artifacts and artifact_contract:
                if (
                    artifact_result is None
                    or not artifact_result.valid
                    or provider_fallback_reason is not None
                    or gateway_fallback_reason is not None
                ):
                    artifact_result = self.training_artifacts.fallback_materialization(raw, text, artifact_contract)
                text = artifact_result.text
            if self.training_workspace and workspace_contract:
                if (
                    workspace_result is None
                    or not workspace_result.valid
                    or provider_fallback_reason is not None
                    or gateway_fallback_reason is not None
                ):
                    workspace_result = self.training_workspace.fallback_materialization(workspace_contract, text)
                text = workspace_result.text
            raw = self.merge_interaction_response(raw, text, artifact_result, workspace_result)

            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response = self.normalize_response(raw, request.model or self.settings.narrative_model)
            text = response_text(response)
            updated_state = self.store.apply_state_patch(patch, reason=f"turn:{request_id}")
            version = int(updated_state.get("meta", {}).get("state_version", 0))
            final_validation = None if self.settings.scenario_type == "rp" else self.validator.validate(
                text,
                outcome,
                updated_state,
                campaign_id=self.settings.campaign_id,
                latest_user_message=latest,
                scenario_type=self.settings.scenario_type,
                training_runtime=self.training_runtime,
                interaction_contract=interaction_contract,
            )
            turn_id = self.store.record_turn(
                idempotency_key,
                request_id,
                latest,
                text,
                response,
                version,
                prompt_messages,
                self.turn_metadata(
                    turn_kind="narrative",
                    validator_valid=final_validation.valid if final_validation is not None else None,
                    repaired=repaired,
                    fallback_reason=provider_fallback_reason or gateway_fallback_reason,
                    transport_status=transport_status,
                    outcome=outcome.model_dump(mode="json"),
                    llm_calls=llm_calls,
                    interaction_evidence=[item.model_dump(mode="json") for item in interaction_evidence],
                ),
                artifacts=artifact_result.persistence_records if artifact_result else [],
                consumed_artifact_event_ids=[item.event_sequence for item in artifact_evidence],
                workspace_files=workspace_result.persistence_records if workspace_result else [],
                consumed_workspace_event_ids=[item.event_sequence for item in workspace_evidence],
            )
            self.store.complete_turn_request(idempotency_key, response)
            if self.settings.scenario_type == "rp":
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
                    "validator_valid": final_validation.valid if final_validation is not None else None,
                    "repair": repaired,
                    "fallback_reason": provider_fallback_reason or gateway_fallback_reason,
                    "provider_fallback_reason": provider_fallback_reason,
                    "gateway_fallback_reason": gateway_fallback_reason,
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

    def turn_metadata(
        self,
        *,
        turn_kind: str,
        validator_valid: bool | None,
        repaired: bool,
        fallback_reason: str | None,
        transport_status: str,
        outcome: dict[str, Any] | None = None,
        llm_calls: int = 0,
        interaction_evidence: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": "rp-gateway.turn.v1",
            "turn_kind": turn_kind,
            "scenario_type": self.settings.scenario_type,
            "worldpack_id": self.settings.campaign_id,
            "state_campaign_id": self.store.campaign_id,
            "narrative_provider": self.settings.llm_provider,
            "narrative_model": self.settings.narrative_model,
            "generated_by": "autotest" if self.store.campaign_id.find("--branch_") >= 0 else "human",
            "validator_valid": validator_valid,
            "repaired": repaired,
            "fallback": fallback_reason is not None,
            "fallback_reason": fallback_reason,
            "transport_status": transport_status,
            "llm_calls": llm_calls,
            "outcome": outcome,
            "interaction_evidence": interaction_evidence or [],
            "training_runtime_contract_hash": (
                self.training_runtime.contract_hash
                if self.training_runtime and self.training_runtime.enabled
                else None
            ),
            "training_capabilities": {
                "interactive_links_enabled": bool(self.training_artifacts and self.training_artifacts.enabled),
                "interactive_workspace_enabled": bool(self.training_workspace and self.training_workspace.enabled),
            },
        }

    def preview_applied_state(self, patch: Any) -> dict[str, Any]:
        candidate = self.store.preview_patch(patch)
        version = self.store.current_version() or int(candidate.get("meta", {}).get("state_version", 1))
        candidate.setdefault("meta", {})
        candidate["meta"]["state_version"] = version + 1
        candidate["meta"]["turn"] = max(int(candidate["meta"].get("turn", 0)) + 1, patch.turn)
        candidate["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        candidate.setdefault("last_turn", {})
        candidate["last_turn"]["turn"] = candidate["meta"]["turn"]
        candidate["last_turn"]["state_patch_id"] = patch.check_id or f"gateway-v{version + 1}"
        return candidate

    def safe_text(
        self,
        outcome: Outcome,
        state: dict[str, Any],
        latest_user_message: str,
        interaction_contract: dict[str, Any] | None,
    ) -> str:
        if self.training_runtime and self.training_runtime.enabled:
            return self.training_runtime.fallback_text(state, interaction_contract)
        return safe_fallback(
            outcome,
            state,
            latest_user_message,
            self.settings.campaign_id,
            self.settings.scenario_type,
        )

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
        job_types = ["memory"]
        if self.settings.scenario_type == "rp":
            job_types.append("rp_story_memory")
            if self.relationship_extraction is not None:
                job_types.append("relationship_extraction")
        for job_type in job_types:
            self.store.enqueue_service_job(job_type, request_id, self.settings.service_job_max_attempts)
        if self.settings.post_turn_helpers_inline and self.settings.app_env == "test":
            await self.drain_service_jobs(authorization, wait_for_retries=False)
            return
        self.schedule_service_jobs(authorization)

    def schedule_service_jobs(self, authorization: str | None = None) -> None:
        campaign_id = self.store.campaign_id
        if campaign_id in self._post_turn_helper_campaigns:
            return
        self._post_turn_helper_campaigns.add(campaign_id)
        task = asyncio.create_task(self.drain_service_jobs(authorization, wait_for_retries=True))
        self._service_tasks[campaign_id] = task
        task.add_done_callback(lambda completed: self.post_turn_helpers_done(campaign_id, completed))

    async def drain_service_jobs(self, authorization: str | None, wait_for_retries: bool) -> None:
        while True:
            job = self.store.due_service_job()
            if job is None:
                delay = self.store.next_service_job_delay()
                if not wait_for_retries or delay is None:
                    return
                await asyncio.sleep(max(min(delay, self.settings.service_job_retry_max_seconds), 1))
                continue
            running = self.store.mark_service_job_running(int(job["id"]))
            try:
                await self.run_service_job(running, authorization)
                self.store.complete_service_job(int(running["id"]))
            except Exception as exc:  # noqa: BLE001 - service work must never affect gameplay
                attempts = max(int(running["attempts"]), 1)
                delay = min(
                    self.settings.service_job_retry_base_seconds * (3 ** (attempts - 1)),
                    self.settings.service_job_retry_max_seconds,
                )
                self.store.retry_service_job(int(running["id"]), f"{type(exc).__name__}: {exc}", delay)
                self.store.audit(
                    "service_job_retry",
                    {
                        "job_id": running["id"],
                        "job_type": running["job_type"],
                        "attempt": attempts,
                        "retry_delay_seconds": delay,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                    running.get("request_id"),
                )

    async def run_service_job(self, job: dict[str, Any], authorization: str | None) -> None:
        for _ in range(64):
            if job["job_type"] == "memory":
                result = await self.memory.summarize(
                    authorization,
                    fail_open=True,
                    request_id=job.get("request_id"),
                )
            elif job["job_type"] == "rp_story_memory" and self.rp_story_memory is not None:
                result = await self.rp_story_memory.update(
                    authorization,
                    fail_open=True,
                    request_id=job.get("request_id"),
                )
            elif job["job_type"] == "relationship_extraction" and self.relationship_extraction is not None:
                turn = self.store.get_turn_by_request_id(str(job.get("request_id") or ""))
                if turn is None:
                    raise ValueError("relationship extraction turn not found")
                result = await self.relationship_extraction.process_turn(
                    int(turn["id"]),
                    authorization,
                )
            elif job["job_type"] == "journal":
                # Retire jobs queued by versions that still had a party journal.
                # Returning a terminal no-op prevents endless retries after upgrade.
                return
            else:
                raise ValueError(f"unsupported service job type: {job['job_type']}")
            if result.get("generated"):
                continue
            if result.get("reason") == "summary_failed" or result.get("error"):
                raise RuntimeError(str(result.get("reason") or result.get("error") or "service job failed"))
            return
        raise RuntimeError(f"{job['job_type']} service job exceeded 64 batches")

    def relationship_pressure(self, state: dict[str, Any]) -> str | None:
        if self.relationship_mechanics is None:
            return None
        turn_id = int(state.get("meta", {}).get("turn", 0))
        self.relationship_mechanics.advance_turn(turn_id)
        characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
        names = {
            str(character_id): self.relationship_character_name(str(character_id), value)
            for character_id, value in characters.items()
        }
        return self.relationship_mechanics.pressure_block(turn_id, names)

    @staticmethod
    def relationship_character_name(character_id: str, value: Any) -> str:
        if isinstance(value, dict):
            explicit = value.get("name") or value.get("display_name")
            if isinstance(explicit, str) and explicit.strip():
                return explicit.strip()
        return character_id.replace("-", " ").replace("_", " ").title()

    def post_turn_helpers_done(self, campaign_id: str, completed: asyncio.Task[None]) -> None:
        self._post_turn_helper_campaigns.discard(campaign_id)
        self._service_tasks.pop(campaign_id, None)
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

    @staticmethod
    def merge_interaction_response(
        raw: dict[str, Any],
        text: str,
        artifact_result: ArtifactMaterialization | None,
        workspace_result: WorkspaceMaterialization | None,
    ) -> dict[str, Any]:
        response = with_text(raw, text)
        message = response.setdefault("choices", [{}])[0].setdefault("message", {})
        if artifact_result and artifact_result.public_artifacts:
            message["artifacts"] = artifact_result.public_artifacts
        else:
            message.pop("artifacts", None)
        if workspace_result and workspace_result.public_files:
            message["workspace_files"] = workspace_result.public_files
        else:
            message.pop("workspace_files", None)
        return response

    def normalize_response(self, raw: dict[str, Any], requested_model: str) -> dict[str, Any]:
        response = dict(raw)
        response.setdefault("id", f"chatcmpl-{uuid.uuid4().hex[:24]}")
        response.setdefault("object", "chat.completion")
        response.setdefault("created", int(time.time()))
        response["model"] = response.get("model") or requested_model
        response.setdefault("choices", [{"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}])
        return response
