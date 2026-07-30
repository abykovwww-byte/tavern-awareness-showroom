"""Natural-language world state instruction drafting."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from typing import Any

import httpx

from app.core.config import Settings
from app.services.provider_auth import outbound_headers
from app.core.json_patch import PatchError
from app.models.schemas import PatchOperation, StatePatch, WorldInstructionDraft
from app.services.narrative import response_text
from app.services.service_models import service_model_settings
from app.services.state_store import StateStore


logger = logging.getLogger(__name__)


ALLOWED_TOP_LEVEL = {
    "player",
    "characters",
    "factions",
    "locations",
    "resources",
    "relationships",
    "active_threads",
    "completed_threads",
    "world_constraints",
    "timeline",
    "uncertain_facts",
}


class WorldInstructor:
    def __init__(self, settings: Settings, store: StateStore):
        self.settings = settings
        self.store = store

    def is_world_command(self, text: str) -> bool:
        return text.strip().lower().startswith("/world")

    async def handle_chat_command(
        self,
        text: str,
        authorization: str | None,
        model: str,
        request_id: str,
    ) -> dict[str, Any]:
        body = text.strip()[len("/world") :].strip()
        if not body or body.lower() in {"help", "?"}:
            return self.chat_response(self.help_text(), model)

        verb, _, rest = body.partition(" ")
        verb = verb.lower()
        if verb in {"apply", "confirm"}:
            proposal_id = rest.strip() or "latest"
            state = self.store.apply_pending_patch(proposal_id, reason=f"world_apply:{request_id}")
            self.store.audit("world_apply", {"proposal_id": proposal_id, "state_version": state["meta"]["state_version"]}, request_id)
            return self.chat_response(
                f"World patch applied: {state['last_turn']['state_patch_id']}\nState version: {state['meta']['state_version']}",
                model,
            )
        if verb in {"discard", "cancel"}:
            proposal_id = rest.strip() or "latest"
            discarded = self.store.discard_pending_patch(proposal_id)
            self.store.audit("world_discard", {"proposal_id": discarded}, request_id)
            return self.chat_response(f"World proposal discarded: {discarded}", model)
        if verb in {"rollback", "undo"}:
            state = self.store.rollback()
            self.store.audit("world_rollback", {"state_version": state["meta"]["state_version"]}, request_id)
            return self.chat_response(f"World state rolled back.\nState version: {state['meta']['state_version']}", model)
        if verb in {"show", "state", "status"}:
            return self.chat_response(self.state_summary(self.store.get_state()), model)
        if verb == "draft":
            body = rest.strip()

        try:
            draft = await self.draft_instruction(body, authorization)
            self.store.create_patch_proposal(draft.patch)
            self.store.audit(
                "world_proposal",
                {
                    "proposal_id": draft.proposal_id,
                    "changes": draft.changes,
                    "warnings": draft.warnings,
                },
                request_id,
            )
        except (PatchError, ValueError, RuntimeError) as exc:
            message = (
                "I did not change the world state.\n"
                f"Reason: {exc}\n\n"
                "Try a smaller instruction, for example:\n"
                "/world Remember: guard Varn now suspects the player."
            )
            return self.chat_response(message, model)

        return self.chat_response(self.preview_text(draft), model)

    async def draft_instruction(self, instruction: str, authorization: str | None, use_llm: bool = True) -> WorldInstructionDraft:
        instruction = instruction.strip()
        if not instruction:
            raise ValueError("world instruction is empty")
        state = self.store.get_state()
        proposal_id = f"world-{uuid.uuid4().hex[:12]}"
        if not use_llm or service_model_settings(self.settings).nvidia_api_base.startswith("mock://"):
            draft = self.mock_draft(state, instruction, proposal_id)
        else:
            try:
                draft = await self.llm_draft(state, instruction, proposal_id, authorization)
            except PermissionError:
                raise
            except Exception as exc:  # noqa: BLE001 - safe fallback keeps the UI usable
                draft = self.mock_draft(state, instruction, proposal_id)
                draft.warnings.append(f"LLM draft failed; used conservative fallback: {type(exc).__name__}")
        self.validate_draft(draft)
        self.store.preview_patch(draft.patch)
        return draft

    async def llm_draft(
        self,
        state: dict[str, Any],
        instruction: str,
        proposal_id: str,
        inbound_authorization: str | None,
    ) -> WorldInstructionDraft:
        runtime = service_model_settings(self.settings)
        headers = outbound_headers(runtime, None)

        turn = int(state.get("meta", {}).get("turn", 0)) + 1
        payload = {
            "model": runtime.intent_model,
            "temperature": 0,
            "max_tokens": 1400,
            "stream": False,
            "messages": [
                {
                    "role": "system",
                    "content": self.draft_prompt(),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "proposal_id": proposal_id,
                            "turn": turn,
                            "instruction": instruction,
                            "state_excerpt": self.state_excerpt(state),
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        timeout = httpx.Timeout(runtime.model_attempt_timeout_seconds, connect=15.0)
        attempts = self.model_attempts(runtime.intent_model, runtime)
        async with httpx.AsyncClient(timeout=timeout) as client:
            for index, model in enumerate(attempts):
                payload["model"] = model
                started = time.perf_counter()
                logger.info(
                    "world_llm_attempt_start proposal_id=%s model=%s attempt=%s/%s timeout_seconds=%s",
                    proposal_id,
                    model,
                    index + 1,
                    len(attempts),
                    runtime.model_attempt_timeout_seconds,
                )
                try:
                    response = await client.post(
                        f"{runtime.nvidia_api_base.rstrip('/')}/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                except httpx.TimeoutException:
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    logger.warning(
                        "world_llm_attempt_timeout proposal_id=%s model=%s attempt=%s/%s elapsed_ms=%s fallback=%s",
                        proposal_id,
                        model,
                        index + 1,
                        len(attempts),
                        elapsed_ms,
                        index < len(attempts) - 1,
                    )
                    if index < len(attempts) - 1:
                        continue
                    raise
                if response.status_code == 429:
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    logger.warning(
                        "world_llm_attempt_rate_limited proposal_id=%s model=%s elapsed_ms=%s",
                        proposal_id,
                        model,
                        elapsed_ms,
                    )
                    raise RuntimeError(f"{runtime.llm_provider} API returned 429 rate limit")
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError:
                    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                    logger.warning(
                        "world_llm_attempt_http_error proposal_id=%s model=%s status=%s elapsed_ms=%s fallback=%s",
                        proposal_id,
                        model,
                        response.status_code,
                        elapsed_ms,
                        index < len(attempts) - 1,
                    )
                    if index < len(attempts) - 1 and response.status_code in {400, 404, 408, 500, 502, 503, 504}:
                        continue
                    raise
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                logger.info(
                    "world_llm_attempt_success proposal_id=%s model=%s status=%s elapsed_ms=%s fallback_used=%s",
                    proposal_id,
                    model,
                    response.status_code,
                    elapsed_ms,
                    index > 0 or model != runtime.intent_model,
                )
                break
        data = self.extract_json(response_text(response.json()))
        patch_data = data.get("patch", {})
        operations = patch_data.get("patch") or data.get("operations") or []
        patch = StatePatch(
            turn=int(patch_data.get("turn", turn)),
            check_id=proposal_id,
            source="world-instructor",
            patch=[PatchOperation.model_validate(operation) for operation in operations],
            uncertain_facts=patch_data.get("uncertain_facts", data.get("uncertain_facts", [])),
            contradictions=patch_data.get("contradictions", data.get("contradictions", [])),
        )
        patch = self.ensure_timeline_patch(patch, instruction, turn)
        return WorldInstructionDraft(
            proposal_id=proposal_id,
            instruction=instruction,
            summary=str(data.get("summary") or instruction[:180]),
            changes=[str(item) for item in data.get("changes", [])],
            warnings=[str(item) for item in data.get("warnings", [])],
            patch=patch,
        )

    def model_attempts(self, primary_model: str, runtime: Settings | None = None) -> list[str]:
        runtime = runtime or self.settings
        disabled = set(runtime.nvidia_disabled_models)
        candidates = [primary_model, *runtime.nvidia_fallback_models]
        attempts: list[str] = []
        for model in candidates:
            if not model or model in disabled or model in attempts:
                continue
            attempts.append(model)
        return attempts or [primary_model]

    def draft_prompt(self) -> str:
        return (
            "You convert a human tabletop-RP world instruction into safe JSON Patch. "
            "Return only a JSON object. Do not include markdown. The response shape is: "
            '{"summary":"short human summary","changes":["human-visible changes"],'
            '"warnings":[],"patch":{"patch":[{"op":"add|replace|remove","path":"/...","value":{},'
            '"reason":"why this is safe","turn":1}],"uncertain_facts":[],"contradictions":[]}}. '
            "Allowed top-level paths: player, characters, factions, locations, resources, relationships, "
            "active_threads, completed_threads, world_constraints, timeline, uncertain_facts. "
            "Never edit meta or last_turn directly. Use add with a full object when creating new records. "
            "Do not assert outcomes that contradict hard constraints; put uncertainty in uncertain_facts."
        )

    def mock_draft(self, state: dict[str, Any], instruction: str, proposal_id: str) -> WorldInstructionDraft:
        turn = int(state.get("meta", {}).get("turn", 0)) + 1
        clean = " ".join(instruction.split())[:500]
        fact = {
            "id": stable_id("fact", clean),
            "text": clean,
            "turn": turn,
            "confidence": "confirmed",
            "source": "world_instruction",
        }
        operations = [
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={"turn": turn, "event": clean, "confirmed": True, "participants": ["player"]},
                reason="Records confirmed world instruction from the GM UI.",
                turn=turn,
            )
        ]
        changes = [f"Timeline: {clean}"]
        warnings: list[str] = []
        entity = self.detect_entity(state, clean)
        if entity:
            char_path = f"/characters/{pointer_escape(entity)}"
            relationship_key = f"player_{entity}"
            if entity not in state.get("characters", {}):
                operations.append(
                    PatchOperation(
                        op="add",
                        path=char_path,
                        value={
                            "status": "alive",
                            "location": state.get("player", {}).get("location", "unknown"),
                            "attitude_to_player": "updated by world instruction",
                            "trust": 0,
                            "fear": 0,
                            "loyalty": "unknown",
                            "current_goal": "unknown",
                            "knowledge": [fact],
                            "secrets": [],
                            "obligations": [],
                            "hard_constraints": [],
                            "last_confirmed_update": turn,
                        },
                        reason="Creates a minimal character record from a confirmed world instruction.",
                        turn=turn,
                    )
                )
                changes.append(f"Character: create {entity}")
            else:
                operations.append(
                    PatchOperation(
                        op="add",
                        path=f"{char_path}/knowledge/-",
                        value=fact,
                        reason="Adds confirmed world instruction to character knowledge.",
                        turn=turn,
                    )
                )
                changes.append(f"Character: add knowledge to {entity}")

            suspicion = 4 if contains_any(clean, ["suspect", "suspicious", "подоз"]) else None
            fear = 3 if contains_any(clean, ["fear", "afraid", "боится", "боит"]) else None
            if suspicion is not None or fear is not None:
                relationship = state.get("relationships", {}).get(relationship_key)
                if relationship:
                    if suspicion is not None:
                        operations.append(
                            PatchOperation(
                                op="replace",
                                path=f"/relationships/{pointer_escape(relationship_key)}/suspicion",
                                value=suspicion,
                                reason="Applies bounded suspicion from confirmed world instruction.",
                                turn=turn,
                            )
                        )
                        changes.append(f"Relationship: {relationship_key}.suspicion -> {suspicion}")
                    operations.append(
                        PatchOperation(
                            op="add",
                            path=f"/relationships/{pointer_escape(relationship_key)}/notes/-",
                            value=clean,
                            reason="Adds relationship note from confirmed world instruction.",
                            turn=turn,
                        )
                    )
                else:
                    operations.append(
                        PatchOperation(
                            op="add",
                            path=f"/relationships/{pointer_escape(relationship_key)}",
                            value={
                                "from": "player",
                                "to": entity,
                                "trust": 0,
                                "suspicion": suspicion or 0,
                                "notes": [clean],
                            },
                            reason="Creates a bounded relationship record from confirmed world instruction.",
                            turn=turn,
                        )
                    )
                    changes.append(f"Relationship: create {relationship_key}")
                if fear is not None and entity in state.get("characters", {}):
                    operations.append(
                        PatchOperation(
                            op="replace",
                            path=f"{char_path}/fear",
                            value=fear,
                            reason="Applies bounded fear from confirmed world instruction.",
                            turn=turn,
                        )
                    )
                    changes.append(f"Character: {entity}.fear -> {fear}")

        if contains_any(clean, ["cannot", "must not", "never", "нельзя", "не может"]):
            operations.append(
                PatchOperation(
                    op="add",
                    path="/world_constraints/-",
                    value={"id": stable_id("constraint", clean), "text": clean, "scope": "global", "turn": turn},
                    reason="Stores hard world constraint from confirmed world instruction.",
                    turn=turn,
                )
            )
            changes.append("World constraint: added")

        patch = StatePatch(turn=turn, check_id=proposal_id, source="world-instructor", patch=operations)
        return WorldInstructionDraft(
            proposal_id=proposal_id,
            instruction=instruction,
            summary=clean,
            changes=changes,
            warnings=warnings,
            patch=patch,
        )

    def ensure_timeline_patch(self, patch: StatePatch, instruction: str, turn: int) -> StatePatch:
        if any(operation.path.startswith("/timeline/") for operation in patch.patch):
            return patch
        patch.patch.insert(
            0,
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={"turn": turn, "event": instruction[:500], "confirmed": True, "participants": ["player"]},
                reason="Records confirmed world instruction from the GM UI.",
                turn=turn,
            ),
        )
        return patch

    def validate_draft(self, draft: WorldInstructionDraft) -> None:
        if draft.patch.check_id != draft.proposal_id:
            raise ValueError("draft proposal_id must match patch.check_id")
        for operation in draft.patch.patch:
            self.validate_operation(operation)

    def validate_operation(self, operation: PatchOperation) -> None:
        path = operation.path
        if not path.startswith("/") or path == "/":
            raise ValueError(f"unsafe patch path: {path}")
        top = path.strip("/").split("/", 1)[0]
        if top not in ALLOWED_TOP_LEVEL:
            raise ValueError(f"patch path is outside world state: {path}")
        if path.startswith("/meta") or path.startswith("/last_turn"):
            raise ValueError(f"patch path is not allowed: {path}")
        if not operation.reason.strip():
            raise ValueError(f"patch operation needs a reason: {path}")

    def extract_json(self, text: str) -> dict[str, Any]:
        clean = text.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?", "", clean).strip()
            clean = re.sub(r"```$", "", clean).strip()
        try:
            data = json.loads(clean)
        except json.JSONDecodeError:
            start = clean.find("{")
            end = clean.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise ValueError("LLM did not return JSON")
            data = json.loads(clean[start : end + 1])
        if not isinstance(data, dict):
            raise ValueError("LLM JSON response must be an object")
        return data

    def preview_text(self, draft: WorldInstructionDraft) -> str:
        lines = [
            f"World proposal ready: {draft.proposal_id}",
            "",
            draft.summary,
            "",
            "Planned changes:",
        ]
        lines.extend(f"- {item}" for item in (draft.changes or self.describe_patch(draft.patch)))
        if draft.warnings:
            lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {item}" for item in draft.warnings)
        lines.extend(
            [
                "",
                "Not applied yet.",
                f"Apply with: /world apply {draft.proposal_id}",
                f"Discard with: /world discard {draft.proposal_id}",
            ]
        )
        return "\n".join(lines)

    def describe_patch(self, patch: StatePatch) -> list[str]:
        return [f"{operation.op} {operation.path}" for operation in patch.patch]

    def state_summary(self, state: dict[str, Any]) -> str:
        meta = state.get("meta", {})
        relationships = state.get("relationships", {})
        resources = state.get("player", {}).get("resources", {})
        threads = state.get("active_threads", [])
        return "\n".join(
            [
                f"World state version: {meta.get('state_version')}",
                f"Turn: {meta.get('turn')}",
                f"Player location: {state.get('player', {}).get('location')}",
                f"Player resources: {json.dumps(resources, ensure_ascii=False)}",
                f"Relationships tracked: {len(relationships)}",
                f"Active threads: {len(threads)}",
            ]
        )

    def state_excerpt(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "meta": state.get("meta", {}),
            "player": state.get("player", {}),
            "characters": state.get("characters", {}),
            "relationships": state.get("relationships", {}),
            "resources": state.get("resources", {}),
            "world_constraints": state.get("world_constraints", []),
            "active_threads": state.get("active_threads", []),
            "timeline_tail": state.get("timeline", [])[-10:],
        }

    def detect_entity(self, state: dict[str, Any], text: str) -> str | None:
        lowered = text.lower()
        for key in state.get("characters", {}):
            if str(key).lower() in lowered:
                return str(key)
        patterns = [
            r"(?:guard|npc|character)\s+([A-Za-z0-9_\-]+)",
            r"(?:стражник|персонаж|нпс)\s+([A-Za-zА-Яа-яЁё0-9_\-]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return slug(match.group(1))
        return None

    def help_text(self) -> str:
        return "\n".join(
            [
                "World command help:",
                '/world Remember: guard Varn now suspects the player.',
                "/world apply latest",
                "/world discard latest",
                "/world rollback",
                "/world show",
            ]
        )

    def chat_response(self, content: str, model: str) -> dict[str, Any]:
        return {
            "id": f"chatcmpl-world-{uuid.uuid4().hex[:16]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


def contains_any(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def slug(value: str) -> str:
    clean = re.sub(r"[^\w\-]+", "_", value.strip().lower(), flags=re.UNICODE).strip("_")
    return clean or "unknown"


def stable_id(prefix: str, text: str) -> str:
    return f"{prefix}_{hashlib.sha256(text.encode('utf-8')).hexdigest()[:12]}"
