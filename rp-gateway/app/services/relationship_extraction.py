"""RP relationship-event extraction through the global service model."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.services.narrative import NarrativeClient, response_text
from app.services.provider_auth import outbound_headers
from app.services.relationship_store import RelationshipStore
from app.services.relationships import RelationshipMechanics
from app.services.service_models import service_model_settings
from app.services.state_store import StateStore


logger = logging.getLogger(__name__)

REJECTION_CODES = frozenset(
    {
        "unknown_event_id",
        "unknown_character_id",
        "missing_evidence",
        "numeric_field_present",
        "too_many_events",
    }
)
MAX_EVENTS_PER_TURN = 5


class RelationshipExtractionRejected(ValueError):
    """A complete, syntactically valid model response rejected by contract."""

    def __init__(self, code: str):
        if code not in REJECTION_CODES:
            raise ValueError(f"unsupported relationship extraction rejection code: {code}")
        self.code = code
        super().__init__(code)


class RelationshipExtractionService:
    """Extract qualitative events from one recorded turn and apply them once."""

    def __init__(self, settings: Settings, store: StateStore, model: dict[str, Any]):
        self.settings = settings
        self.store = store
        self.model = model
        self.relationship_store = RelationshipStore(store, model)
        self.mechanics = RelationshipMechanics(store, model)

    async def process_turn(self, turn_id: int, authorization: str | None = None) -> dict[str, Any]:
        """Process exactly one recorded turn; semantic rejection is terminal."""
        if self.settings.scenario_type != "rp":
            return {
                "processed": False,
                "applied": False,
                "turn_id": int(turn_id),
                "reason": "scenario_not_rp",
                "events": [],
            }
        turn = self._recorded_turn(turn_id)
        request_id = str(turn.get("request_id") or "") or None
        character_ids = self._character_ids()

        try:
            raw_response = await self._complete(turn, character_ids, authorization)
            parsed = self.parse_response(response_text(raw_response), character_ids=character_ids)
        except RelationshipExtractionRejected as exc:
            self.store.audit(
                "relationship_extraction_rejected",
                {"turn_id": int(turn_id), "code": exc.code},
                request_id,
            )
            return {
                "processed": True,
                "applied": False,
                "turn_id": int(turn_id),
                "rejection_code": exc.code,
                "events": [],
            }

        applied = self.mechanics.apply_events(turn_id=int(turn_id), events=parsed["events"])
        self.store.audit(
            "relationship_extraction_applied",
            {
                "turn_id": int(turn_id),
                "extracted_events": len(parsed["events"]),
                "applied_events": len(applied),
                "model": raw_response.get("model"),
            },
            request_id,
        )
        return {
            "processed": True,
            "applied": bool(applied),
            "turn_id": int(turn_id),
            "events": applied,
        }

    def parse_response(self, payload: object, *, character_ids: set[str]) -> dict[str, Any]:
        """Parse and validate the all-or-nothing qualitative extraction payload."""
        if isinstance(payload, str):
            data = json.loads(payload)
        elif isinstance(payload, (bytes, bytearray)):
            data = json.loads(payload.decode("utf-8"))
        else:
            data = payload

        # Scan the complete decoded document before inspecting its expected shape.
        # bool is a JSON boolean, not a numeric relationship value.
        if self._contains_number(data):
            raise RelationshipExtractionRejected("numeric_field_present")
        if not isinstance(data, dict) or set(data) != {"events"} or not isinstance(data["events"], list):
            raise ValueError("relationship extraction response must be an object containing only an events array")

        events = data["events"]
        if len(events) > MAX_EVENTS_PER_TURN:
            raise RelationshipExtractionRejected("too_many_events")

        event_ids = set(self._event_models())
        normalized: list[dict[str, str]] = []
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("each relationship event must be an object")
            character_id = event.get("character_id")
            event_id = event.get("event_id")
            evidence = event.get("evidence")
            if not isinstance(character_id, str) or character_id not in character_ids:
                raise RelationshipExtractionRejected("unknown_character_id")
            if not isinstance(event_id, str) or event_id not in event_ids:
                raise RelationshipExtractionRejected("unknown_event_id")
            if not isinstance(evidence, str) or not evidence.strip():
                raise RelationshipExtractionRejected("missing_evidence")
            if set(event) != {"character_id", "event_id", "evidence"}:
                raise ValueError(
                    "each relationship event must contain only character_id, event_id, and evidence"
                )
            normalized.append(
                {
                    "character_id": character_id,
                    "event_id": event_id,
                    "evidence": evidence.strip(),
                }
            )
        return {"events": normalized}

    async def _complete(
        self,
        turn: dict[str, Any],
        character_ids: set[str],
        authorization: str | None,
    ) -> dict[str, Any]:
        # The global service model has its own credentials. Party BYOK is never
        # forwarded, but the public method keeps the common service-job signature.
        _ = authorization
        settings = service_model_settings(self.settings)
        if settings.nvidia_api_base.startswith("mock://"):
            return self._mock_response(settings.narrative_model)

        payload = self._completion_payload(turn, character_ids, settings.narrative_model)
        client_policy = NarrativeClient(settings)
        client_policy.apply_prompt_cache_policy(payload)
        timeout = httpx.Timeout(settings.model_attempt_timeout_seconds, connect=15.0)
        attempts = client_policy.model_attempts(settings.narrative_model)
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, model_name in enumerate(attempts):
                request_payload = dict(payload)
                request_payload["model"] = model_name
                client_policy.apply_model_policy(request_payload, model_name)
                started = time.perf_counter()
                try:
                    response = await client.post(
                        f"{settings.nvidia_api_base.rstrip('/')}/chat/completions",
                        json=request_payload,
                        headers=outbound_headers(settings, None),
                    )
                    response.raise_for_status()
                    result = response.json()
                    if not isinstance(result, dict):
                        raise ValueError("relationship extraction provider returned a non-object response")
                    result.setdefault("model", model_name)
                    logger.info(
                        "relationship_extraction_success campaign_id=%s turn_id=%s model=%s elapsed_ms=%s",
                        self.store.campaign_id,
                        turn["id"],
                        result.get("model"),
                        round((time.perf_counter() - started) * 1000, 2),
                    )
                    return result
                except (httpx.TimeoutException, httpx.HTTPStatusError, ValueError) as exc:
                    last_error = exc
                    if index >= len(attempts) - 1:
                        raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("No service-model attempts configured for relationship extraction")

    def _completion_payload(
        self,
        turn: dict[str, Any],
        character_ids: set[str],
        model_name: str,
    ) -> dict[str, Any]:
        state = self.store.get_state()
        characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
        character_catalog = [
            {
                "character_id": character_id,
                "name": (
                    str(characters.get(character_id, {}).get("name") or character_id)
                    if isinstance(characters.get(character_id), dict)
                    else character_id
                ),
            }
            for character_id in sorted(character_ids)
        ]
        context = {
            "turn": {
                "player_message": str(turn.get("player_message") or ""),
                "narrative_response": str(turn.get("narrative_response") or ""),
            },
            "characters": character_catalog,
            "allowed_event_ids": sorted(self._event_models()),
        }
        return {
            "model": model_name,
            "stream": False,
            "temperature": 0,
            "max_tokens": 700,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract only relationship events that are directly evidenced by this completed RP turn. "
                        "Return strict JSON with exactly one top-level key, events. Each event must contain exactly "
                        "character_id, event_id, and a short verbatim evidence quote from the supplied turn. Use only "
                        "the supplied identifiers. Return at most five events. Do not output numbers in any field. "
                        "Do not infer hidden motives, scores, weights, bands, or events not completed in the turn. "
                        "If nothing qualifies, return {\"events\":[]}."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
                },
            ],
        }

    def _recorded_turn(self, turn_id: int) -> dict[str, Any]:
        if isinstance(turn_id, bool) or not isinstance(turn_id, int) or turn_id <= 0:
            raise ValueError("turn_id must be a positive integer")
        rows = self.store.turns_for_memory(after_turn_id=turn_id - 1, to_turn_id=turn_id, limit=1)
        if not rows or int(rows[0]["id"]) != turn_id:
            raise ValueError(f"turn not found: {turn_id}")
        return rows[0]

    def _character_ids(self) -> set[str]:
        characters = self.store.get_state().get("characters")
        if not isinstance(characters, dict):
            return set()
        return {character_id for character_id in characters if isinstance(character_id, str)}

    def _event_models(self) -> dict[str, Any]:
        events = self.model.get("events")
        return events if isinstance(events, dict) else {}

    @classmethod
    def _contains_number(cls, value: object) -> bool:
        if isinstance(value, bool) or value is None or isinstance(value, str):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, dict):
            return any(cls._contains_number(key) or cls._contains_number(item) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(cls._contains_number(item) for item in value)
        return False

    @staticmethod
    def _mock_response(model_name: str) -> dict[str, Any]:
        return {
            "model": model_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": '{"events":[]}'},
                    "finish_reason": "stop",
                }
            ],
        }
