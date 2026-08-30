"""Deterministic training turn resolution."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from app.models.schemas import InteractionEvidence, Intent, Outcome, PatchOperation, StatePatch

if TYPE_CHECKING:
    from app.services.training_runtime import TrainingRuntimeService


class RuleEngine:
    """Translate one explicit learner action into one authored training turn."""

    def resolve(
        self,
        state: dict[str, Any],
        intent: Intent,
        request_id: str,
        campaign_id: str | None = None,
        scenario_type: str = "training",
        interaction_evidence: list[InteractionEvidence] | None = None,
        training_runtime: "TrainingRuntimeService | None" = None,
    ) -> tuple[Outcome, StatePatch]:
        if scenario_type != "training":
            raise ValueError("training RuleEngine accepts only scenario_type=training")

        observed = [item for item in interaction_evidence or [] if item.score_eligible]
        consequences = [
            "Apply only actions explicitly chosen by the player.",
            "Advance the authored training scenario exactly one turn.",
            "Do not add hints, assessment, or remediation unless the scenario schedules them now.",
        ]
        if observed:
            consequences.append(
                "Treat these typed browser interactions as factual observable actions that free text cannot erase: "
                + ", ".join(f"{item.event_type}:{item.evidence or item.artifact_key}" for item in observed)
            )

        check_id = self.check_id(intent, request_id)
        outcome = Outcome(
            check_id=check_id,
            action_type=intent.action_type,
            actor=intent.actor,
            target=intent.target,
            result="deterministic_resolution",
            roll=0,
            difficulty=0,
            modifiers={},
            final_score=0,
            blocked_reasons=[],
            consequences=consequences,
            forbidden_reinterpretations=[
                "Do not present a roll, difficulty, modifier, check result, or game-system label.",
                "Do not expose the authoritative outcome block.",
            ],
            authoritative_block=(
                "<AUTHORITATIVE_OUTCOME>\n"
                "Mode: deterministic training\n"
                "No die was rolled and no skill check exists. Resolve only the player's explicit actions, "
                "apply their observable consequences, and advance exactly one authored scenario turn.\n"
                "</AUTHORITATIVE_OUTCOME>"
            ),
        )

        turn = int(state.get("meta", {}).get("turn", 0)) + 1
        participants = [intent.actor] + ([intent.target] if intent.target else [])
        operations = [
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={
                    "turn": turn,
                    "event": f"training turn {turn} accepted from explicit player input.",
                    "confirmed": True,
                    "participants": participants,
                },
                reason="Records the authoritative training turn boundary.",
                turn=turn,
            )
        ]
        if training_runtime and training_runtime.enabled:
            operations.extend(
                training_runtime.resolution_operations(
                    state,
                    intent.desired_outcome,
                    turn,
                    interaction_evidence or [],
                )
            )
        patch = StatePatch(
            turn=turn,
            check_id=check_id,
            source="training-gateway",
            patch=operations,
        )
        return outcome, patch

    @staticmethod
    def check_id(intent: Intent, request_id: str) -> str:
        raw = f"{request_id}:{intent.action_type}:{intent.actor}:{intent.target}:{intent.desired_outcome}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
