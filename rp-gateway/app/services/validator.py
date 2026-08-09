"""Narrative output validation."""

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
RESULT_NARRATION = {
    "critical_success": "Сцена открывает ясный проход вперед.",
    "success": "Сцена продолжает двигаться в выбранном направлении.",
    "partial_success": "Сцена сдвигается вперед, но часть результата остается неустойчивой.",
    "failure_with_progress": "Желаемое сразу не складывается, но остается узкая зацепка.",
    "failure": "Попытка не дает прямого результата; напряжение удерживается в сцене.",
    "critical_failure": "Попытка резко ухудшает положение, и последствия становятся заметны сразу.",
}


class OutputValidator:
    def validate(
        self,
        text: str,
        outcome: Outcome,
        state: dict[str, Any] | None = None,
        campaign_id: str | None = None,
        latest_user_message: str = "",
        scenario_type: str = "rp",
        training_runtime: "TrainingRuntimeService | None" = None,
        interaction_contract: dict[str, Any] | None = None,
    ) -> ValidationResult:
        lowered = text.lower()
        violations: list[str] = []
        if "<authoritative_outcome>" in lowered or "</authoritative_outcome>" in lowered:
            violations.append("Narrative exposed service outcome tags to the player.")
        if SERVICE_LINE_RE.search(text):
            violations.append("Narrative exposed analysis, recommendation, or diagnostic labels to the player.")
        for phrase in SERVICE_PHRASES:
            if phrase in lowered:
                violations.append(f"Narrative exposed service wording: {phrase}")
        if scenario_type == "rp" and outcome.result in {"failure", "critical_failure", "failure_with_progress"}:
            risky = [
                "secretly grants",
                "equivalent authority",
                "military authority",
                "takes command",
                "transfers command",
                "hands over the throne",
            ]
            if any(item in lowered for item in risky):
                violations.append("Narrative grants an equivalent hidden success despite failed or limited result.")
        for reason in outcome.blocked_reasons:
            key_terms = [part for part in reason.lower().split() if len(part) >= 6]
            if key_terms and "despite" in lowered and any(term in lowered for term in key_terms):
                violations.append(f"Narrative appears to bypass blocked constraint: {reason}")
        if "you decide to" in lowered or "you willingly" in lowered:
            violations.append("Narrative may have taken control of the player character.")
        if scenario_type == "training" and training_runtime and training_runtime.enabled:
            violations.extend(
                training_runtime.validate_narrative(text, state or {}, interaction_contract)
            )
        if violations:
            return ValidationResult(
                valid=False,
                violations=violations,
                repair_instruction=(
                    "Перепиши ответ как обычную сцену для игрока. Удали служебные метки, анализ и диагностику. "
                    "Не принимай решений за персонажа игрока и не меняй зафиксированный исход действия."
                ),
            )
        return ValidationResult(valid=True)


def safe_fallback(
    outcome: Outcome,
    state: dict[str, Any] | None = None,
    latest_user_message: str = "",
    campaign_id: str | None = None,
    scenario_type: str = "rp",
) -> str:
    if scenario_type == "novel":
        return (
            "Сцена сохраняет набранный ритм: собеседник реагирует на сказанное, а напряжение между героями "
            "остается в воздухе, не подменяя твоего следующего решения."
        )
    if scenario_type == "training":
        return "Ситуация меняется только в пределах явно выбранного действия. Следующий этап сценария готов к продолжению."
    first = RESULT_NARRATION.get(outcome.result, "Сцена сдвигается дальше, но без лишних уступок за кадром.")
    if outcome.blocked_reasons:
        second = "Что-то в устройстве мира упирается и не дает продавить желаемое напрямую."
    elif outcome.result in {"critical_success", "success"}:
        second = "Мир не делает лишних подарков, но сейчас у тебя есть честное окно для следующего шага."
    elif outcome.result == "partial_success":
        second = "Дальше придется выбрать, чем воспользоваться и какую цену принять."
    else:
        second = "Остается решить, как обойти препятствие или чем рискнуть дальше."
    return f"{first} {second}"
