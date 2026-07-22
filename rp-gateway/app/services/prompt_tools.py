"""Prompt preview helpers for party-scoped Light GUI debugging."""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.json_patch import PatchError, apply_patch
from app.models.schemas import ChatCompletionRequest, ChatMessage
from app.services.context_estimator import estimate_tokens
from app.services.intent_parser import IntentParser
from app.services.narrative import NarrativeClient
from app.services.rule_engine import RuleEngine
from app.services.state_store import StateStore


class PromptInspector:
    def __init__(self, settings: Any, store: StateStore):
        self.settings = settings
        self.store = store
        self.intent_parser = IntentParser()
        self.rule_engine = RuleEngine()

    def preview(self, content: str, source: str = "current") -> dict[str, Any]:
        if source == "last":
            return self.preview_last(content)
        return self.preview_current(content)

    def preview_last(self, fallback_content: str = "") -> dict[str, Any]:
        latest_turn = self.store.latest_turn(include_prompt=True)
        if latest_turn and latest_turn.get("prompt_json"):
            try:
                messages = json.loads(str(latest_turn["prompt_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                messages = None
            if isinstance(messages, list) and all(isinstance(message, dict) for message in messages):
                blocks = self.blocks(messages)
                return self.payload(
                    latest_turn.get("player_message") or "",
                    messages,
                    blocks,
                    source="recorded_last_turn",
                    dry_run=False,
                    turn=latest_turn,
                )
        if latest_turn:
            reconstructed = self.reconstruct_last_prompt(latest_turn)
            reconstructed["source"] = "reconstructed_last_turn"
            reconstructed["dry_run"] = True
            return reconstructed
        current = self.preview_current(fallback_content)
        current["source"] = "no_previous_turn"
        return current

    def preview_current(self, content: str) -> dict[str, Any]:
        state = self.store.get_state()
        latest = content.strip() or "[следующий ход игрока]"
        intent = self.intent_parser.parse(latest)
        outcome, patch = self.rule_engine.resolve(
            state,
            intent,
            "prompt-preview",
            roll=10,
            campaign_id=self.settings.campaign_id,
        )
        candidate_state = self.preview_state(state, patch)
        request = self.chat_request(latest)
        memory_summary = self.store.latest_memory_summary()
        messages = NarrativeClient(self.settings).narrative_messages(
            request,
            candidate_state,
            outcome,
            repair_instruction=None,
            memory_summary=memory_summary,
        )
        blocks = self.blocks(messages)
        payload = self.payload(latest, messages, blocks, source="current_dry_run", dry_run=True)
        payload.update(
            {
                "mutation": "none",
                "roll": 10,
                "intent": intent.model_dump(mode="json"),
                "outcome": outcome.model_dump(mode="json"),
                "candidate_state_meta": candidate_state.get("meta", {}),
            }
        )
        return payload

    def reconstruct_last_prompt(self, latest_turn: dict[str, Any]) -> dict[str, Any]:
        state = self.store.get_state()
        latest = str(latest_turn.get("player_message") or "")
        intent = self.intent_parser.parse(latest)
        outcome, _patch = self.rule_engine.resolve(
            state,
            intent,
            str(latest_turn.get("request_id") or "prompt-preview"),
            roll=10,
            campaign_id=self.settings.campaign_id,
        )
        request = self.chat_request(latest, before_turn_id=int(latest_turn["id"]))
        memory_summary = self.store.latest_memory_summary()
        messages = NarrativeClient(self.settings).narrative_messages(
            request,
            state,
            outcome,
            repair_instruction=None,
            memory_summary=memory_summary,
        )
        blocks = self.blocks(messages)
        payload = self.payload(latest, messages, blocks, source="reconstructed_last_turn", dry_run=True, turn=latest_turn)
        payload.update({"intent": intent.model_dump(mode="json"), "outcome": outcome.model_dump(mode="json")})
        return payload

    def payload(
        self,
        latest: str,
        messages: list[dict[str, str]],
        blocks: list[dict[str, Any]],
        source: str,
        dry_run: bool,
        turn: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "input": latest,
            "model": self.settings.narrative_model,
            "source": source,
            "dry_run": dry_run,
            "mutation": "none",
            "turn": turn,
            "messages": messages,
            "blocks": blocks,
            "estimated_prompt_tokens": sum(block["estimated_tokens"] for block in blocks),
            "estimated_prompt_chars": sum(len(block["content"]) for block in blocks),
        }

    def chat_request(self, latest: str, before_turn_id: int | None = None) -> ChatCompletionRequest:
        messages: list[ChatMessage] = []
        if before_turn_id is None:
            turns = self.store.turn_history(limit=max(self.settings.party_raw_turn_limit, 0))
        else:
            turns = self.store.turns_before(before_turn_id, limit=max(self.settings.party_raw_turn_limit, 0))
        for turn in turns:
            messages.append(ChatMessage(role="user", content=turn["player_message"]))
            messages.append(ChatMessage(role="assistant", content=turn["narrative_response"]))
        messages.append(ChatMessage(role="user", content=latest))
        return ChatCompletionRequest(model=self.settings.narrative_model, messages=messages, stream=False)

    def preview_state(self, state: dict[str, Any], patch: Any) -> dict[str, Any]:
        operations = [operation.model_dump(exclude_none=True) for operation in patch.patch]
        try:
            candidate = apply_patch(state, operations)
        except PatchError:
            candidate = dict(state)
        current_version = int(state.get("meta", {}).get("state_version", 1))
        candidate.setdefault("meta", {})
        candidate["meta"]["state_version"] = current_version + 1
        candidate["meta"]["turn"] = max(int(candidate["meta"].get("turn", 0)) + 1, patch.turn)
        candidate["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        candidate.setdefault("last_turn", {})
        candidate["last_turn"]["turn"] = candidate["meta"]["turn"]
        candidate["last_turn"]["state_patch_id"] = patch.check_id or f"prompt-preview-v{current_version + 1}"
        return candidate

    def blocks(self, messages: list[dict[str, str]]) -> list[dict[str, Any]]:
        raw_items: list[dict[str, str]] = []
        blocks: list[dict[str, Any]] = []
        for index, message in enumerate(messages):
            role = message.get("role", "")
            content = message.get("content", "")
            if role != "system":
                raw_items.append({"role": role, "content": content})
                continue
            block_id, title = self.system_block_label(content, index)
            blocks.append(self.block(block_id, title, content, role=role))
        if raw_items:
            raw_content = "\n\n".join(f"{item['role']}: {item['content']}" for item in raw_items)
            raw_block = self.block("raw_turns", "Последние raw turns", raw_content, role="mixed")
            raw_block["messages"] = raw_items
            blocks.append(raw_block)
        return blocks

    def system_block_label(self, content: str, index: int) -> tuple[str, str]:
        if content.startswith("LONG_TERM_PARTY_MEMORY"):
            return "long_term_memory", "LONG_TERM_PARTY_MEMORY"
        if content.startswith("Relevant state summary:"):
            return "state_summary", "State summary"
        if "AUTHORITATIVE_OUTCOME" in content:
            return "authoritative_outcome", "AUTHORITATIVE_OUTCOME"
        if index == 0:
            return "system_rules", "System rules"
        return f"system_{index}", f"System block {index + 1}"

    def block(self, block_id: str, title: str, content: str, role: str) -> dict[str, Any]:
        return {
            "id": block_id,
            "title": title,
            "role": role,
            "content": content,
            "estimated_tokens": estimate_tokens(content),
            "estimated_chars": len(content),
        }
