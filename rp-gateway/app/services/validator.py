"""Narrative output validation."""

from __future__ import annotations

from app.models.schemas import Outcome, ValidationResult


class OutputValidator:
    def validate(self, text: str, outcome: Outcome) -> ValidationResult:
        lowered = text.lower()
        violations: list[str] = []
        if "<authoritative_outcome>" in lowered or "</authoritative_outcome>" in lowered:
            violations.append("Narrative exposed service outcome tags to the player.")
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
                repair_instruction="Remove the hidden concession, preserve the fixed result, and keep player agency intact.",
            )
        return ValidationResult(valid=True)


def safe_fallback(outcome: Outcome) -> str:
    target = outcome.target or "the scene"
    consequences = " ".join(outcome.consequences)
    return (
        f"The action resolves as {outcome.result}. {target} follows the fixed outcome: "
        f"{consequences} The narration preserves the listed constraints without adding a hidden success."
    )
