"""Narrative output validation."""

from __future__ import annotations

import re
from typing import Any

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
    "critical_success": "Сцена открывает ясный проход вперед.",
    "success": "Сцена продолжает двигаться в выбранном направлении.",
    "partial_success": "Сцена сдвигается вперед, но часть результата остается неустойчивой.",
    "failure_with_progress": "Желаемое сразу не складывается, но остается узкая зацепка.",
    "failure": "Попытка не дает прямого результата; напряжение удерживается в сцене.",
    "critical_failure": "Попытка резко ухудшает положение, и последствия становятся заметны сразу.",
}
DOUBLE_EXTENSION_RE = re.compile(r"\b[\w.-]+\.(?:xlsx|xlsm|docx|pdf|zip|rar|7z|pptx)\.exe\b", re.IGNORECASE)
DANGEROUS_FILE_ACTION_MARKERS = ("откры", "запуск", "запуст", "скач", "open", "run", "download")


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


def safe_fallback(outcome: Outcome, state: dict[str, Any] | None = None, latest_user_message: str = "") -> str:
    if state and state.get("meta", {}).get("campaign_id") == "awareness":
        return awareness_safe_fallback(state, latest_user_message)
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


def awareness_safe_fallback(state: dict[str, Any], latest_user_message: str) -> str:
    resources = state.get("player", {}).get("resources", {})
    window = resources.get("current-turn-window") if isinstance(resources, dict) else None
    window_text = f" ({window})" if isinstance(window, str) and window else ""
    if DOUBLE_EXTENSION_RE.search(latest_user_message) and has_dangerous_file_action(latest_user_message):
        return (
            f"Рабочий блок{window_text} продолжается без заметных окон, ошибок или немедленных внешних изменений. "
            "Календарь и переписка остаются в обычном ритме: текущие задачи ждут решения, а коллеги рассчитывают на твой статус. "
            "Что делаешь дальше?"
        )
    return (
        f"Рабочий блок{window_text} продолжается в обычном ритме: сообщения, письма и календарь остаются перед тобой. "
        "Что делаешь дальше?"
    )


def has_dangerous_file_action(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in DANGEROUS_FILE_ACTION_MARKERS)
