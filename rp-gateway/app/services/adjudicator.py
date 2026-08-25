"""One-turn gateway orchestration."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

import httpx

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome, PatchOperation, StatePatch
from app.services.character_retrieval import relationship_scene_character_ids
from app.services.context_budget import estimate_tokens
from app.services.intent_parser import IntentParser
from app.services.memory import MemorySummarizer
from app.services.narrative import (
    NarrativeClient,
    PromptBudgetExceeded,
    ProviderRateLimitError,
    archived_memory_retrieval_block,
    party_lore_cards_block,
    prompt_cache_observability,
    prompt_assembly_diagnostics,
    response_text,
    with_text,
)
from app.services.rp_story_memory import RPStoryMemoryUpdater
from app.services.relationship_attribution import normalized_aliases
from app.services.relationship_extraction import RelationshipExtractionService
from app.services.relationships import RelationshipMechanics
from app.services.rule_engine import RuleEngine
from app.services.rp_history import (
    eligible_rp_turns,
    raw_history_window,
    recent_rp_scan_text,
    removable_covered_history_units,
    rp_turn_messages,
    story_memory_safe_coverage,
)
from app.services.scene_state import (
    SceneMaterialization,
    fallback_scene_state,
    initial_scene_state,
    materialize_scene_bundle,
    scene_state_boundary_block,
    unresolved_noncanonical_fallback_turns,
)
from app.services.state_store import StateStore, StateVersionConflict
from app.services.training_artifacts import ArtifactMaterialization, TrainingArtifactService
from app.services.training_runtime import TrainingRuntimeService
from app.services.training_workspace import TrainingWorkspaceService, WorkspaceMaterialization
from app.services.trace_redaction import redact_trace_value
from app.services.validator import OutputValidator, safe_fallback
from app.services.world_instructor import WorldInstructor


logger = logging.getLogger(__name__)


class RequestAlreadyRunning(RuntimeError):
    def __init__(self, request_id: str, idempotency_key: str):
        super().__init__("request is already running")
        self.request_id = request_id
        self.idempotency_key = idempotency_key


class SceneContinuityError(RuntimeError):
    """The narrator bundle still violates hard scene continuity after repair."""


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
        scene_contract: dict[str, Any] | None = None,
    ):
        self.settings = settings
        self.store = store
        self.intent_parser = IntentParser()
        self.rule_engine = RuleEngine()
        self.validator = OutputValidator()
        self.narrative = NarrativeClient(settings, trace_recorder=store.record_narrative_attempt)
        self.memory = MemorySummarizer(settings, store)
        self.rp_story_memory = RPStoryMemoryUpdater(settings, store) if settings.scenario_type == "rp" else None
        self.world = WorldInstructor(settings, store)
        self.training_artifacts = training_artifacts
        self.training_workspace = training_workspace
        self.training_runtime = training_runtime
        self.relationship_model = relationship_model if settings.scenario_type == "rp" else None
        self.scene_contract = scene_contract if settings.scenario_type == "rp" else None
        self.relationship_mechanics = (
            RelationshipMechanics(
                store,
                self.relationship_model,
                rp_contract_revision=settings.rp_contract_revision,
            )
            if self.relationship_model is not None
            else None
        )
        self.relationship_extraction = (
            RelationshipExtractionService(settings, store, self.relationship_model)
            if self.relationship_model is not None
            else None
        )

    def record_trace_event(self, **event: Any) -> None:
        """Trace capture is best-effort and can never decide a game turn."""

        try:
            safe_event = dict(event)
            safe_event["payload"] = redact_trace_value(
                event.get("payload", {}),
                self.narrative.trace_secrets(),
            )
            self.store.record_trace_event(**safe_event)
        except Exception as exc:  # noqa: BLE001 - diagnostics are deliberately fail-open
            logger.warning(
                "turn_trace_event_failed request_id=%s phase=%s error=%s",
                event.get("request_id"),
                event.get("phase_key"),
                f"{type(exc).__name__}: {exc}",
            )

    def trace_projection_snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        try:
            return self.store.trace_projection_snapshot()
        except Exception as exc:  # noqa: BLE001 - diagnostics are deliberately fail-open
            logger.warning("turn_trace_projection_snapshot_failed error=%s", f"{type(exc).__name__}: {exc}")
            return {}

    def capture_projection_changes(
        self,
        request_id: str,
        before: dict[str, dict[str, dict[str, Any]]],
        *,
        source: str,
        reason: str,
        lane: str = "background",
    ) -> None:
        try:
            self.store.capture_projection_changes(
                request_id,
                before,
                source=source,
                reason=reason,
                lane=lane,
            )
        except Exception as exc:  # noqa: BLE001 - diagnostics are deliberately fail-open
            logger.warning(
                "turn_trace_projection_capture_failed request_id=%s source=%s error=%s",
                request_id,
                source,
                f"{type(exc).__name__}: {exc}",
            )

    async def handle_chat(
        self,
        request: ChatCompletionRequest,
        authorization: str | None,
        idempotency_key: str | None,
        request_id: str | None = None,
        allow_gateway_fallback: bool = True,
        story_memory_corrections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or f"req_{uuid.uuid4().hex}"
        idempotency_key = idempotency_key or request_id
        existing = self.store.get_turn_by_idempotency(idempotency_key)
        if existing:
            return existing
        request_status = self.store.begin_turn_request(idempotency_key, request_id)
        request_id = str(request_status.get("request_id") or request_id)
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
            expected_party_turn = int(self.store.get_state().get("meta", {}).get("turn", 0)) + 1
            self.record_trace_event(
                request_id=request_id,
                phase_key="player_input",
                alignment_key="player_input",
                lane="main",
                event_type="player_input",
                status="completed",
                payload={"input": {"content": latest}},
                party_turn=expected_party_turn,
            )
            normalized_story_corrections: list[dict[str, str]] = []
            if story_memory_corrections:
                if self.rp_story_memory is None:
                    raise ValueError("story-memory corrections are only available for RP parties")
                normalized_story_corrections = self.rp_story_memory.validate_corrections(
                    story_memory_corrections
                )
            if self.world.is_world_command(latest):
                self.record_trace_event(
                    request_id=request_id,
                    phase_key="gateway_assembly",
                    alignment_key="gateway_assembly",
                    lane="main",
                    event_type="gateway_assembly",
                    status="skipped",
                    payload={
                        "capture_status": "complete",
                        "reason": "not_applicable_world_command",
                        "input": {"messages": []},
                    },
                    party_turn=expected_party_turn,
                )
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
                    prompt_messages=[],
                    party_turn=int(self.store.get_state().get("meta", {}).get("turn", 0)),
                    metadata=self.turn_metadata(
                        turn_kind="world_command",
                        validator_valid=None,
                        repaired=False,
                        fallback_reason=None,
                        transport_status="ok",
                        story_memory_corrections=normalized_story_corrections,
                    ),
                )
                self.store.complete_turn_request(idempotency_key, response)
                if self.settings.rp_contract_revision < 8:
                    await self.after_turn_recorded(authorization, request_id)
                return response

            state = self.store.get_state()
            expected_state_version = int(state.get("meta", {}).get("state_version") or 0)
            rp_no_checks = (
                self.settings.scenario_type == "rp"
                and self.settings.rp_contract_revision >= 1
            )
            intent = self.intent_parser.parse(latest, mechanical=not rp_no_checks)
            artifact_evidence = self.training_artifacts.pending_evidence() if self.training_artifacts else []
            workspace_evidence = self.training_workspace.pending_evidence() if self.training_workspace else []
            interaction_evidence = [*artifact_evidence, *workspace_evidence]
            outcome, patch = self.rule_engine.resolve(
                state,
                intent,
                request_id,
                campaign_id=self.settings.campaign_id,
                scenario_type=self.settings.scenario_type,
                rp_contract_version=self.settings.rp_contract_version,
                rp_contract_revision=self.settings.rp_contract_revision,
                interaction_evidence=interaction_evidence,
                training_runtime=self.training_runtime,
                character_aliases=(
                    normalized_aliases(self.relationship_model or {})
                    if self.settings.scenario_type == "rp"
                    else None
                ),
                authored_stable_affiliations=self.authored_stable_affiliations(),
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
            revision_seven = (
                self.settings.scenario_type == "rp"
                and self.settings.rp_contract_revision >= 7
            )
            revision_eight = (
                self.settings.scenario_type == "rp"
                and self.settings.rp_contract_revision >= 8
            )
            if revision_eight:
                self.refresh_revision_eight_lore_cards(
                    request,
                    latest_player_message=latest,
                    outcome_target=outcome.target,
                )
            scene_bundle_revision = (
                self.settings.scenario_type == "rp"
                and self.settings.rp_contract_revision == 7
            )
            provider_fallback_reason: str | None = None
            gateway_fallback_reason: str | None = None
            transport_status = "ok"
            prompt_messages: list[dict[str, str]] | None = None
            prompt_assembly: dict[str, Any] | None = None
            prompt_cache_response: dict[str, Any] | None = None
            artifact_result: ArtifactMaterialization | None = None
            workspace_result: WorkspaceMaterialization | None = None
            scene_result: SceneMaterialization | None = None
            scene_before = (
                initial_scene_state(state, self.authored_stable_affiliations())
                if scene_bundle_revision
                else None
            )
            bundle_received = False
            fallback_noncanonical = False
            try:
                relationship_projection_before = self.trace_projection_snapshot()
                relationship_pressure = self.relationship_pressure(
                    narrative_state,
                    latest_player_message=latest,
                    outcome_target=outcome.target,
                )
                self.capture_projection_changes(
                    request_id,
                    relationship_projection_before,
                    source="relationship_turn_advance",
                    reason="prepare_relationship_pressure",
                    lane="main",
                )
                memory_summary = (
                    None
                    if self.settings.rp_contract_revision >= 8
                    else self.store.memory_for_prompt(
                        self.settings.party_memory_prompt_max_chars
                    )
                )
                rp_story_memory: dict[str, Any] | None = None
                story_snapshot_id: int | None = None
                story_coverage = 0
                raw_tail_turn_ids: list[int] = []
                prompt_diagnostics: dict[str, Any] = {}

                def assemble_prompt() -> list[dict[str, str]]:
                    return self.narrative.narrative_messages(
                        request,
                        narrative_state,
                        outcome,
                        repair_instruction=None,
                        memory_summary=memory_summary,
                        rp_story_memory=rp_story_memory,
                        artifact_contract=interaction_contract,
                        training_turn_contract=training_turn_contract,
                        relationship_pressure=relationship_pressure,
                        diagnostics=prompt_diagnostics if revision_seven else None,
                    )

                refresh_attempted = False
                before_refresh: dict[str, Any] | None = None
                refresh: dict[str, Any] | None = None
                for _assembly_pass in range(2):
                    try:
                        snapshot_stable = False
                        for _snapshot_read in range(3 if revision_seven else 1):
                            rp_story_memory = (
                                self.rp_story_memory.prompt_snapshot(
                                    normalized_story_corrections
                                )
                                if self.rp_story_memory
                                else None
                            )
                            if revision_seven:
                                story_snapshot_id, story_coverage, raw_tail_turn_ids = (
                                    self.rebuild_revision_seven_request(
                                        request,
                                        rp_story_memory,
                                        latest,
                                    )
                                )
                            prompt_messages = assemble_prompt()
                            if not revision_seven or self.rp_story_memory is None:
                                snapshot_stable = True
                                break
                            final_story_memory = self.rp_story_memory.prompt_snapshot(
                                normalized_story_corrections
                            )
                            final_snapshot_id = (
                                int(final_story_memory["id"])
                                if final_story_memory is not None
                                and final_story_memory.get("id") is not None
                                else None
                            )
                            final_coverage = (
                                story_memory_safe_coverage(final_story_memory)
                                if self.settings.rp_contract_revision >= 8
                                else int(final_story_memory.get("to_turn_id") or 0)
                                if final_story_memory is not None
                                else 0
                            )
                            if (
                                story_snapshot_id != final_snapshot_id
                                or story_coverage != final_coverage
                            ):
                                continue
                            snapshot_stable = True
                            break
                        if not snapshot_stable:
                            raise RuntimeError(
                                "RP story-memory snapshot did not stabilize before narrator call"
                            )
                    except PromptBudgetExceeded as overflow:
                        if (
                            not revision_seven
                            or self.settings.rp_contract_revision >= 8
                            or self.rp_story_memory is None
                        ):
                            raise
                        if refresh_attempted:
                            refresh_result = refresh or {}
                            refresh_before = before_refresh or {}
                            self.store.audit(
                                "rp_story_memory_force_refresh",
                                {
                                    "request_id": request_id,
                                    "pending_turns_before": refresh_before.get("pending_turns"),
                                    "pending_turns_after": refresh_result.get("stats", {}).get(
                                        "pending_turns"
                                    ),
                                    "batches": refresh_result.get("batches"),
                                    "coverage_before": refresh_result.get("coverage_before"),
                                    "coverage_after": refresh_result.get("coverage_after"),
                                    "result": refresh_result.get("terminal_result"),
                                    "hard_overflow": True,
                                    "estimated_tokens": overflow.estimated_tokens,
                                    "token_budget": overflow.token_budget,
                                },
                                request_id,
                            )
                            raise

                        refresh_attempted = True
                        before_refresh = self.rp_story_memory.stats()
                        refresh_batches = 0

                        def rebuilt_prompt_fits(checkpoint: dict[str, Any]) -> bool:
                            nonlocal rp_story_memory, refresh_batches
                            refresh_batches = int(checkpoint.get("batches") or 0)
                            rp_story_memory = self.rp_story_memory.prompt_snapshot(
                                normalized_story_corrections
                            )
                            self.rebuild_revision_seven_request(
                                request,
                                rp_story_memory,
                                latest,
                            )
                            try:
                                assemble_prompt()
                            except PromptBudgetExceeded:
                                return False
                            return True

                        try:
                            async with asyncio.timeout(
                                self.settings.model_attempt_timeout_seconds
                            ):
                                refresh = await self.rp_story_memory.catch_up(
                                    authorization,
                                    force=True,
                                    fail_open=False,
                                    request_id=request_id,
                                    stop_when=rebuilt_prompt_fits,
                                )
                        except Exception as refresh_error:
                            after_refresh = self.rp_story_memory.stats()
                            self.store.audit(
                                "rp_story_memory_force_refresh_failed",
                                {
                                    "request_id": request_id,
                                    "pending_turns": before_refresh.get("pending_turns"),
                                    "batches": refresh_batches,
                                    "coverage_before": int(
                                        before_refresh.get("covered_through_turn_id") or 0
                                    ),
                                    "coverage_after": int(
                                        after_refresh.get("covered_through_turn_id") or 0
                                    ),
                                    "result": "failed",
                                    "hard_overflow": True,
                                    "estimated_tokens": overflow.estimated_tokens,
                                    "token_budget": overflow.token_budget,
                                    "error_type": type(refresh_error).__name__,
                                },
                                request_id,
                            )
                            raise overflow from refresh_error
                        continue

                    if refresh_attempted and refresh is not None:
                        self.store.audit(
                            "rp_story_memory_force_refresh",
                            {
                                "request_id": request_id,
                                "pending_turns_before": (before_refresh or {}).get(
                                    "pending_turns"
                                ),
                                "pending_turns_after": refresh.get("stats", {}).get(
                                    "pending_turns"
                                ),
                                "batches": refresh.get("batches"),
                                "coverage_before": refresh.get("coverage_before"),
                                "coverage_after": refresh.get("coverage_after"),
                                "result": refresh.get("terminal_result"),
                                "hard_overflow": False,
                            },
                            request_id,
                        )
                    break
                if revision_seven:
                    prompt_assembly = prompt_assembly_diagnostics(
                        prompt_messages,
                        story_memory_covered_through_turn_id=story_coverage,
                        raw_tail_turn_ids=prompt_diagnostics.get(
                            "raw_history_turn_ids",
                            raw_tail_turn_ids,
                        ),
                        omitted_blocks=prompt_diagnostics.get("omitted_blocks"),
                        rp_contract_revision=self.settings.rp_contract_revision,
                    )
                assembly_details: dict[str, Any] = {
                    "message_count": len(prompt_messages),
                    "relationship_pressure_included": bool(relationship_pressure),
                    "training_turn_contract_included": bool(training_turn_contract),
                    "interaction_contract_included": bool(interaction_contract),
                    "assembly_trace": self.prompt_assembly_trace(prompt_messages, latest),
                }
                if prompt_assembly is not None:
                    assembly_details["prompt_assembly"] = prompt_assembly
                self.record_trace_event(
                    request_id=request_id,
                    phase_key="gateway_assembly",
                    alignment_key="gateway_assembly",
                    lane="main",
                    event_type="gateway_assembly",
                    status="completed",
                    payload={
                        "capture_status": "complete",
                        "input": {"messages": prompt_messages},
                        "details": assembly_details,
                    },
                    party_turn=expected_party_turn,
                )
                if revision_seven:
                    current_state_version = int(self.store.current_version() or 0)
                    if current_state_version != expected_state_version:
                        self.store.audit(
                            "state_version_conflict_pre_provider",
                            {
                                "request_id": request_id,
                                "expected_state_version": expected_state_version,
                                "current_state_version": current_state_version,
                            },
                            request_id,
                        )
                        raise StateVersionConflict(
                            "state version changed during narrator assembly: "
                            f"expected {expected_state_version}, current {current_state_version}"
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
                prompt_cache_response = raw
                bundle_received = True
                if scene_bundle_revision:
                    scene_result = materialize_scene_bundle(
                        raw,
                        state,
                        latest_user_message=latest,
                        party_turn=patch.turn,
                        authoritative_outcome={
                            **outcome.model_dump(mode="json"),
                            "scene_allowance": (
                                outcome.scene_allowance.model_dump(mode="json")
                                if outcome.scene_allowance is not None
                                else None
                            ),
                        },
                    )
                    text = scene_result.text
                    if scene_result.valid:
                        raw = with_text(raw, text)
                else:
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
                if (
                    self.settings.scenario_type == "rp"
                    and not text.strip()
                    and not (scene_bundle_revision and scene_result is not None and not scene_result.valid)
                ):
                    self.store.audit(
                        "llm_invalid_response",
                        {
                            "request_id": request_id,
                            "model": self.settings.narrative_model,
                            "reason": "empty_response",
                        },
                        request_id,
                    )
                    raise RuntimeError("Narrative provider returned an invalid response")
                validation = None if (
                    self.settings.scenario_type == "rp" and self.settings.rp_contract_revision < 3
                ) else self.validator.validate(
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
                scene_valid = scene_result.valid if scene_result is not None else True
                if validation is not None:
                    self.record_trace_event(
                        request_id=request_id,
                        phase_key="validation:initial",
                        alignment_key="validation",
                        lane="main",
                        event_type="validation",
                        status=(
                            "completed"
                            if validation.valid and interaction_valid and scene_valid
                            else "failed"
                        ),
                        payload={
                            "input": {"response": text},
                            "output": {
                                "valid": validation.valid and interaction_valid,
                                "violations": [
                                    *validation.violations,
                                    *(scene_result.violations if scene_result else []),
                                    *(artifact_result.violations if artifact_result else []),
                                    *(workspace_result.violations if workspace_result else []),
                                ],
                            },
                            "metadata": {"repair": False},
                        },
                        party_turn=expected_party_turn,
                    )
                training_runtime_enabled = bool(self.training_runtime and self.training_runtime.enabled)
                repair_attempts = (
                    1
                    if revision_seven
                    else (
                        self.settings.training_repair_attempts
                        if training_runtime_enabled
                        else self.settings.max_repair_attempts
                    )
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
                    (not validation.valid or not interaction_valid or not scene_valid)
                    and repair_attempts > 0
                    and training_repair_allowed
                ):
                    repaired = True
                    repair_instruction = (
                        self.training_runtime.repair_instruction(text, narrative_state, interaction_contract)
                        if training_runtime_enabled and self.training_runtime
                        else validation.repair_instruction
                    )
                    if scene_result is not None and not scene_result.valid:
                        repair_instruction = " ".join(
                            [repair_instruction, scene_result.repair_instruction]
                        ).strip()
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
                    if revision_seven:
                        current_state_version = int(self.store.current_version() or 0)
                        if current_state_version != expected_state_version:
                            self.store.audit(
                                "state_version_conflict_pre_provider",
                                {
                                    "request_id": request_id,
                                    "expected_state_version": expected_state_version,
                                    "current_state_version": current_state_version,
                                    "repair": True,
                                },
                                request_id,
                            )
                            raise StateVersionConflict(
                                "state version changed before narrator repair: "
                                f"expected {expected_state_version}, current {current_state_version}"
                            )
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
                        relationship_pressure=relationship_pressure,
                    )
                    if scene_bundle_revision:
                        scene_result = materialize_scene_bundle(
                            raw,
                            state,
                            latest_user_message=latest,
                            party_turn=patch.turn,
                            authoritative_outcome={
                                **outcome.model_dump(mode="json"),
                                "scene_allowance": (
                                    outcome.scene_allowance.model_dump(mode="json")
                                    if outcome.scene_allowance is not None
                                    else None
                                ),
                            },
                        )
                        text = scene_result.text
                        if scene_result.valid:
                            raw = with_text(raw, text)
                    else:
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
                    scene_valid = scene_result.valid if scene_result is not None else True
                    self.record_trace_event(
                        request_id=request_id,
                        phase_key="validation:repair",
                        alignment_key="validation",
                        lane="main",
                        event_type="validation",
                        status="completed" if validation.valid and scene_valid else "failed",
                        payload={
                            "input": {"response": text},
                            "output": {
                                "valid": validation.valid and scene_valid,
                                "violations": [
                                    *validation.violations,
                                    *(scene_result.violations if scene_result else []),
                                ],
                            },
                            "metadata": {"repair": True},
                        },
                        party_turn=expected_party_turn,
                    )
                interaction_valid = (artifact_result.valid if artifact_result else True) and (
                    workspace_result.valid if workspace_result else True
                )
                if scene_result is not None and not scene_result.valid:
                    self.store.audit(
                        "scene_continuity_failed",
                        {
                            "request_id": request_id,
                            "violations": scene_result.violations,
                            "repair_attempted": repaired,
                        },
                        request_id,
                    )
                    raise SceneContinuityError("; ".join(scene_result.violations))
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
                    if revision_seven:
                        raise RuntimeError(
                            "LLM response failed narrative validation after bundle parsing"
                            if scene_bundle_revision
                            else "LLM response failed narrative validation"
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
            except (PermissionError, PromptBudgetExceeded):
                raise
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code if exc.response is not None else "unknown"
                self.store.audit(
                    "llm_http_error",
                    {"request_id": request_id, "model": self.settings.narrative_model, "status": status},
                    request_id,
                )
                if revision_seven and bundle_received:
                    raise
                if self.settings.scenario_type == "rp" and not revision_seven:
                    raise RuntimeError(f"Narrative provider HTTP {status}") from exc
                if not allow_gateway_fallback:
                    raise
                provider_fallback_reason = f"http_{status}"
                transport_status = "provider_error"
                text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                raw = self.provider_fallback_response(
                    outcome, text, provider_fallback_reason, request_id, audit=not revision_seven
                )
                fallback_noncanonical = revision_seven
            except httpx.TimeoutException as exc:
                self.store.audit("llm_timeout", {"request_id": request_id, "model": self.settings.narrative_model}, request_id)
                if revision_seven and bundle_received:
                    raise
                if self.settings.scenario_type == "rp" and not revision_seven:
                    raise RuntimeError("Narrative provider timed out") from exc
                if not allow_gateway_fallback:
                    raise
                provider_fallback_reason = "timeout"
                transport_status = "provider_timeout"
                text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                raw = self.provider_fallback_response(
                    outcome, text, provider_fallback_reason, request_id, audit=not revision_seven
                )
                fallback_noncanonical = revision_seven
            except ProviderRateLimitError as exc:
                self.store.audit("llm_rate_limited", {"request_id": request_id, **exc.details}, request_id)
                if revision_seven and bundle_received:
                    raise
                if self.settings.scenario_type == "rp" and not revision_seven:
                    raise
                if not allow_gateway_fallback:
                    raise
                provider_fallback_reason = "rate_limited"
                transport_status = "provider_error"
                text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                raw = self.provider_fallback_response(
                    outcome, text, provider_fallback_reason, request_id, audit=not revision_seven
                )
                fallback_noncanonical = revision_seven
            except httpx.RequestError as exc:
                self.store.audit(
                    "llm_network_error",
                    {
                        "request_id": request_id,
                        "model": self.settings.narrative_model,
                        "error_type": type(exc).__name__,
                    },
                    request_id,
                )
                if revision_seven and bundle_received:
                    raise
                if self.settings.scenario_type == "rp" and not revision_seven:
                    raise RuntimeError("Narrative provider request failed") from exc
                if not allow_gateway_fallback:
                    raise
                provider_fallback_reason = "network_error"
                transport_status = "provider_error"
                text = self.safe_text(outcome, narrative_state, latest, interaction_contract)
                raw = self.provider_fallback_response(
                    outcome, text, provider_fallback_reason, request_id, audit=not revision_seven
                )
                fallback_noncanonical = revision_seven
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
            scene_after: dict[str, Any] | None = None
            if revision_seven and fallback_noncanonical:
                patch = StatePatch(
                    turn=patch.turn,
                    check_id=patch.check_id,
                    source=patch.source,
                    patch=[],
                )
            if scene_bundle_revision:
                if fallback_noncanonical:
                    scene_after = fallback_scene_state(
                        state,
                        self.authored_stable_affiliations(),
                    )
                elif scene_result is not None and scene_result.valid:
                    scene_after = scene_result.scene_state
                else:
                    raise SceneContinuityError("revision-7 turn has no valid scene projection")
                if not fallback_noncanonical:
                    patch.patch.extend(
                        self.scene_legacy_operations(state, scene_result, patch.turn)
                    )
                patch.patch.append(
                    PatchOperation(
                        op="replace" if "scene_state" in state else "add",
                        path="/scene_state",
                        value=scene_after,
                        reason="Commits the deterministic revision-7 scene projection with the turn.",
                        turn=patch.turn,
                    )
                )
            projected_state = self.preview_applied_state(patch)
            final_validation = None if (
                self.settings.scenario_type == "rp" and self.settings.rp_contract_revision < 3
            ) else self.validator.validate(
                text,
                outcome,
                projected_state,
                campaign_id=self.settings.campaign_id,
                latest_user_message=latest,
                scenario_type=self.settings.scenario_type,
                training_runtime=self.training_runtime,
                interaction_contract=interaction_contract,
            )
            if final_validation is not None and not final_validation.valid:
                raise RuntimeError("final narrative validation changed before commit")
            version = int(projected_state.get("meta", {}).get("state_version", 0))
            self.record_trace_event(
                request_id=request_id,
                phase_key="validation:final",
                alignment_key="validation",
                lane="main",
                event_type="validation",
                status=(
                    "completed"
                    if final_validation is None or final_validation.valid
                    else "failed"
                ),
                payload={
                    "input": {"response": text},
                    "output": (
                        {
                            "valid": final_validation.valid,
                            "violations": final_validation.violations,
                        }
                        if final_validation is not None
                        else {"valid": None, "reason": "not_applicable"}
                    ),
                    "metadata": {"repair": repaired},
                },
                party_turn=int(projected_state["meta"]["turn"]),
            )
            turn_metadata = self.turn_metadata(
                turn_kind="narrative",
                validator_valid=final_validation.valid if final_validation is not None else None,
                repaired=repaired,
                fallback_reason=provider_fallback_reason or gateway_fallback_reason,
                transport_status=transport_status,
                outcome=outcome.model_dump(mode="json"),
                llm_calls=llm_calls,
                interaction_evidence=[item.model_dump(mode="json") for item in interaction_evidence],
                story_memory_corrections=normalized_story_corrections,
            )
            if prompt_assembly is not None:
                turn_metadata["prompt_assembly"] = prompt_assembly
            if revision_eight and prompt_messages is not None:
                turn_metadata.update(
                    prompt_cache_observability(
                        prompt_cache_response or response,
                        prompt_messages,
                        history_units=self.settings.effective_rp_raw_history_window_turns,
                    )
                )
            if revision_seven:
                turn_metadata["story_memory_canonical"] = not fallback_noncanonical
                if scene_bundle_revision:
                    turn_metadata.update(
                        {
                            "scene_claims": scene_result.claims if scene_result is not None else None,
                            "applied_scene_delta": (
                                scene_result.applied_operations if scene_result is not None else []
                            ),
                            "dropped_scene_delta": (
                                scene_result.dropped_operations if scene_result is not None else []
                            ),
                            "scene_state_before": scene_before,
                            "scene_state_after": scene_after,
                            "scene_state_stale": bool(scene_after and scene_after.get("stale")),
                        }
                    )
                atomic_audit_events: list[tuple[str, dict[str, Any]]] = []
                if (
                    scene_bundle_revision
                    and scene_result is not None
                    and scene_result.dropped_operations
                ):
                    atomic_audit_events.append(
                        (
                            "scene_delta_operations_dropped",
                            {
                                "request_id": request_id,
                                "dropped_scene_delta": scene_result.dropped_operations,
                            },
                        )
                    )
                if fallback_noncanonical:
                    atomic_audit_events.append(
                        (
                            "llm_safe_fallback",
                            {
                                "request_id": request_id,
                                "check_id": outcome.check_id,
                                "model": self.settings.narrative_model,
                                "reason": provider_fallback_reason or gateway_fallback_reason,
                                "story_memory_canonical": False,
                            },
                        )
                    )
                atomic_audit_events.append(
                    (
                        "turn_complete",
                        {
                            "request_id": request_id,
                            "campaign_id": self.settings.campaign_id,
                            "duration_ms": duration_ms,
                            "llm_calls": llm_calls,
                            "model": self.settings.narrative_model,
                            "validator_valid": (
                                final_validation.valid
                                if final_validation is not None
                                else None
                            ),
                            "repair": repaired,
                            "fallback_reason": (
                                provider_fallback_reason or gateway_fallback_reason
                            ),
                            "provider_fallback_reason": provider_fallback_reason,
                            "gateway_fallback_reason": gateway_fallback_reason,
                            "check_id": outcome.check_id,
                            "result": outcome.result,
                        },
                    )
                )
                updated_state, turn_id = self.store.commit_turn(
                    patch,
                    reason=f"turn:{request_id}",
                    idempotency_key=idempotency_key,
                    request_id=request_id,
                    player_message=latest,
                    narrative_response=text,
                    response_json=response,
                    expected_state_version=expected_state_version,
                    prompt_messages=prompt_messages,
                    metadata=turn_metadata,
                    artifacts=artifact_result.persistence_records if artifact_result else [],
                    consumed_artifact_event_ids=[item.event_sequence for item in artifact_evidence],
                    workspace_files=workspace_result.persistence_records if workspace_result else [],
                    consumed_workspace_event_ids=[item.event_sequence for item in workspace_evidence],
                    party_turn=int(projected_state["meta"]["turn"]),
                    audit_events=atomic_audit_events,
                    excluded_from_memory=fallback_noncanonical,
                )
                version = int(updated_state.get("meta", {}).get("state_version", 0))
            else:
                updated_state = self.store.apply_state_patch(patch, reason=f"turn:{request_id}")
                version = int(updated_state.get("meta", {}).get("state_version", 0))
                turn_id = self.store.record_turn(
                    idempotency_key,
                    request_id,
                    latest,
                    text,
                    response,
                    version,
                    prompt_messages,
                    turn_metadata,
                    artifacts=artifact_result.persistence_records if artifact_result else [],
                    consumed_artifact_event_ids=[item.event_sequence for item in artifact_evidence],
                    workspace_files=workspace_result.persistence_records if workspace_result else [],
                    consumed_workspace_event_ids=[item.event_sequence for item in workspace_evidence],
                    party_turn=int(updated_state["meta"]["turn"]),
                )
            try:
                self.record_trace_event(
                    request_id=request_id,
                    phase_key="turn_commit",
                    alignment_key="turn_commit",
                    lane="main",
                    event_type="turn_commit",
                    status="completed",
                    payload={
                        "output": {
                            "turn_id": turn_id,
                            "state_version": version,
                            "party_turn": int(updated_state["meta"]["turn"]),
                        }
                    },
                    party_turn=int(updated_state["meta"]["turn"]),
                    turn_id=turn_id,
                )
            except Exception:  # noqa: BLE001 - revision-7 authority is already committed
                if not revision_seven:
                    raise
                logger.exception("turn_commit_trace_failed request_id=%s", request_id)
            if (
                self.relationship_mechanics is not None
                and self.settings.rp_contract_revision >= 4
                and not fallback_noncanonical
            ):
                try:
                    relationship_projection_before = self.trace_projection_snapshot()
                    self.relationship_mechanics.advance_turn(int(updated_state["meta"]["turn"]))
                    self.capture_projection_changes(
                        request_id,
                        relationship_projection_before,
                        source="relationship_turn_advance",
                        reason="post_commit_relationship_advance",
                        lane="main",
                    )
                except Exception:  # noqa: BLE001 - revision-7 authority is already committed
                    if not revision_seven:
                        raise
                    logger.exception(
                        "postcommit_relationship_advance_failed request_id=%s",
                        request_id,
                    )
            if not revision_seven:
                self.store.complete_turn_request(idempotency_key, response)
            if self.settings.scenario_type == "rp" and not rp_no_checks:
                self.store.record_check(turn_id, outcome)
            if not revision_seven:
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
            try:
                await self.after_turn_recorded(authorization, request_id)
            except Exception:  # noqa: BLE001 - revision-7 authority is already committed
                if not revision_seven:
                    raise
                logger.exception("postcommit_helpers_failed request_id=%s", request_id)
            return response
        except Exception as exc:
            self.store.fail_turn_request(idempotency_key, f"{type(exc).__name__}: {exc}")
            try:
                self.record_trace_event(
                    request_id=request_id,
                    phase_key="request_failed",
                    alignment_key="request_terminal",
                    lane="main",
                    event_type="request_failed",
                    status="failed",
                    payload={
                        "error": {
                            "type": type(exc).__name__,
                            "message": str(exc)[:1000],
                        }
                    },
                )
            except Exception:  # noqa: BLE001 - diagnostics must not mask the primary error
                logger.exception("turn_trace_terminal_capture_failed request_id=%s", request_id)
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
        story_memory_corrections: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "schema_version": "rp-gateway.turn.v1",
            "turn_kind": turn_kind,
            "scenario_type": self.settings.scenario_type,
            "rp_contract_version": self.settings.rp_contract_version,
            "rp_contract_revision": int(getattr(self.settings, "rp_contract_revision", 0) or 0),
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
        if story_memory_corrections:
            metadata["story_memory_corrections"] = [dict(item) for item in story_memory_corrections]
        return metadata

    def preview_applied_state(self, patch: Any) -> dict[str, Any]:
        candidate = self.store.preview_patch(patch, trusted_internal=True)
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

    def provider_fallback_response(
        self,
        outcome: Outcome,
        text: str,
        reason: str,
        request_id: str,
        *,
        audit: bool = True,
    ) -> dict[str, Any]:
        if audit:
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
        response = {
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
        if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 7:
            if reason in {"timeout", "rate_limited"}:
                public_reason = reason
            elif re.fullmatch(r"http_4\d\d", reason):
                public_reason = "http_client_error"
            elif re.fullmatch(r"http_5\d\d", reason):
                public_reason = "http_server_error"
            else:
                public_reason = "http_error"
            response["gateway_fallback"] = {"reason": public_reason}
        return response

    async def after_turn_recorded(self, authorization: str | None, request_id: str) -> None:
        jobs: list[tuple[str, int]] = []
        revision_eight = (
            self.settings.scenario_type == "rp"
            and self.settings.rp_contract_revision >= 8
        )
        if not revision_eight:
            jobs.append(("memory", self.settings.service_job_max_attempts))
        if self.settings.scenario_type == "rp":
            if self.rp_story_memory is not None and self.rp_story_memory.should_enqueue():
                jobs.append(
                    (
                        "rp_story_memory",
                        2 if revision_eight else self.settings.service_job_max_attempts,
                    )
                )
            if self.relationship_extraction is not None:
                jobs.append(("relationship_extraction", self.settings.service_job_max_attempts))
        for job_type, max_attempts in jobs:
            self.store.enqueue_service_job(job_type, request_id, max_attempts)
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
                self.store.retry_service_job(
                    int(running["id"]),
                    f"{type(exc).__name__}: {exc}",
                    delay,
                    terminal_status=(
                        "stale"
                        if running["job_type"] == "relationship_extraction"
                        else "failed"
                    ),
                )
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
        request_id = str(job.get("request_id") or "")
        projection_before = self.trace_projection_snapshot()
        if (
            self.settings.scenario_type == "rp"
            and self.settings.rp_contract_revision >= 8
        ):
            if job["job_type"] == "memory":
                # Retire legacy jobs that were queued before this party contract was activated.
                return
            if job["job_type"] == "rp_story_memory" and self.rp_story_memory is not None:
                result = await self.rp_story_memory.update(
                    authorization,
                    fail_open=True,
                    request_id=job.get("request_id"),
                )
                if result.get("retry_required") or result.get("error"):
                    raise RuntimeError(str(result.get("error") or "story-memory section failed"))
                return
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
            if request_id:
                self.capture_projection_changes(
                    request_id,
                    projection_before,
                    source=str(job["job_type"]),
                    reason=f"service_job:{job['id']}",
                )
            return
        raise RuntimeError(f"{job['job_type']} service job exceeded 64 batches")

    def authored_stable_affiliations(self) -> dict[str, str] | None:
        contract = self.scene_contract if isinstance(self.scene_contract, dict) else {}
        raw = contract.get("stable_affiliations")
        if not isinstance(raw, dict):
            return None
        return {
            str(character_id): affiliation
            for character_id, affiliation in raw.items()
            if isinstance(character_id, str)
            and isinstance(affiliation, str)
            and 0 < len(character_id) <= 128
            and 0 < len(affiliation) <= 128
        }

    @staticmethod
    def scene_legacy_operations(
        state: dict[str, Any],
        materialization: SceneMaterialization | None,
        turn: int,
    ) -> list[PatchOperation]:
        if materialization is None:
            return []
        operations: list[PatchOperation] = []
        characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
        for scene_operation in materialization.applied_operations:
            operation_type = scene_operation["type"]
            if operation_type == "move_player":
                path = "/player/location"
                op = "replace" if "location" in state.get("player", {}) else "add"
            else:
                character_id = str(scene_operation["character_id"])
                escaped = character_id.replace("~", "~0").replace("/", "~1")
                path = f"/characters/{escaped}/location"
                character = characters.get(character_id)
                op = "replace" if isinstance(character, dict) and "location" in character else "add"
            operations.append(
                PatchOperation(
                    op=op,
                    path=path,
                    value=scene_operation["location_id"],
                    reason=f"Mirrors applied {operation_type} into the canonical legacy location field.",
                    turn=turn,
                )
            )
        return operations

    def relationship_pressure(
        self,
        state: dict[str, Any],
        *,
        latest_player_message: str = "",
        outcome_target: str | None = None,
    ) -> str | None:
        if self.relationship_mechanics is None:
            return None
        party_turn = int(state.get("meta", {}).get("turn", 0))
        characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
        declared_aliases = normalized_aliases(self.relationship_model or {})
        if self.settings.rp_contract_revision >= 8:
            names = {
                str(character_id): str(value["name"]).strip()
                for character_id, value in characters.items()
                if isinstance(value, dict)
                and isinstance(value.get("name"), str)
                and str(value["name"]).strip()
            }
        else:
            names = {
                str(character_id): self.relationship_character_name(
                    str(character_id),
                    value,
                    declared_aliases.get(str(character_id))
                    if self.settings.rp_contract_revision >= 7
                    else None,
                )
                for character_id, value in characters.items()
            }
        scene_character_ids = None
        if self.settings.rp_contract_revision >= 7:
            scan_text = latest_player_message
            if self.settings.rp_contract_revision >= 8:
                scan_text = recent_rp_scan_text(
                    self.store.turns_for_memory(include_noncanonical_fallback=False),
                    latest_player_message,
                )
            scene_character_ids = relationship_scene_character_ids(
                state,
                scan_text,
                outcome_target=outcome_target,
                character_aliases=declared_aliases,
                use_scene_state=self.settings.rp_contract_revision == 7,
                use_seed_signals=self.settings.rp_contract_revision == 7,
            )
        if self.settings.rp_contract_revision >= 4:
            pressure = (
                self.relationship_mechanics.pressure_block(
                    party_turn,
                    names,
                    persist_seed_state=False,
                    character_ids=scene_character_ids,
                )
                if self.settings.rp_contract_revision >= 7
                else self.relationship_mechanics.pressure_block(party_turn, names)
            )
            resolution = (
                self.relationship_mechanics.due_event_block(
                    party_turn,
                    names,
                    character_ids=scene_character_ids,
                )
                if self.settings.rp_contract_revision >= 7
                else self.relationship_mechanics.due_event_block(party_turn, names)
            )
        else:
            changes = self.relationship_mechanics.advance_turn(party_turn)
            pressure = self.relationship_mechanics.pressure_block(party_turn, names)
            resolution = self.relationship_mechanics.resolved_event_block(changes, names)
        if self.settings.rp_contract_revision >= 8:
            pressure_lines = pressure.splitlines() if pressure else []
            if pressure_lines and pressure_lines[0] == "RELATIONSHIP_PRESSURE":
                pressure_lines = pressure_lines[1:]
            mandatory_lines = ["RELATIONSHIP_PRESSURE"]
            if resolution:
                mandatory_lines.extend(["", *resolution.splitlines()])
            mandatory = "\n".join(mandatory_lines).rstrip()
            if len(mandatory) > 1_500:
                raise PromptBudgetExceeded(
                    estimated_tokens=estimate_tokens(mandatory),
                    token_budget=estimate_tokens("x" * 1_500),
                )
            ordered_lines = list(mandatory_lines)
            if pressure_lines:
                ordered_lines.extend(["", *pressure_lines])
            bounded_lines = list(mandatory_lines)
            for line in ordered_lines[len(mandatory_lines) :]:
                candidate = "\n".join([*bounded_lines, line])
                if len(candidate) > 1_500:
                    break
                bounded_lines.append(line)
            bounded = "\n".join(bounded_lines).rstrip()
            return bounded if bounded != "RELATIONSHIP_PRESSURE" else None
        return "\n\n".join(block for block in (pressure, resolution) if block) or None

    def refresh_revision_eight_lore_cards(
        self,
        request: ChatCompletionRequest,
        *,
        latest_player_message: str,
        outcome_target: str | None,
    ) -> None:
        scan_text = recent_rp_scan_text(
            self.store.turns_for_memory(include_noncanonical_fallback=False),
            latest_player_message,
        )
        if str(outcome_target or "").strip():
            scan_text = f"{scan_text}\n{str(outcome_target).strip()}"
        cards = self.store.lore_cards_for_prompt(
            scan_text,
            limit=self.settings.party_lore_card_prompt_limit,
            max_chars=min(self.settings.party_lore_card_prompt_max_chars, 4_000),
            title_keywords_only=True,
            whole_match=True,
        )
        lore_block = party_lore_cards_block(cards, max_chars=4_000)
        messages = [
            message
            for message in request.messages
            if not (
                message.role == "system"
                and isinstance(message.content, str)
                and message.content.startswith("PARTY_LORE_CARDS")
            )
        ]
        if lore_block:
            messages.insert(0, ChatMessage(role="system", content=lore_block))
        request.messages = messages

    @staticmethod
    def prompt_assembly_trace(messages: list[dict[str, str]], latest: str) -> list[dict[str, Any]]:
        """Describe the exact assembled messages without becoming a prompt authority."""

        prefixes = {
            "LONG_TERM_PARTY_MEMORY": "long_term_memory",
            "RP_STORY_MEMORY": "rp_story_memory",
            "WORLD_SYSTEM_PROMPT": "world_system_prompt",
            "WORLD_AUTHORS_NOTE": "world_authors_note",
            "RELEVANT_CHARACTERS": "relevant_characters",
            "RETRIEVED_ARCHIVE_SCENES": "retrieved_archive_scenes",
            "UNCOMPACTED_ARCHIVE_FALLBACK": "uncompacted_archive_fallback",
            "PARTY_LORE_CARDS": "party_lore_cards",
            "ИСПРАВЛЕНИЯ ИГРОКА": "player_corrections",
            "RELATIONSHIP_PRESSURE": "relationship_pressure",
            "ACTIVE_TRAINING_TURN_CONTRACT": "training_turn_contract",
            "TRAINING_INTERACTION_CONTRACT": "training_interaction_contract",
            "WORLD_ABSOLUTE_RULES": "world_absolute_rules",
        }
        result: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            source = "conversation_history"
            reason = "Selected by the active history/context budget."
            if role == "system":
                source = "gateway_system"
                reason = "Added by Gateway for the active runtime contract."
                for prefix, label in prefixes.items():
                    if content.startswith(prefix) or (prefix == "WORLD_ABSOLUTE_RULES" and prefix in content):
                        source = label
                        break
                else:
                    if content.startswith("Relevant state summary:"):
                        source = "state_summary"
                    elif "AUTHORITATIVE_OUTCOME" in content:
                        source = "authoritative_outcome"
                    else:
                        source = "scenario_rules"
            elif role == "user" and content == latest and index == len(messages) - 1:
                source = "player_input"
                reason = "Latest player message."
            result.append(
                {
                    "index": index,
                    "role": role,
                    "source": source,
                    "reason": reason,
                    "content": content,
                }
            )
        return result

    @staticmethod
    def relationship_character_name(
        character_id: str,
        value: Any,
        declared_aliases: list[str] | None = None,
    ) -> str:
        if isinstance(value, dict):
            explicit = value.get("name") or value.get("display_name")
            if isinstance(explicit, str) and explicit.strip():
                return explicit.strip()
        for alias in declared_aliases or []:
            if isinstance(alias, str) and alias.strip():
                return alias.strip()
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

    def rebuild_revision_seven_request(
        self,
        request: ChatCompletionRequest,
        story_memory: dict[str, Any] | None,
        current_action: str,
    ) -> tuple[int | None, int, list[int]]:
        """Align the protected raw tail with one effective story-memory snapshot."""

        revision_eight = self.settings.rp_contract_revision >= 8
        covered_through = (
            story_memory_safe_coverage(story_memory)
            if revision_eight
            else int(story_memory.get("to_turn_id") or 0)
            if story_memory
            else 0
        )
        messages = [
            message
            for message in request.messages
            if message.role == "system"
            and isinstance(message.content, str)
            and message.content.startswith(("PARTY_LORE_CARDS", "ИСПРАВЛЕНИЯ ИГРОКА"))
        ]
        if not revision_eight:
            messages.insert(
                0,
                ChatMessage(role="system", content=scene_state_boundary_block(self.store.get_state())),
            )
        all_turns = self.store.turns_for_memory(include_noncanonical_fallback=True)
        if not revision_eight:
            all_turns = unresolved_noncanonical_fallback_turns(
                self.store.get_state(),
                all_turns,
            )
        if revision_eight:
            all_turns = eligible_rp_turns(all_turns)
            raw_tail_turns = raw_history_window(
                all_turns,
                safe_coverage=covered_through,
                window_turns=self.settings.effective_rp_raw_history_window_turns,
            )
        else:
            raw_tail_turns = [turn for turn in all_turns if int(turn["id"]) > covered_through]
            unresolved_fallbacks = [
                turn for turn in all_turns if turn.get("noncanonical_safe_fallback")
            ]
            raw_tail_turns = list(
                {
                    int(turn["id"]): turn
                    for turn in [*unresolved_fallbacks, *raw_tail_turns]
                }.values()
            )
            raw_tail_turns.sort(key=lambda turn: int(turn["id"]))
        for turn in raw_tail_turns:
            rendered = (
                rp_turn_messages(turn)
                if revision_eight
                else [
                    ("user", str(turn.get("player_message") or "")),
                    ("assistant", str(turn.get("narrative_response") or "")),
                ]
            )
            messages.extend(ChatMessage(role=role, content=content) for role, content in rendered)
        if self.settings.party_memory_retrieval_enabled and not revision_eight:
            retrieved = self.store.search_archived_turns(
                current_action,
                through_turn_id=covered_through,
                limit=self.settings.party_memory_retrieval_limit,
            )
            retrieval_block = archived_memory_retrieval_block(
                retrieved,
                self.settings.party_memory_retrieval_max_chars,
            )
            if retrieval_block:
                messages.append(ChatMessage(role="system", content=retrieval_block))
        messages.append(ChatMessage(role="user", content=current_action))
        request.messages = messages
        snapshot_id = (
            int(story_memory["id"])
            if story_memory is not None and story_memory.get("id") is not None
            else None
        )
        request._latest_player_action = current_action
        request._rp_story_memory_snapshot_id = snapshot_id
        request._rp_story_memory_covered_through_turn_id = covered_through
        if revision_eight:
            request._rp_raw_history_turn_ids = [int(turn["id"]) for turn in raw_tail_turns]
            request._rp_raw_history_removable_units = removable_covered_history_units(
                raw_tail_turns,
                safe_coverage=covered_through,
            )
        return snapshot_id, covered_through, [int(turn["id"]) for turn in raw_tail_turns]

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
