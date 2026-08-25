"""Revision-9 out-of-fiction GM correction channel."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any

from app.core.config import Settings
from app.models.schemas import PartyGMPatchDraft
from app.services.narrative import response_text
from app.services.rp_history import eligible_rp_turns
from app.services.rp_story_memory import (
    STORY_LIST_FIELDS,
    STORY_MEMORY_SECTION_FIELDS,
    normalize_sectioned_story_memory,
    story_fact_fingerprint,
    story_fact_id,
    validate_story_memory_corrections,
)
from app.services.service_model_client import ServiceModelClient, service_prompt_text
from app.services.service_models import local_service_model_settings
from app.services.state_store import StateStore


GM_INTENT_INPUT_MAX_CHARS = 2_000
GM_INTENT_OUTPUT_MAX_TOKENS = 100
GM_PATCH_INPUT_MAX_CHARS = 4_000
GM_PATCH_OUTPUT_MAX_TOKENS = 300
PLAYER_CORRECTION_MAX_CHARS = 600
ACTIVE_PLAYER_CORRECTION_LIMIT = 20
PLAYER_CORRECTION_SCHEMA = "rp-gateway.player-correction.v1"
GM_PATCH_DRAFT_SCHEMA = "rp-gateway.gm-patch-draft.v1"

FIELD_TO_SECTION = {
    field: section_key
    for section_key, fields in STORY_MEMORY_SECTION_FIELDS.items()
    for field in fields
    if field != "current_situation"
}


class RPGMService:
    """Classify, draft and validate one tightly scoped player correction."""

    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store

    @property
    def enabled(self) -> bool:
        return self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 9

    def active_corrections(self) -> list[dict[str, Any]]:
        """Return the latest effective record for each slot when that record is active."""

        by_slot: dict[str, dict[str, Any]] = {}
        for correction in self.store.player_correction_records():
            slot = str(correction.get("target_slot") or "")
            if slot:
                by_slot[slot] = correction
        return sorted(
            (
                correction
                for correction in by_slot.values()
                if correction.get("status") == "active"
            ),
            key=lambda item: int(item.get("source_turn_id") or 0),
        )

    def overlay_block(self) -> str | None:
        corrections = self.active_corrections()
        if not corrections:
            return None
        lines = [
            "ИСПРАВЛЕНИЯ ИГРОКА",
            "Эти подтверждённые исправления выше дословной истории и story memory. "
            "Старое утверждение в RAW является только исторической репликой. "
            "Они не изменяют WORLD_ABSOLUTE_RULES и не подменяют текущее действие игрока.",
        ]
        for correction in corrections:
            before = str(correction.get("before") or "").strip()
            after = str(correction.get("after") or "").strip()
            if correction.get("action") == "retract" or not after:
                lines.append(f"- Считать неверным: {before}")
            else:
                lines.append(f"- Было: {before} → Верно: {after}")
        return "\n".join(lines)

    def correction_for_request(self, request_id: str | None) -> dict[str, Any] | None:
        if not request_id:
            return None
        matches = [
            item
            for item in self.store.player_correction_records()
            if item.get("request_id") == request_id and item.get("status") == "active"
        ]
        return matches[-1] if matches else None

    async def classify(self, content: str, *, request_id: str) -> dict[str, Any]:
        """Return scene/correction/uncertain; failures deliberately become a user choice."""

        payload = self.intent_payload(content)
        prompt = service_prompt_text(payload)
        runtime = local_service_model_settings(self.settings)
        if runtime.llm_api_base.startswith("mock://"):
            return self.mock_intent(content)
        try:
            completion = await ServiceModelClient(runtime).complete(
                role="gm_intent",
                provider="local",
                model=runtime.local_llm_model_alias,
                party_id=self.store.campaign_id,
                turn_id=None,
                request_id=request_id,
                party_turn=int(self.store.get_state().get("meta", {}).get("turn") or 0),
                attempt=1,
                prompt=prompt,
                payload=payload,
            )
            if self.finish_reason(completion.data) == "length":
                return {"label": "uncertain", "target": None, "reason": "length"}
            decoded = json.loads(response_text(completion.data))
            if not isinstance(decoded, dict) or set(decoded) != {"label", "target"}:
                raise ValueError("gm_intent must return exactly label and target")
            label = str(decoded.get("label") or "")
            target = decoded.get("target")
            if label not in {"scene", "correction", "uncertain"}:
                raise ValueError("gm_intent returned an unsupported label")
            if target is not None and not isinstance(target, str):
                raise ValueError("gm_intent target must be a string or null")
            return {"label": label, "target": target.strip() if isinstance(target, str) else None}
        except Exception as exc:  # noqa: BLE001 - classifier uncertainty must not mutate gameplay
            return {
                "label": "uncertain",
                "target": None,
                "reason": f"{type(exc).__name__}",
            }

    async def draft(
        self,
        content: str,
        *,
        request_id: str,
        target_hint: str | None = None,
    ) -> PartyGMPatchDraft:
        instruction = content.strip()
        if not instruction:
            raise ValueError("GM correction is empty")
        if len(instruction) > PLAYER_CORRECTION_MAX_CHARS:
            raise ValueError(
                f"GM correction exceeds {PLAYER_CORRECTION_MAX_CHARS} characters"
            )
        self.require_capacity_before_model(instruction, target_hint)
        candidates = self.correction_candidates(instruction, target_hint=target_hint)
        if not candidates:
            raise ValueError("No existing memory fact, recent RAW assertion, or absolute rule can be corrected")
        payload, included = self.patch_payload(instruction, candidates)
        prompt = service_prompt_text(payload)
        runtime = local_service_model_settings(self.settings)
        proposal_id = f"gm-{uuid.uuid4().hex[:20]}"
        if runtime.llm_api_base.startswith("mock://"):
            decoded = self.mock_patch(instruction, included[0])
        else:
            completion = await ServiceModelClient(runtime).complete(
                role="gm_patch_draft",
                provider="local",
                model=runtime.local_llm_model_alias,
                party_id=self.store.campaign_id,
                turn_id=None,
                request_id=request_id,
                party_turn=int(self.store.get_state().get("meta", {}).get("turn") or 0),
                attempt=1,
                prompt=prompt,
                payload=payload,
            )
            if self.finish_reason(completion.data) == "length":
                raise ValueError("GM patch draft was truncated by the output limit")
            decoded = json.loads(response_text(completion.data))
        return self.normalize_patch_draft(decoded, included, proposal_id)

    def validate_confirmed_proposal(self, proposal: PartyGMPatchDraft) -> dict[str, Any]:
        if not self.enabled:
            raise ValueError("GM corrections require an RP revision-9 party")
        current_version = self.store.current_version() or 0
        if int(proposal.base_state_version) != int(current_version):
            raise ValueError("Party state changed after the GM draft; create a new draft")
        candidates = self.correction_candidates("", target_hint=proposal.target_slot, include_all=True)
        candidate = next(
            (
                item
                for item in candidates
                if item["target_slot"] == proposal.target_slot
                or proposal.target_slot.startswith(f"{item['target_slot']}:")
            ),
            None,
        )
        if candidate is None:
            raise ValueError("GM correction target is no longer available")
        self.validate_proposal_against_candidate(proposal.model_dump(mode="json"), candidate)
        if proposal.target_kind == "memory":
            correction = self.story_memory_correction(proposal)
            validate_story_memory_corrections(
                self.store.effective_rp_story_memory(),
                [correction],
                self.settings.rp_story_memory_max_chars,
            )
        self.require_confirm_capacity(proposal.target_slot)
        return candidate

    def player_correction_artifact(
        self,
        proposal: PartyGMPatchDraft,
        *,
        party_turn: int,
        timestamp: int | None = None,
    ) -> dict[str, Any]:
        created_at = int(timestamp if timestamp is not None else time.time())
        artifact = {
            "schema_version": PLAYER_CORRECTION_SCHEMA,
            "correction_id": proposal.proposal_id,
            "target_kind": proposal.target_kind,
            "target_id": proposal.target_id,
            "target_slot": proposal.target_slot,
            "target_turn_id": proposal.target_turn_id,
            "field": proposal.field,
            "section_key": proposal.section_key,
            "action": proposal.action,
            "before": proposal.before,
            "after": proposal.after,
            "provenance": {
                "source": "player",
                "party_turn": int(party_turn),
                "timestamp": created_at,
            },
            "status": "absorbed" if proposal.target_kind == "absolute_rule" else "active",
        }
        if proposal.target_kind in {"memory", "raw"}:
            correction = self.story_memory_correction(proposal)
            artifact["story_memory_correction"] = correction
            artifact["replacement_fact_id"] = (
                story_fact_id(None, str(proposal.after or ""))
                if proposal.action == "replace"
                else proposal.target_id
            )
            if proposal.target_kind == "raw":
                artifact["synthetic_before_fact"] = {
                    "fact_id": correction["fact_id"],
                    "text": proposal.before,
                }
        return artifact

    def story_memory_correction(self, proposal: PartyGMPatchDraft) -> dict[str, str]:
        fact_id = proposal.target_id
        if proposal.target_kind == "raw":
            digest = hashlib.sha256(
                f"{proposal.target_turn_id}:{story_fact_fingerprint(proposal.before)}".encode("utf-8")
            ).hexdigest()[:20]
            fact_id = f"raw:{digest}"
        correction = {
            "field": str(proposal.field),
            "fact_id": fact_id,
            "action": proposal.action,
        }
        if proposal.after:
            correction["replacement_text"] = proposal.after
        return correction

    def require_capacity_before_model(self, instruction: str, target_hint: str | None) -> None:
        active = self.active_corrections()
        if len(active) < ACTIVE_PLAYER_CORRECTION_LIMIT:
            return
        resolved_hint = target_hint or self.match_active_slot(instruction, active)
        if resolved_hint and any(
            resolved_hint == str(item.get("target_slot") or "")
            or resolved_hint.startswith(f"{item.get('target_slot')}:")
            for item in active
        ):
            return
        raise ValueError(
            f"Active player-correction limit ({ACTIVE_PLAYER_CORRECTION_LIMIT}) reached; "
            "wait for absorption or edit an existing target"
        )

    def require_confirm_capacity(self, target_slot: str) -> None:
        active = self.active_corrections()
        if any(item.get("target_slot") == target_slot for item in active):
            return
        if len(active) >= ACTIVE_PLAYER_CORRECTION_LIMIT:
            raise ValueError(f"Active player-correction limit ({ACTIVE_PLAYER_CORRECTION_LIMIT}) reached")

    @staticmethod
    def match_active_slot(instruction: str, active: list[dict[str, Any]]) -> str | None:
        folded = story_fact_fingerprint(instruction)
        for item in reversed(active):
            slot = str(item.get("target_slot") or "")
            if slot and slot.casefold() in instruction.casefold():
                return slot
            for key in ("before", "after", "target_id"):
                value = story_fact_fingerprint(str(item.get(key) or ""))
                if value and len(value) >= 8 and value in folded:
                    return slot
        return None

    def correction_candidates(
        self,
        instruction: str,
        *,
        target_hint: str | None,
        include_all: bool = False,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        snapshot = self.store.effective_rp_story_memory()
        if snapshot is not None:
            memory = normalize_sectioned_story_memory(
                snapshot.get("memory"),
                self.settings.rp_story_memory_max_chars,
            )
            for field in STORY_LIST_FIELDS:
                for item in memory.get(field) or []:
                    if not isinstance(item, dict) or item.get("status") != "active":
                        continue
                    fact_id = str(item.get("fact_id") or "")
                    text = str(item.get("text") or "").strip()
                    if not fact_id or not text:
                        continue
                    candidates.append(
                        {
                            "target_kind": "memory",
                            "target_id": fact_id,
                            "target_slot": f"memory:{field}:{fact_id}",
                            "target_turn_id": max(
                                [int(value) for value in item.get("source_turn_ids") or [] if int(value) >= 0],
                                default=0,
                            ),
                            "field": field,
                            "section_key": FIELD_TO_SECTION[field],
                            "before": text,
                            "allowed_actions": ["replace", "retract"],
                        }
                    )
        raw_turns = eligible_rp_turns(
            self.store.turns_for_memory(include_noncanonical_fallback=False)
        )[-self.settings.effective_rp_raw_history_window_turns :]
        for turn in reversed(raw_turns):
            candidates.append(
                {
                    "target_kind": "raw",
                    "target_id": str(int(turn["id"])),
                    "target_slot": f"raw:{int(turn['id'])}",
                    "target_turn_id": int(turn["id"]),
                    "player": str(turn.get("player_message") or ""),
                    "narrator": str(turn.get("narrative_response") or ""),
                    "allowed_fields": list(FIELD_TO_SECTION),
                    "allowed_actions": ["replace"],
                }
            )
        state = self.store.get_state()
        for item in state.get("world_constraints") or []:
            if not isinstance(item, dict) or item.get("kind") != "absolute":
                continue
            rule_id = str(item.get("id") or "")
            text = str(item.get("text") or "").strip()
            if not rule_id or not text:
                continue
            candidates.append(
                {
                    "target_kind": "absolute_rule",
                    "target_id": rule_id,
                    "target_slot": f"rule:{rule_id}",
                    "before": text,
                    "forbidden_claims": [str(value) for value in item.get("forbidden_claims") or []],
                    "allowed_actions": ["replace"],
                }
            )
        if target_hint:
            return [
                item
                for item in candidates
                if item["target_slot"] == target_hint
                or target_hint.startswith(f"{item['target_slot']}:")
            ]
        if include_all:
            return candidates
        words = set(re.findall(r"[\w-]{3,}", instruction.casefold(), flags=re.UNICODE))
        for index, item in enumerate(candidates):
            searchable = " ".join(
                str(item.get(key) or "")
                for key in ("target_id", "before", "player", "narrator")
            ).casefold()
            item["_score"] = sum(1 for word in words if word in searchable) * 100 - index
        return sorted(candidates, key=lambda item: int(item.get("_score") or 0), reverse=True)

    def intent_payload(self, content: str) -> dict[str, Any]:
        system = (
            "Определи маршрут реплики в ролевой игре. correction — прямое внефикшенное "
            "исправление уже сказанного факта или правила; scene — действие либо реплика персонажа "
            "внутри сцены; uncertain — если контекста недостаточно. Фраза персонажа без явного "
            "опровержения относится к scene. Верни только JSON с label и target."
        )
        recent = eligible_rp_turns(
            self.store.turns_for_memory(include_noncanonical_fallback=False)
        )[-1:]
        recent_text = ""
        if recent:
            recent_text = str(recent[-1].get("narrative_response") or "")[-240:]
        current = content.strip()
        while True:
            payload = {
                "model": self.settings.local_llm_model_alias,
                "stream": False,
                "temperature": 0,
                "max_tokens": GM_INTENT_OUTPUT_MAX_TOKENS,
                "response_format": self.intent_response_format(),
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"recent_scene": recent_text, "message": current},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ],
            }
            if len(service_prompt_text(payload)) <= GM_INTENT_INPUT_MAX_CHARS:
                return payload
            if recent_text:
                recent_text = recent_text[: max(len(recent_text) - 80, 0)]
                continue
            if len(current) <= 200:
                raise ValueError("gm_intent prompt cannot fit its input contract")
            current = current[: len(current) - 100]

    def patch_payload(
        self,
        instruction: str,
        candidates: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        system = (
            "Сопоставь исправление игрока ровно с одной существующей целью. Нельзя добавлять новый "
            "факт или правило. memory допускает replace/retract; raw и absolute_rule — только replace. "
            "Для raw выбери конкретное утверждение before длиной до 600 символов и поле памяти. "
            "Для absolute_rule верни полный новый список forbidden_claims. Верни только JSON с полями "
            "target_kind,target_id,field,action,before,after,forbidden_claims."
        )
        included: list[dict[str, Any]] = []
        for candidate in candidates:
            clean = {key: value for key, value in candidate.items() if not key.startswith("_")}
            trial = [*included, clean]
            payload = {
                "model": self.settings.local_llm_model_alias,
                "stream": False,
                "temperature": 0,
                "max_tokens": GM_PATCH_OUTPUT_MAX_TOKENS,
                "response_format": self.patch_response_format(),
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {"instruction": instruction, "candidates": trial},
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                ],
            }
            if len(service_prompt_text(payload)) > GM_PATCH_INPUT_MAX_CHARS:
                continue
            included = trial
            if len(included) >= 8:
                break
        if not included:
            raise ValueError("No complete correction target fits the 4000-character draft contract")
        payload["messages"][-1]["content"] = json.dumps(
            {"instruction": instruction, "candidates": included},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return payload, included

    def normalize_patch_draft(
        self,
        decoded: Any,
        candidates: list[dict[str, Any]],
        proposal_id: str,
    ) -> PartyGMPatchDraft:
        expected = {
            "target_kind",
            "target_id",
            "field",
            "action",
            "before",
            "after",
            "forbidden_claims",
        }
        if not isinstance(decoded, dict) or set(decoded) != expected:
            raise ValueError("gm_patch_draft returned an invalid object shape")
        candidate = next(
            (
                item
                for item in candidates
                if item.get("target_kind") == decoded.get("target_kind")
                and str(item.get("target_id")) == str(decoded.get("target_id"))
            ),
            None,
        )
        if candidate is None:
            raise ValueError("gm_patch_draft selected a target outside the supplied catalog")
        self.validate_proposal_against_candidate(decoded, candidate)
        field = str(decoded.get("field") or "") or candidate.get("field")
        return PartyGMPatchDraft.model_validate(
            {
                "schema_version": GM_PATCH_DRAFT_SCHEMA,
                "proposal_id": proposal_id,
                "target_kind": candidate["target_kind"],
                "target_id": candidate["target_id"],
                "target_slot": (
                    f"raw:{candidate['target_turn_id']}:{hashlib.sha256(story_fact_fingerprint(str(decoded['before'])).encode('utf-8')).hexdigest()[:12]}"
                    if candidate["target_kind"] == "raw"
                    else candidate["target_slot"]
                ),
                "target_turn_id": candidate.get("target_turn_id"),
                "field": field or None,
                "section_key": FIELD_TO_SECTION.get(field),
                "action": decoded["action"],
                "before": decoded["before"],
                "after": decoded.get("after"),
                "forbidden_claims": decoded.get("forbidden_claims") or [],
                "base_state_version": self.store.current_version() or 0,
                "base_snapshot_id": (
                    int(self.store.effective_rp_story_memory()["id"])
                    if self.store.effective_rp_story_memory()
                    else None
                ),
            }
        )

    def validate_proposal_against_candidate(
        self,
        decoded: dict[str, Any],
        candidate: dict[str, Any],
    ) -> None:
        target_kind = str(candidate["target_kind"])
        action = str(decoded.get("action") or "")
        before = str(decoded.get("before") or "").strip()
        after = decoded.get("after")
        after = str(after).strip() if after is not None else None
        if not before or len(before) > PLAYER_CORRECTION_MAX_CHARS:
            raise ValueError("GM correction before text is empty or too long")
        if action not in candidate["allowed_actions"]:
            raise ValueError("GM correction action is outside the target contract")
        if action == "replace":
            if not after or len(after) > PLAYER_CORRECTION_MAX_CHARS:
                raise ValueError("GM correction after text is empty or too long")
            if story_fact_fingerprint(after) == story_fact_fingerprint(before):
                raise ValueError("GM correction does not change the target")
        elif after is not None:
            raise ValueError("Retraction cannot contain replacement text")
        if target_kind in {"memory", "absolute_rule"}:
            if before != str(candidate.get("before") or "").strip():
                raise ValueError("GM patch before value does not match its target")
            if target_kind == "memory" and decoded.get("field") != candidate.get("field"):
                raise ValueError("GM patch field does not match its memory target")
            if target_kind == "absolute_rule" and decoded.get("field") is not None:
                raise ValueError("Absolute-rule correction cannot select a story-memory field")
        elif target_kind == "raw":
            haystack = story_fact_fingerprint(
                f"{candidate.get('player') or ''}\n{candidate.get('narrator') or ''}"
            )
            if story_fact_fingerprint(before) not in haystack:
                raise ValueError("GM patch before value is not present in the target RAW turn")
            field = str(decoded.get("field") or "")
            if field not in FIELD_TO_SECTION:
                raise ValueError("RAW correction selected an unsupported story-memory field")
        forbidden_claims = decoded.get("forbidden_claims")
        if not isinstance(forbidden_claims, list) or any(
            not isinstance(item, str) or not item.strip() or len(item.strip()) > 160
            for item in forbidden_claims
        ):
            raise ValueError("GM patch forbidden_claims is invalid")

    @staticmethod
    def mock_intent(content: str) -> dict[str, Any]:
        folded = content.casefold()
        markers = ("а не", "не так", "исправ", "ошиб", "на самом деле", "мастер")
        label = "correction" if any(marker in folded for marker in markers) else "scene"
        return {"label": label, "target": content.strip()[:160] if label == "correction" else None}

    @staticmethod
    def mock_patch(instruction: str, candidate: dict[str, Any]) -> dict[str, Any]:
        target_kind = str(candidate["target_kind"])
        field = candidate.get("field")
        retract = target_kind == "memory" and "отозвать" in instruction.casefold()
        if target_kind == "raw":
            field = "canon"
            before = str(candidate.get("narrator") or candidate.get("player") or "")[:600].strip()
        else:
            before = str(candidate.get("before") or "")
        return {
            "target_kind": target_kind,
            "target_id": candidate["target_id"],
            "field": field,
            "action": "retract" if retract else "replace",
            "before": before,
            "after": None if retract else instruction,
            "forbidden_claims": candidate.get("forbidden_claims") or [],
        }

    @staticmethod
    def finish_reason(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return ""
        return str(choices[0].get("finish_reason") or "")

    @staticmethod
    def intent_response_format() -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "gm_intent",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", "target"],
                    "properties": {
                        "label": {"type": "string", "enum": ["scene", "correction", "uncertain"]},
                        "target": {"type": ["string", "null"]},
                    },
                },
            },
        }

    @staticmethod
    def patch_response_format() -> dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "gm_patch_draft",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "target_kind",
                        "target_id",
                        "field",
                        "action",
                        "before",
                        "after",
                        "forbidden_claims",
                    ],
                    "properties": {
                        "target_kind": {"type": "string", "enum": ["memory", "raw", "absolute_rule"]},
                        "target_id": {"type": "string"},
                        "field": {"type": ["string", "null"]},
                        "action": {"type": "string", "enum": ["replace", "retract"]},
                        "before": {"type": "string"},
                        "after": {"type": ["string", "null"]},
                        "forbidden_claims": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        }
