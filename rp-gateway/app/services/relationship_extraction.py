"""RP relationship-event extraction through the global service model."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.services.narrative import NarrativeClient, json_object_content, response_text
from app.services.relationship_attribution import (
    REJECTION_CODES,
    RelationshipExtractionRejected,
    normalized_aliases,
    resolve_mention,
)
from app.services.relationship_store import RelationshipStore
from app.services.relationships import RelationshipMechanics
from app.services.service_model_client import ServiceModelClient, service_prompt_text
from app.services.service_models import local_service_model_settings, service_model_settings
from app.services.state_store import StateStore


logger = logging.getLogger(__name__)

MAX_EVENTS_PER_TURN = 5


class RelationshipExtractionService:
    """Extract qualitative events from one recorded turn and apply them once."""

    def __init__(self, settings: Settings, store: StateStore, model: dict[str, Any]):
        self.settings = settings
        self.store = store
        self.model = model
        self.relationship_store = RelationshipStore(store, model)
        self.mechanics = RelationshipMechanics(
            store,
            model,
            rp_contract_revision=settings.rp_contract_revision,
        )

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
        if turn.get("noncanonical_safe_fallback"):
            return {
                "processed": False,
                "applied": False,
                "turn_id": int(turn_id),
                "reason": "noncanonical_safe_fallback",
                "events": [],
            }
        request_id = str(turn.get("request_id") or "") or None
        party_turn = turn.get("party_turn")
        if isinstance(party_turn, bool) or not isinstance(party_turn, int) or party_turn < 0:
            self.store.audit(
                "relationship_extraction_failed",
                {"turn_id": int(turn_id), "error": "missing_party_turn"},
                request_id,
            )
            raise RuntimeError(f"relationship extraction missing party_turn for turn_id={turn_id}")
        aliases = self._aliases()
        turn_text = self._turn_text(turn)

        try:
            raw_response = await self._complete(turn, aliases, authorization)
            parsed = self.parse_response(
                response_text(raw_response),
                aliases=aliases,
                turn_text=turn_text,
            )
        except RelationshipExtractionRejected as exc:
            audit_payload = {"turn_id": int(turn_id), "code": exc.code}
            if exc.mention is not None:
                audit_payload["mention"] = exc.mention
            self.store.audit(
                "relationship_extraction_rejected",
                audit_payload,
                request_id,
            )
            return {
                "processed": True,
                "applied": False,
                "turn_id": int(turn_id),
                "rejection_code": exc.code,
                "events": [],
            }

        applied = self.mechanics.apply_events(
            turn_id=int(turn_id),
            party_turn=party_turn,
            events=parsed["events"],
        )
        delivered_favours = self.mechanics.resolve_delivered_favours(
            turn_id=int(turn_id),
            party_turn=party_turn,
            narrative_response=str(turn.get("narrative_response") or ""),
        )
        applied.extend(delivered_favours)
        self.store.audit(
            "relationship_extraction_applied",
            {
                "turn_id": int(turn_id),
                "party_turn": party_turn,
                "extracted_events": len(parsed["events"]),
                "applied_events": len(applied),
                "delivered_favours": len(delivered_favours),
                "model": raw_response.get("model"),
            },
            request_id,
        )
        return {
            "processed": True,
            "applied": bool(applied),
            "turn_id": int(turn_id),
            "party_turn": party_turn,
            "events": applied,
            "delivered_favours": delivered_favours,
        }

    def parse_response(
        self,
        payload: object,
        *,
        aliases: dict[str, list[str]] | None = None,
        turn_text: str = "",
    ) -> dict[str, Any]:
        """Parse and validate the all-or-nothing qualitative extraction payload."""
        try:
            if isinstance(payload, str):
                data = json.loads(json_object_content(payload))
            elif isinstance(payload, (bytes, bytearray)):
                data = json.loads(json_object_content(payload.decode("utf-8")))
            else:
                data = payload
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise RelationshipExtractionRejected("malformed_response") from exc

        # Scan the complete decoded document before inspecting its expected shape.
        # bool is a JSON boolean, not a numeric relationship value.
        if self._contains_number(data):
            raise RelationshipExtractionRejected("numeric_field_present")
        if not isinstance(data, dict) or set(data) != {"events"} or not isinstance(data["events"], list):
            raise RelationshipExtractionRejected("malformed_response")

        events = data["events"]
        if len(events) > MAX_EVENTS_PER_TURN:
            raise RelationshipExtractionRejected("too_many_events")

        event_ids = set(self._event_models())
        normalized: list[dict[str, str]] = []
        for event in events:
            if not isinstance(event, dict):
                raise RelationshipExtractionRejected("malformed_response")
            if "character_id" in event:
                raise RelationshipExtractionRejected("character_id_present")
            if "evidence_quote" in event:
                raise RelationshipExtractionRejected("malformed_response")
            if "evidence" not in event or not isinstance(event.get("evidence"), str) or not event["evidence"].strip():
                raise RelationshipExtractionRejected("missing_evidence")
            if "character_mention" not in event or not isinstance(event.get("character_mention"), str) or not event["character_mention"].strip():
                raise RelationshipExtractionRejected("mention_missing")
            if set(event) != {"character_mention", "event_id", "evidence"}:
                raise RelationshipExtractionRejected("malformed_response")
            character_mention = event.get("character_mention")
            event_id = event.get("event_id")
            evidence = event.get("evidence")
            if not isinstance(event_id, str) or event_id not in event_ids:
                raise RelationshipExtractionRejected("unknown_event_id")
            character_id = resolve_mention(
                character_mention,
                evidence=evidence,
                turn_text=turn_text,
                aliases=aliases or {},
            )
            normalized.append(
                {
                    "character_id": character_id,
                    "character_mention": character_mention.strip(),
                    "event_id": event_id,
                    "evidence": evidence.strip(),
                }
            )
        return {"events": normalized}

    async def _complete(
        self,
        turn: dict[str, Any],
        aliases: dict[str, list[str]],
        authorization: str | None,
    ) -> dict[str, Any]:
        # The global service model has its own credentials. Party BYOK is never
        # forwarded, but the public method keeps the common service-job signature.
        _ = authorization
        settings = (
            local_service_model_settings(self.settings)
            if self.settings.rp_contract_revision >= 9
            else service_model_settings(self.settings)
        )
        if settings.llm_api_base.startswith("mock://"):
            return self._mock_response(settings.narrative_model)

        payload = self._completion_payload(
            turn,
            aliases,
            settings.narrative_model,
            enforce_json_schema=settings.llm_provider == "local",
        )
        client_policy = NarrativeClient(settings)
        client_policy.apply_prompt_cache_policy(payload)
        attempts = client_policy.model_attempts(settings.narrative_model)
        service_client = ServiceModelClient(settings)
        last_error: Exception | None = None

        for index, model_name in enumerate(attempts):
            request_payload = dict(payload)
            request_payload["model"] = model_name
            client_policy.apply_model_policy(request_payload, model_name)
            started = time.perf_counter()
            try:
                completion = await service_client.complete(
                    role="relationship_extraction",
                    provider=settings.llm_provider,
                    model=model_name,
                    party_id=self.store.campaign_id,
                    turn_id=int(turn["id"]),
                    request_id=str(turn.get("request_id") or "") or None,
                    party_turn=turn.get("party_turn"),
                    attempt=index + 1,
                    prompt=service_prompt_text(request_payload),
                    payload=request_payload,
                )
                result = completion.data
                result.setdefault("model", model_name)
                logger.info(
                    "relationship_extraction_success campaign_id=%s turn_id=%s model=%s elapsed_ms=%s",
                    self.store.campaign_id,
                    turn["id"],
                    result.get("model"),
                    round((time.perf_counter() - started) * 1000, 2),
                )
                return result
            except (httpx.TimeoutException, httpx.HTTPStatusError, RuntimeError, ValueError) as exc:
                last_error = exc
                if index >= len(attempts) - 1:
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("No service-model attempts configured for relationship extraction")

    def _completion_payload(
        self,
        turn: dict[str, Any],
        aliases: dict[str, list[str]],
        model_name: str,
        *,
        enforce_json_schema: bool = False,
    ) -> dict[str, Any]:
        state = self.store.get_state()
        characters = state.get("characters") if isinstance(state.get("characters"), dict) else {}
        character_catalog = []
        for character_id in sorted(aliases):
            character = characters.get(character_id)
            if not isinstance(character, dict):
                character = {}
            knowledge = character.get("knowledge")
            identity_hint = ""
            if isinstance(knowledge, list):
                identity_hint = next(
                    (
                        str(item.get("text") or "").strip()[:240]
                        for item in knowledge
                        if isinstance(item, dict) and str(item.get("text") or "").strip()
                    ),
                    "",
                )
            character_catalog.append(
                {
                    "name": aliases[character_id][0] if aliases[character_id] else character_id,
                    "aliases": aliases[character_id],
                    "identity_hint": identity_hint,
                }
            )
        context = {
            "turn": {
                "player_message": str(turn.get("player_message") or ""),
                "narrative_response": str(turn.get("narrative_response") or ""),
            },
            "characters": character_catalog,
            "allowed_event_ids": sorted(self._event_models()),
        }
        payload: dict[str, Any] = {
            "model": model_name,
            "stream": False,
            "temperature": 0,
            "max_tokens": 700,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Extract only relationship events that are directly evidenced by this completed RP turn. "
                        "Return strict JSON with exactly one top-level key, events. Each event object must contain "
                        "exactly these JSON keys: \"character_mention\", \"event_id\", and \"evidence\". Put a short "
                        "verbatim quote from the supplied turn in \"evidence\"; never use \"evidence_quote\". "
                        "Use only the supplied alias forms for character_mention; never output an internal character "
                        "ID. Return at most five events. Do not "
                        "output numbers in any field. "
                        "Each event's evidence must be one self-contained verbatim fragment that explicitly shows "
                        "both the player and the named character in the completed interaction; do not combine "
                        "separate snippets. The character's presence, routine action, or danger alone is not enough. "
                        "For shared_risk, that one fragment must explicitly show both the player and the character "
                        "jointly facing the same concrete danger. A fragment saying only that the character holds a "
                        "rope near a breach or chasm is not shared_risk because it shows only the character. "
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
        if enforce_json_schema:
            character_mentions = sorted(
                {
                    alias
                    for character_aliases in aliases.values()
                    for alias in character_aliases
                    if alias
                }
            )
            character_mention_schema: dict[str, Any] = {"type": "string"}
            if character_mentions:
                character_mention_schema["enum"] = character_mentions
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "relationship_events",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "events": {
                                "type": "array",
                                "maxItems": MAX_EVENTS_PER_TURN,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "character_mention": character_mention_schema,
                                        "event_id": {
                                            "type": "string",
                                            "enum": sorted(self._event_models()),
                                        },
                                        "evidence": {"type": "string"},
                                    },
                                    "required": ["character_mention", "event_id", "evidence"],
                                    "additionalProperties": False,
                                },
                            }
                        },
                        "required": ["events"],
                        "additionalProperties": False,
                    },
                },
            }
        return payload

    def _recorded_turn(self, turn_id: int) -> dict[str, Any]:
        if isinstance(turn_id, bool) or not isinstance(turn_id, int) or turn_id <= 0:
            raise ValueError("turn_id must be a positive integer")
        rows = self.store.turns_for_memory(
            after_turn_id=turn_id - 1,
            to_turn_id=turn_id,
            limit=1,
            include_noncanonical_fallback=True,
        )
        if not rows or int(rows[0]["id"]) != turn_id:
            raise ValueError(f"turn not found: {turn_id}")
        return rows[0]

    def _aliases(self) -> dict[str, list[str]]:
        return normalized_aliases(self.model)

    @staticmethod
    def _turn_text(turn: dict[str, Any]) -> str:
        return f"{str(turn.get('player_message') or '')}\n{str(turn.get('narrative_response') or '')}"

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
