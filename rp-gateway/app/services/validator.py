"""Narrative output validation."""

from __future__ import annotations

import re

from app.models.schemas import Outcome, ValidationResult


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
    "critical_success": "Твой ход срабатывает особенно чисто: сцена открывает лучший проход вперед.",
    "success": "Твой ход срабатывает: ситуация складывается в твою пользу.",
    "partial_success": "Твой ход срабатывает не полностью, но дает проход вперед с ценой, условием или задержкой.",
    "failure_with_progress": "Желаемый результат ускользает, но сцена оставляет узкую зацепку.",
    "failure": "Попытка не дает желаемого результата; напряжение растет, и действовать дальше придется осторожнее.",
    "critical_failure": "Попытка оборачивается жесткой неудачей, и цена момента становится заметной сразу.",
}


class OutputValidator:
    def validate(self, text: str, outcome: Outcome) -> ValidationResult:
        lowered = text.lower()
        violations: list[str] = []
        if "<authoritative_outcome>" in lowered or "</authoritative_outcome>" in lowered:
            violations.append("Narrative exposed service outcome tags to the player.")
        if SERVICE_LINE_RE.search(text):
            violations.append("Narrative exposed analysis, recommendation, or diagnostic labels to the player.")
        for phrase in SERVICE_PHRASES:
            if phrase in lowered:
                violations.append(f"Narrative exposed service wording: {phrase}")
        if outcome.result in {"failure", "critical_failure", "failure_with_progress"}:
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
        if violations:
            return ValidationResult(
                valid=False,
                violations=violations,
                repair_instruction=(
                    "Rewrite as final in-world narration only. Remove analysis, recommendation, diagnostics, "
                    "Gateway/service wording, result labels, and hidden concessions. Keep player agency intact."
                ),
            )
        return ValidationResult(valid=True)


def safe_fallback(outcome: Outcome) -> str:
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
