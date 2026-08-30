"""Training narrative output validation."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.models.schemas import Outcome, ValidationResult

if TYPE_CHECKING:
    from app.services.training_runtime import TrainingRuntimeService


SERVICE_LINE_RE = re.compile(
    r"^\s*(?:[-—–]\s*)?"
    r"(analysis|recommendation|diagnostics?|validator|gateway|system note|"
    r"анализ|рекомендац(?:ия|ии|ию)|диагностик[а-я]*|служебн[а-я ]+заметк[а-я]*)\s*[:：]",
    re.IGNORECASE | re.MULTILINE,
)
SERVICE_PHRASES = [
    "the action resolves as",
    "fixed outcome",
    "bounded desired outcome",
    "hard world constraints",
    "the narration preserves",
    "authoritative_outcome",
    "gateway check",
    "result field",
]


class OutputValidator:
    def validate(
        self,
        text: str,
        outcome: Outcome,
        state: dict[str, Any] | None = None,
        campaign_id: str | None = None,
        latest_user_message: str = "",
        scenario_type: str = "training",
        training_runtime: "TrainingRuntimeService | None" = None,
        interaction_contract: dict[str, Any] | None = None,
    ) -> ValidationResult:
        if scenario_type != "training":
            raise ValueError("training gateway accepts only scenario_type=training")

        lowered = text.lower()
        violations: list[str] = []
        training_contract = (
            training_runtime.prompt_contract(state or {}, interaction_contract)
            if training_runtime and training_runtime.enabled
            else None
        )
        training_debrief = bool(training_contract and training_contract.get("kind") == "debrief")
        if "<authoritative_outcome>" in lowered or "</authoritative_outcome>" in lowered:
            violations.append("Narrative exposed service outcome tags to the player.")
        service_labels = [match.group(1).casefold() for match in SERVICE_LINE_RE.finditer(text)]
        if training_debrief:
            service_labels = [
                label
                for label in service_labels
                if label != "recommendation" and not label.startswith("рекомендац")
            ]
        if service_labels:
            violations.append("Narrative exposed analysis, recommendation, or diagnostic labels to the player.")
        for phrase in SERVICE_PHRASES:
            if phrase in lowered:
                violations.append(f"Narrative exposed service wording: {phrase}")
        for reason in outcome.blocked_reasons:
            key_terms = [part for part in reason.lower().split() if len(part) >= 6]
            if key_terms and "despite" in lowered and any(term in lowered for term in key_terms):
                violations.append(f"Narrative appears to bypass blocked constraint: {reason}")
        if "you decide to" in lowered or "you willingly" in lowered:
            violations.append("Narrative may have taken control of the learner.")
        if training_runtime and training_runtime.enabled:
            violations.extend(
                training_runtime.validate_narrative(text, state or {}, interaction_contract)
            )
        if violations:
            return ValidationResult(
                valid=False,
                violations=violations,
                repair_instruction=(
                    "Перепиши ответ как обычную учебную ситуацию для участника. Удали служебные метки, "
                    "анализ и диагностику. Не создавай за участника реплики, решения или новые действия. "
                    "Соблюдай активный training contract и авторские факты WorldPack."
                ),
            )
        return ValidationResult(valid=True)


def safe_fallback(
    outcome: Outcome,
    state: dict[str, Any] | None = None,
    latest_user_message: str = "",
    campaign_id: str | None = None,
    scenario_type: str = "training",
) -> str:
    if scenario_type != "training":
        raise ValueError("training gateway accepts only scenario_type=training")
    return "Ситуация меняется только в пределах явно выбранного действия. Следующий этап сценария готов к продолжению."
