"""Conservative parser for explicit learner actions."""

from __future__ import annotations

from app.models.schemas import Intent


class IntentParser:
    def parse(self, latest_user_message: str, *, mechanical: bool = False) -> Intent:
        if mechanical:
            raise ValueError("training gateway does not support mechanical checks")
        text = latest_user_message.strip()
        return Intent(
            action_type="narrative",
            desired_outcome=text,
            methods=["free_text"],
            facts_claimed_by_player=self.claimed_facts(text),
            ambiguities=[],
            confidence=1.0,
            skill=0,
            preparation=0,
            leverage=0,
            difficulty=0,
            resource_amount=0.0,
        )

    @staticmethod
    def claimed_facts(text: str) -> list[str]:
        lowered = text.lower()
        markers = ["i already", "я уже", "у меня есть", "он обязан", "это факт"]
        return [marker for marker in markers if marker in lowered]
