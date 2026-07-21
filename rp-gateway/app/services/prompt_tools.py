"""Prompt preview helpers for party-scoped Light GUI debugging."""

from __future__ import annotations

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

    def preview(self, content: str) -> dict[str, Any]:
        state = self.store.get_state()
        latest = content.strip() or "[следующий ход игрока]"
        intent = self.intent_parser.parse(latest)
        outcome, patch = self.rule_engine.resolve(state, intent, "prompt-preview", roll=10)
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
        total_prompt_tokens = sum(block["estimated_tokens"] for block in blocks)
        return {
            "input": latest,
            "model": self.settings.narrative_model,
            "dry_run": True,
            "mutation": "none",
            "roll": 10,
            "intent": intent.model_dump(mode="json"),
            "outcome": outcome.model_dump(mode="json"),
            "candidate_state_meta": candidate_state.get("meta", {}),
            "messages": messages,
            "blocks": blocks,
            "estimated_prompt_tokens": total_prompt_tokens,
            "estimated_prompt_chars": sum(len(block["content"]) for block in blocks),
        }

    def chat_request(self, latest: str) -> ChatCompletionRequest:
        messages: list[ChatMessage] = []
        for turn in self.store.turn_history(limit=max(self.settings.party_raw_turn_limit, 0)):
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
