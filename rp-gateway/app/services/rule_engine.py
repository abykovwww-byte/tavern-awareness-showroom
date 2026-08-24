"""Deterministic check resolution for RP Gateway."""

from __future__ import annotations

import hashlib
import json
import random
import re
from typing import TYPE_CHECKING, Any

from app.models.schemas import (
    InteractionEvidence,
    Intent,
    Outcome,
    PatchOperation,
    SceneAllowance,
    StatePatch,
)
from app.services.scene_state import build_scene_transition_allowance

if TYPE_CHECKING:
    from app.services.training_runtime import TrainingRuntimeService


TARGETED_CHECKS = {"persuasion", "intimidation", "deception", "trust", "conflict"}
SOCIAL_CHECKS = {"persuasion", "intimidation", "deception", "trust"}
def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def normalize_tokens(value: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", value.lower()) if len(token) >= 4}


class RuleEngine:
    def resolve(
        self,
        state: dict[str, Any],
        intent: Intent,
        request_id: str,
        roll: int | None = None,
        campaign_id: str | None = None,
        scenario_type: str = "rp",
        rp_contract_version: str = "rp-core.v1",
        rp_contract_revision: int = 0,
        interaction_evidence: list[InteractionEvidence] | None = None,
        training_runtime: "TrainingRuntimeService | None" = None,
        character_aliases: dict[str, list[str]] | None = None,
        authored_stable_affiliations: dict[str, str] | None = None,
    ) -> tuple[Outcome, StatePatch]:
        if scenario_type == "rp" and rp_contract_revision >= 1:
            return self.resolve_nonmechanical(
                state,
                intent,
                request_id,
                campaign_id,
                scenario_type,
                rp_contract_revision=rp_contract_revision,
                character_aliases=character_aliases,
                authored_stable_affiliations=authored_stable_affiliations,
            )
        if scenario_type == "training":
            return self.resolve_nonmechanical(
                state,
                intent,
                request_id,
                campaign_id,
                scenario_type,
                interaction_evidence=interaction_evidence or [],
                training_runtime=training_runtime,
            )
        if intent.action_type in TARGETED_CHECKS and not intent.target:
            intent.ambiguities.append(f"{intent.action_type} has no target; outcome is constrained.")
        check_id = self.check_id(intent, request_id)
        roll_value = roll if roll is not None else random.SystemRandom().randint(1, 20)
        relationship_key, relationship = self.relationship(state, intent.actor, intent.target)
        relation_mod = self.relation_modifier(intent.action_type, state.get("characters", {}).get(intent.target or ""), relationship)
        blocked = self.blockers(state, intent)
        final_score = intent.skill + intent.preparation + intent.leverage + relation_mod + roll_value - intent.difficulty
        result = self.outcome_from_score(final_score, roll_value, bool(blocked))
        consequences = self.consequences(intent, result, blocked)
        forbidden = self.forbidden(intent, blocked)
        outcome = Outcome(
            check_id=check_id,
            action_type=intent.action_type,
            actor=intent.actor,
            target=intent.target,
            result=result,
            roll=roll_value,
            difficulty=intent.difficulty,
            modifiers={
                "skill": intent.skill,
                "preparation": intent.preparation,
                "leverage": intent.leverage,
                "relation": relation_mod,
            },
            final_score=final_score,
            blocked_reasons=blocked,
            consequences=consequences,
            forbidden_reinterpretations=forbidden,
            authoritative_block=self.authoritative_block(check_id, intent, result, roll_value, final_score, consequences, forbidden),
        )
        patch = self.patch_for_outcome(state, intent, outcome, relationship_key, relationship, campaign_id)
        return outcome, patch

    def resolve_nonmechanical(
        self,
        state: dict[str, Any],
        intent: Intent,
        request_id: str,
        campaign_id: str | None,
        scenario_type: str,
        interaction_evidence: list[InteractionEvidence] | None = None,
        training_runtime: "TrainingRuntimeService | None" = None,
        rp_contract_revision: int = 0,
        character_aliases: dict[str, list[str]] | None = None,
        authored_stable_affiliations: dict[str, str] | None = None,
    ) -> tuple[Outcome, StatePatch]:
        check_id = self.check_id(intent, request_id)
        training = scenario_type == "training"
        scene_allowance: SceneAllowance | None = None
        result = "deterministic_resolution" if training else "narrative_continuation"
        if training:
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
            authoritative = (
                "<AUTHORITATIVE_OUTCOME>\n"
                "Mode: deterministic training\n"
                "No die was rolled and no skill check exists. Resolve only the player's explicit actions, "
                "apply their observable consequences, and advance exactly one authored scenario turn.\n"
                "</AUTHORITATIVE_OUTCOME>"
            )
        else:
            consequences = [
                "Continue the roleplaying scene from the player's stated intent.",
                "Apply active WorldPack rules, current state, character goals, relationships, and prior consequences.",
                "Leave consequential choices and the player character's inner decisions to the player.",
            ]
            if rp_contract_revision == 7:
                scene_allowance = SceneAllowance.model_validate(
                    build_scene_transition_allowance(
                        state,
                        intent.desired_outcome,
                        character_aliases=character_aliases,
                        authored_stable_affiliations=authored_stable_affiliations,
                    )
                )
            allowance_block = (
                "<SCENE_TRANSITION_ALLOWANCE>\n"
                + json.dumps(
                    scene_allowance.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n</SCENE_TRANSITION_ALLOWANCE>\n"
                if scene_allowance is not None
                else ""
            )
            authoritative = (
                "<AUTHORITATIVE_OUTCOME>\n"
                "Mode: roleplaying without mechanical checks\n"
                "No die was rolled and no feasibility, difficulty, score, success, or failure was assigned. "
                "Treat the player text as intent and continue from active world facts and character causes.\n"
                f"{allowance_block}"
                "</AUTHORITATIVE_OUTCOME>"
            )
        outcome = Outcome(
            check_id=check_id,
            action_type=intent.action_type,
            actor=intent.actor,
            target=intent.target,
            result=result,
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
            authoritative_block=authoritative,
            scene_allowance=scene_allowance,
        )
        return outcome, self.patch_for_nonmechanical(
            state,
            intent,
            outcome,
            campaign_id,
            scenario_type,
            interaction_evidence=interaction_evidence or [],
            training_runtime=training_runtime,
        )

    def patch_for_nonmechanical(
        self,
        state: dict[str, Any],
        intent: Intent,
        outcome: Outcome,
        campaign_id: str | None,
        scenario_type: str,
        interaction_evidence: list[InteractionEvidence] | None = None,
        training_runtime: "TrainingRuntimeService | None" = None,
    ) -> StatePatch:
        turn = int(state.get("meta", {}).get("turn", 0)) + 1
        participants = [intent.actor] + ([intent.target] if intent.target else [])
        operations: list[PatchOperation] = [
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={
                    "turn": turn,
                    "event": f"{scenario_type} turn {turn} accepted from explicit player input.",
                    "confirmed": True,
                    "participants": participants,
                },
                reason=f"Records the authoritative {scenario_type} turn boundary.",
                turn=turn,
            )
        ]
        if scenario_type == "training":
            if training_runtime and training_runtime.enabled:
                operations.extend(
                    training_runtime.resolution_operations(
                        state,
                        intent.desired_outcome,
                        turn,
                        interaction_evidence or [],
                    )
                )
        return StatePatch(
            turn=turn,
            check_id=outcome.check_id,
            source=f"{scenario_type}-gateway",
            patch=operations,
        )

    def check_id(self, intent: Intent, request_id: str) -> str:
        raw = f"{request_id}:{intent.action_type}:{intent.actor}:{intent.target}:{intent.desired_outcome}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def relationship(self, state: dict[str, Any], actor: str, target: str | None) -> tuple[str | None, dict[str, Any] | None]:
        if not target:
            return None, None
        keys = [f"{actor}_{target}", f"{target}_{actor}", f"player_{target}"]
        relationships = state.get("relationships", {})
        for key in keys:
            relation = relationships.get(key)
            if isinstance(relation, dict):
                return key, relation
        return f"{actor}_{target}", None

    def relation_modifier(self, action_type: str, character: dict[str, Any] | None, relationship: dict[str, Any] | None) -> int:
        trust = 0
        suspicion = 0
        fear = 0
        if character:
            trust += int(character.get("trust", 0))
            fear += int(character.get("fear", 0))
        if relationship:
            trust += int(relationship.get("trust", 0))
            suspicion += int(relationship.get("suspicion", 0))
        if action_type == "persuasion":
            return clamp(round(trust / 3) - round(suspicion / 3), -4, 4)
        if action_type == "intimidation":
            return clamp(round(fear / 3) - round(trust / 5), -4, 4)
        if action_type == "deception":
            return clamp(round(trust / 4) - round(suspicion / 2), -5, 3)
        if action_type == "trust":
            return clamp(round(trust / 4) - round(suspicion / 3), -4, 4)
        if action_type == "conflict":
            return clamp(round(fear / 4), 0, 3)
        return 0

    def blockers(self, state: dict[str, Any], intent: Intent) -> list[str]:
        blocked: list[str] = []
        target = state.get("characters", {}).get(intent.target or "")
        if isinstance(target, dict) and target.get("status") in {"dead", "missing", "incapacitated"}:
            blocked.append(f"target {intent.target} status is {target.get('status')}")
        if intent.resources_claimed:
            player_resources = state.get("player", {}).get("resources", {})
            resources = state.get("resources", {})
            for resource_id in intent.resources_claimed:
                record = resources.get(resource_id, {})
                if isinstance(record, dict) and str(record.get("state", "")).lower() in {"unavailable", "spent", "destroyed"}:
                    blocked.append(f"resource {resource_id} is {record.get('state')}")
                quantity = player_resources.get(resource_id)
                if not isinstance(quantity, (int, float)) or quantity < intent.resource_amount:
                    blocked.append(f"player cannot spend {intent.resource_amount:g} {resource_id}")
        desired_tokens = normalize_tokens(intent.desired_outcome)
        constraints: list[str] = []
        if isinstance(target, dict):
            constraints.extend(str(item) for item in target.get("hard_constraints", []))
        constraints.extend(str(item.get("text", "")) for item in state.get("world_constraints", []) if isinstance(item, dict))
        for constraint in constraints:
            lowered = constraint.lower()
            if any(marker in lowered for marker in ["cannot", "must not", "never", "unavailable", "blocked"]):
                if desired_tokens.intersection(normalize_tokens(constraint)):
                    blocked.append(constraint)
        return blocked

    def outcome_from_score(self, score: int, roll: int, blocked: bool) -> str:
        if blocked:
            return "failure"
        if roll == 1 and score <= 0:
            return "critical_failure"
        if roll == 20 and score >= 0:
            return "critical_success"
        if score <= -10:
            return "critical_failure"
        if score <= -4:
            return "failure"
        if score <= 0:
            return "failure_with_progress"
        if score <= 5:
            return "partial_success"
        if score <= 14:
            return "success"
        return "critical_success"

    def consequences(self, intent: Intent, result: str, blocked: list[str]) -> list[str]:
        if blocked:
            return [f"check is blocked by hard constraint: {reason}" for reason in blocked]
        target = intent.target or "scene"
        if result == "failure_with_progress":
            return [f"{target} does not grant the desired outcome", "one narrow lead or limited opening remains"]
        if result in {"critical_failure", "failure"}:
            return [f"{target} does not grant the desired outcome", "the attempt creates cost or caution"]
        if result == "partial_success":
            return [f"{target} grants a limited, conditional, or delayed benefit"]
        return [f"{target} grants the bounded desired outcome", "hard world constraints still apply"]

    def forbidden(self, intent: Intent, blocked: list[str]) -> list[str]:
        items = [
            "do not change the Result field",
            "do not add an equivalent hidden success",
            "do not bypass hard world constraints",
        ]
        if intent.desired_outcome:
            items.append(f"do not silently grant '{intent.desired_outcome}' beyond the listed consequences")
        items.extend(f"do not reinterpret blocked constraint as satisfied: {reason}" for reason in blocked)
        return items

    def authoritative_block(
        self,
        check_id: str,
        intent: Intent,
        result: str,
        roll: int,
        final_score: int,
        consequences: list[str],
        forbidden: list[str],
    ) -> str:
        lines = [
            "<AUTHORITATIVE_OUTCOME>",
            f"Check ID: {check_id}",
            f"Action: {intent.action_type}",
            f"Target: {intent.target or 'scene'}",
            f"Result: {result}",
            f"Roll: {roll}",
            f"Final score: {final_score}",
            "Consequences:",
        ]
        lines.extend(f"- {item}" for item in consequences)
        lines.append("Forbidden reinterpretations:")
        lines.extend(f"- {item}" for item in forbidden)
        lines.append("</AUTHORITATIVE_OUTCOME>")
        return "\n".join(lines)

    def patch_for_outcome(
        self,
        state: dict[str, Any],
        intent: Intent,
        outcome: Outcome,
        relationship_key: str | None,
        relationship: dict[str, Any] | None,
        campaign_id: str | None = None,
    ) -> StatePatch:
        turn = int(state.get("meta", {}).get("turn", 0)) + 1
        participants = [intent.actor] + ([intent.target] if intent.target else [])
        operations: list[PatchOperation] = [
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={
                    "turn": turn,
                    "event": f"Gateway check {outcome.check_id} ({intent.action_type}) resolved as {outcome.result}.",
                    "confirmed": True,
                    "participants": participants,
                },
                reason="Records fixed gateway check outcome before narration.",
                turn=turn,
            )
        ]
        if intent.resources_claimed and not outcome.blocked_reasons:
            resource_id = intent.resources_claimed[0]
            current = state.get("player", {}).get("resources", {}).get(resource_id)
            if isinstance(current, (int, float)):
                new_value = current - intent.resource_amount
                operations.append(
                    PatchOperation(
                        op="replace",
                        path=f"/player/resources/{pointer_escape(resource_id)}",
                        value=int(new_value) if float(new_value).is_integer() else new_value,
                        reason=f"Consumes {intent.resource_amount:g} {resource_id} for gateway check {outcome.check_id}.",
                        turn=turn,
                    )
                )
        trust_delta, suspicion_delta = self.relationship_delta(intent.action_type, outcome.result)
        if relationship_key and intent.target and (trust_delta or suspicion_delta):
            if relationship:
                trust = clamp(int(relationship.get("trust", 0)) + trust_delta, -10, 10)
                suspicion = clamp(int(relationship.get("suspicion", 0)) + suspicion_delta, 0, 10)
                operations.append(
                    PatchOperation(
                        op="replace",
                        path=f"/relationships/{pointer_escape(relationship_key)}/trust",
                        value=trust,
                        reason=f"Bounded trust delta from gateway check {outcome.check_id}.",
                        turn=turn,
                    )
                )
                operations.append(
                    PatchOperation(
                        op="replace",
                        path=f"/relationships/{pointer_escape(relationship_key)}/suspicion",
                        value=suspicion,
                        reason=f"Bounded suspicion delta from gateway check {outcome.check_id}.",
                        turn=turn,
                    )
                )
            else:
                operations.append(
                    PatchOperation(
                        op="add",
                        path=f"/relationships/{pointer_escape(relationship_key)}",
                        value={
                            "from": intent.actor,
                            "to": intent.target,
                            "trust": clamp(trust_delta, -10, 10),
                            "suspicion": clamp(suspicion_delta, 0, 10),
                            "notes": [f"Created by gateway check {outcome.check_id}."],
                        },
                        reason=f"Creates bounded relationship record from gateway check {outcome.check_id}.",
                        turn=turn,
                    )
                )
        return StatePatch(turn=turn, check_id=outcome.check_id, patch=operations, contradictions=outcome.blocked_reasons)

    def relationship_delta(self, action_type: str, result: str) -> tuple[int, int]:
        if action_type not in SOCIAL_CHECKS:
            return 0, 0
        if result == "critical_failure":
            return -2, 2
        if result == "failure":
            return -1, 1
        if result == "failure_with_progress":
            return 0, 1
        if result == "partial_success":
            return 1, 0
        if result == "success":
            return 1, -1
        if result == "critical_success":
            return 2, -1
        return 0, 0
