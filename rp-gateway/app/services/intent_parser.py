"""Conservative player intent parser with a legacy mechanical compatibility path."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any

from pydantic import ValidationError

from app.models.schemas import CheckType, Intent


CHECK_TYPES = {
    "persuasion",
    "intimidation",
    "deception",
    "stealth",
    "information",
    "resource",
    "feasibility",
    "trust",
    "conflict",
    "random_event",
}


class IntentParser:
    def parse(self, latest_user_message: str, *, mechanical: bool = True) -> Intent:
        text = latest_user_message.strip()
        match = re.search(r"/check\s+([a-zA-Z_ -]+)", text)
        if not match:
            return Intent(
                action_type="feasibility" if mechanical else "narrative",
                # Training scoring must see the complete explicit action. Party
                # messages are already bounded by the API schema, so truncating
                # here only creates silent false negatives.
                desired_outcome=text,
                methods=["free_text"],
                facts_claimed_by_player=self.claimed_facts(text),
                ambiguities=(
                    ["No explicit /check command; using safe feasibility category."]
                    if mechanical
                    else []
                ),
                confidence=0.35 if mechanical else 1.0,
            )

        command = text[match.start() :].splitlines()[0]
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        action = parts[1].replace("-", "_").lower() if len(parts) > 1 else "feasibility"
        if action not in CHECK_TYPES:
            action = "feasibility"

        fields: dict[str, str] = {}
        loose: list[str] = []
        for part in parts[2:]:
            if "=" in part:
                key, value = part.split("=", 1)
                fields[key.strip().lower().replace("-", "_")] = value.strip()
            else:
                loose.append(part)

        desired = fields.get("goal") or fields.get("outcome") or fields.get("desired_outcome") or " ".join(loose)
        resource = fields.get("resource")
        return Intent(
            action_type=action if mechanical else "narrative",  # type: ignore[arg-type]
            actor=fields.get("actor", "player"),
            target=fields.get("target"),
            desired_outcome=desired[:500],
            methods=[fields.get("method", "explicit_check")] if mechanical else ["free_text"],
            resources_claimed=[resource] if resource else [],
            facts_claimed_by_player=self.claimed_facts(text),
            ambiguities=[] if desired else ["Desired outcome is not explicit."],
            confidence=(0.9 if desired else 0.7) if mechanical else 1.0,
            skill=self.safe_int(fields.get("skill"), 0) if mechanical else 0,
            preparation=self.safe_int(fields.get("preparation"), 0) if mechanical else 0,
            leverage=self.safe_int(fields.get("leverage"), 0) if mechanical else 0,
            difficulty=self.safe_int(fields.get("difficulty"), 10) if mechanical else 0,
            resource_amount=self.safe_float(fields.get("amount"), 1.0) if mechanical else 0.0,
        )

    def parse_json_intent(self, raw: str) -> Intent:
        try:
            data: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid intent JSON: {exc.msg}") from exc
        try:
            return Intent.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"intent validation failed: {exc}") from exc

    def claimed_facts(self, text: str) -> list[str]:
        lowered = text.lower()
        markers = ["i already", "я уже", "у меня есть", "он обязан", "это факт"]
        return [marker for marker in markers if marker in lowered]

    def safe_int(self, value: str | None, default: int) -> int:
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def safe_float(self, value: str | None, default: float) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default
