"""Prompt preview helpers for party-scoped Light GUI debugging."""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.json_patch import PatchError, apply_patch
from app.models.schemas import ChatCompletionRequest, ChatMessage
from app.services.context_budget import split_turns_by_token_budget
from app.services.context_estimator import estimate_tokens, recorded_prompt_assembly
from app.services.intent_parser import IntentParser
from app.services.narrative import (
    NarrativeClient,
    PromptBudgetExceeded,
    archived_memory_retrieval_block,
    party_lore_cards_block,
    prompt_assembly_diagnostics,
    uncompacted_archive_fallback_block,
)
from app.services.rule_engine import RuleEngine
from app.services.rp_history import (
    eligible_rp_turns,
    raw_history_window,
    recent_rp_scan_text,
    removable_covered_history_units,
    rp_turn_messages,
    story_memory_safe_coverage,
)
from app.services.rp_story_memory import RPStoryMemoryUpdater
from app.services.rp_supervisor import RPSupervisorService
from app.services.relationship_attribution import normalized_aliases
from app.services.scene_state import (
    scene_state_boundary_block,
    unresolved_noncanonical_fallback_turns,
)
from app.services.state_store import StateStore
from app.services.world_clock import WorldClockService


class PromptInspector:
    def __init__(
        self,
        settings: Any,
        store: StateStore,
        relationship_model: dict[str, Any] | None = None,
        scene_contract: dict[str, Any] | None = None,
        world_clock_contract: dict[str, Any] | None = None,
        rp_supervisor_contract: dict[str, Any] | None = None,
    ):
        self.settings = settings
        self.store = store
        self.relationship_model = relationship_model
        self.scene_contract = scene_contract
        self.world_clock = (
            WorldClockService(settings, store, world_clock_contract)
            if settings.scenario_type == "rp"
            and settings.rp_contract_revision >= 10
            and world_clock_contract is not None
            else None
        )
        self.rp_supervisor = (
            RPSupervisorService(settings, store, rp_supervisor_contract)
            if settings.scenario_type == "rp" and rp_supervisor_contract is not None
            else None
        )
        self.intent_parser = IntentParser()
        self.rule_engine = RuleEngine()
        self.rp_story_memory = RPStoryMemoryUpdater(settings, store) if settings.scenario_type == "rp" else None

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
                prompt_assembly = (
                    recorded_prompt_assembly(self.store, latest_turn)
                    if self.settings.scenario_type == "rp"
                    and self.settings.rp_contract_revision >= 7
                    else None
                )
                messages = self.public_messages(messages)
                public_turn = dict(latest_turn)
                public_turn["prompt_json"] = json.dumps(messages, ensure_ascii=False)
                blocks = self.blocks(messages)
                return self.payload(
                    latest_turn.get("player_message") or "",
                    messages,
                    blocks,
                    source="recorded_last_turn",
                    dry_run=False,
                    turn=public_turn,
                    prompt_assembly=prompt_assembly,
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
        rp_no_checks = self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 1
        intent = self.intent_parser.parse(latest, mechanical=not rp_no_checks)
        outcome, patch = self.rule_engine.resolve(
            state,
            intent,
            "prompt-preview",
            roll=10,
            campaign_id=self.settings.campaign_id,
            scenario_type=self.settings.scenario_type,
            rp_contract_version=self.settings.rp_contract_version,
            rp_contract_revision=self.settings.rp_contract_revision,
            character_aliases=self.character_aliases(),
            authored_stable_affiliations=self.authored_stable_affiliations(),
        )
        candidate_state = self.preview_state(state, patch)
        request = self.chat_request(latest, outcome_target=outcome.target)
        revision_eight = self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 8
        memory_summary = (
            None
            if revision_eight
            else self.store.memory_for_prompt(self.settings.party_memory_prompt_max_chars)
        )
        rp_story_memory = self.rp_story_memory.prompt_snapshot() if self.rp_story_memory else None
        narrative = NarrativeClient(self.settings)
        token_budget = narrative.input_token_budget(request)
        revision_seven = self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 7
        prompt_diagnostics: dict[str, Any] = {}
        world_clock_projection = (
            self.world_clock.prompt_projection(candidate_state)
            if self.world_clock is not None
            else None
        )
        try:
            messages = narrative.narrative_messages(
                request,
                candidate_state,
                outcome,
                repair_instruction=None,
                memory_summary=memory_summary,
                rp_story_memory=rp_story_memory,
                world_events=(
                    str(world_clock_projection["block"])
                    if world_clock_projection is not None
                    else None
                ),
                supervisor_advisory=(
                    self.rp_supervisor.prompt_advisory()
                    if self.rp_supervisor is not None
                    else None
                ),
                diagnostics=prompt_diagnostics if revision_seven else None,
            )
        except PromptBudgetExceeded as exc:
            covered_through = (
                story_memory_safe_coverage(rp_story_memory)
                if self.settings.rp_contract_revision >= 8
                else int(rp_story_memory.get("to_turn_id") or 0)
                if rp_story_memory
                else 0
            )
            story_stats = self.rp_story_memory.stats() if self.rp_story_memory else {}
            story_diagnostics = self.story_memory_diagnostics(rp_story_memory, story_stats)
            story_diagnostics["hard_overflow"] = True
            story_diagnostics["operator_status"] = "overflow"
            return {
                "input": "[omitted: hard prompt overflow]",
                "model": self.settings.narrative_model,
                "source": "current_dry_run",
                "dry_run": True,
                "mutation": "none",
                "turn": None,
                "messages": [],
                "blocks": [],
                "estimated_prompt_tokens": exc.estimated_tokens,
                "estimated_prompt_chars": None,
                "hard_input_budget_tokens": exc.token_budget,
                "hard_budget_status": "over_budget",
                "hard_overflow": True,
                "error": {
                    "type": "PromptBudgetExceeded",
                    "message": str(exc),
                },
                "inspection": {
                    "memory_coverage_through_turn_id": covered_through or None,
                    "raw": {
                        "included_turn_ids": (
                            list(request._rp_raw_history_turn_ids)
                            if self.settings.rp_contract_revision >= 8
                            else turn_ids(self.turns_for_prompt(after_turn_id=covered_through))
                        ),
                        "excluded_turn_ids": [],
                        "excluded_reason": None,
                    },
                    "story_memory": story_diagnostics,
                },
            }
        blocks = self.blocks(messages)
        prompt_assembly = None
        if revision_seven:
            covered_through = int(
                request._rp_story_memory_covered_through_turn_id or 0
            )
            prompt_assembly = prompt_assembly_diagnostics(
                messages,
                story_memory_covered_through_turn_id=covered_through,
                raw_tail_turn_ids=prompt_diagnostics.get(
                    "raw_history_turn_ids",
                    turn_ids(self.turns_for_prompt(after_turn_id=covered_through)),
                ),
                omitted_blocks=prompt_diagnostics.get("omitted_blocks"),
                rp_contract_revision=self.settings.rp_contract_revision,
            )
        payload = self.payload(
            latest,
            messages,
            blocks,
            source="current_dry_run",
            dry_run=True,
            inspection=self.memory_inspection(
                latest,
                fitted_raw_turn_ids=(
                    prompt_diagnostics.get("raw_history_turn_ids")
                    if revision_eight
                    else None
                ),
            ),
            token_budget=token_budget,
            prompt_assembly=prompt_assembly,
        )
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
        rp_no_checks = self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 1
        intent = self.intent_parser.parse(latest, mechanical=not rp_no_checks)
        outcome, _patch = self.rule_engine.resolve(
            state,
            intent,
            str(latest_turn.get("request_id") or "prompt-preview"),
            roll=10,
            campaign_id=self.settings.campaign_id,
            scenario_type=self.settings.scenario_type,
            rp_contract_version=self.settings.rp_contract_version,
            rp_contract_revision=self.settings.rp_contract_revision,
            character_aliases=self.character_aliases(),
            authored_stable_affiliations=self.authored_stable_affiliations(),
        )
        request = self.chat_request(
            latest,
            before_turn_id=int(latest_turn["id"]),
            outcome_target=outcome.target,
        )
        memory_summary = (
            None
            if self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 8
            else self.store.memory_for_prompt(self.settings.party_memory_prompt_max_chars)
        )
        rp_story_memory = self.store.latest_rp_story_memory() if self.settings.scenario_type == "rp" else None
        world_clock_projection = (
            self.world_clock.prompt_projection(state)
            if self.world_clock is not None
            else None
        )
        messages = NarrativeClient(self.settings).narrative_messages(
            request,
            state,
            outcome,
            repair_instruction=None,
            memory_summary=memory_summary,
            rp_story_memory=rp_story_memory,
            world_events=(
                str(world_clock_projection["block"])
                if world_clock_projection is not None
                else None
            ),
            supervisor_advisory=(
                self.rp_supervisor.prompt_advisory()
                if self.rp_supervisor is not None
                else None
            ),
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
        inspection: dict[str, Any] | None = None,
        token_budget: int | None = None,
        prompt_assembly: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prompt_text = "\n".join(str(message.get("content") or "") for message in messages)
        actual_prompt_tokens = estimate_tokens(prompt_text)
        payload = {
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
            "inspection": inspection,
        }
        if prompt_assembly is not None:
            payload["prompt_assembly"] = prompt_assembly
        if token_budget is not None:
            payload.update(
                {
                    "hard_input_budget_tokens": token_budget,
                    "hard_budget_status": (
                        "within_budget"
                        if actual_prompt_tokens <= token_budget
                        else "over_budget"
                    ),
                    "hard_overflow": actual_prompt_tokens > token_budget,
                }
            )
        return payload

    def chat_request(
        self,
        latest: str,
        before_turn_id: int | None = None,
        outcome_target: str | None = None,
    ) -> ChatCompletionRequest:
        messages: list[ChatMessage] = []
        revision_seven = self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 7
        revision_eight = self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 8
        story_memory = self.store.effective_rp_story_memory() if revision_seven else None
        if before_turn_id is None:
            memory = story_memory if revision_seven else self.store.latest_memory_coverage()
            covered_through = (
                story_memory_safe_coverage(story_memory)
                if revision_eight
                else int(memory["to_turn_id"])
                if memory
                else 0
            )
            all_turns = self.turns_for_prompt()
            if revision_eight:
                turns = raw_history_window(
                    all_turns,
                    safe_coverage=covered_through,
                    window_turns=self.settings.effective_rp_raw_history_window_turns,
                )
            else:
                turns = [turn for turn in all_turns if int(turn["id"]) > covered_through]
            if revision_seven and not revision_eight:
                turns = list(
                    {
                        int(turn["id"]): turn
                        for turn in [
                            *[
                                item
                                for item in all_turns
                                if item.get("noncanonical_safe_fallback")
                            ],
                            *turns,
                        ]
                    }.values()
                )
                turns.sort(key=lambda turn: int(turn["id"]))
        else:
            covered_through = 0
            turns = self.turns_for_prompt(to_turn_id=before_turn_id - 1)
        if revision_seven and before_turn_id is None:
            overflow_turns = []
        else:
            overflow_turns, turns = split_turns_by_token_budget(
                turns,
                max(self.settings.effective_party_history_token_budget - estimate_tokens(latest), 0),
            )
        if before_turn_id is None:
            lore_query = latest
            if revision_eight:
                lore_query = recent_rp_scan_text(
                    self.store.turns_for_memory(include_noncanonical_fallback=False),
                    latest,
                )
                if str(outcome_target or "").strip():
                    lore_query = f"{lore_query}\n{str(outcome_target).strip()}"
            lore_block = party_lore_cards_block(
                self.store.lore_cards_for_prompt(
                    lore_query,
                    limit=self.settings.party_lore_card_prompt_limit,
                    max_chars=(
                        min(self.settings.party_lore_card_prompt_max_chars, 4_000)
                        if revision_eight
                        else self.settings.party_lore_card_prompt_max_chars
                    ),
                    title_keywords_only=revision_eight,
                    whole_match=revision_eight,
                ),
                max_chars=4_000 if revision_eight else None,
            )
            if lore_block:
                messages.append(ChatMessage(role="system", content=lore_block))
            fallback_block = uncompacted_archive_fallback_block(
                overflow_turns,
                self.settings.party_memory_fallback_max_chars,
            )
            if fallback_block:
                messages.append(ChatMessage(role="system", content=fallback_block))
        if revision_seven and not revision_eight:
            messages.insert(
                0,
                ChatMessage(
                    role="system",
                    content=scene_state_boundary_block(self.store.get_state()),
                ),
            )
        for turn in turns:
            rendered = (
                rp_turn_messages(turn)
                if revision_eight
                else [
                    ("user", str(turn["player_message"])),
                    ("assistant", str(turn["narrative_response"])),
                ]
            )
            messages.extend(ChatMessage(role=role, content=content) for role, content in rendered)
        if before_turn_id is None and self.settings.party_memory_retrieval_enabled and not revision_eight:
            retrieved = self.store.search_archived_turns(
                latest,
                through_turn_id=covered_through,
                limit=self.settings.party_memory_retrieval_limit,
            )
            retrieval_block = archived_memory_retrieval_block(retrieved, self.settings.party_memory_retrieval_max_chars)
            if retrieval_block:
                messages.append(ChatMessage(role="system", content=retrieval_block))
        messages.append(ChatMessage(role="user", content=latest))
        request = ChatCompletionRequest(model=self.settings.narrative_model, messages=messages, stream=False)
        request._latest_player_action = latest
        request._raw_transcript_chars = sum(
            len(str(turn.get("player_message") or "")) + len(str(turn.get("narrative_response") or ""))
            for turn in self.turns_for_prompt()
        )
        if revision_seven:
            request._rp_story_memory_snapshot_id = int(story_memory["id"]) if story_memory else None
            request._rp_story_memory_covered_through_turn_id = covered_through
        if revision_eight and before_turn_id is None:
            request._rp_raw_history_turn_ids = [int(turn["id"]) for turn in turns]
            request._rp_raw_history_removable_units = removable_covered_history_units(
                turns,
                safe_coverage=covered_through,
            )
        return request

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

    @staticmethod
    def public_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            dict(message)
            for message in messages
            if not (
                message.get("role") == "system"
                and str(message.get("content") or "").startswith(
                    ("RELATIONSHIP_PRESSURE", "RELATIONSHIP_EVENT_RESOLUTION")
                )
            )
        ]

    def system_block_label(self, content: str, index: int) -> tuple[str, str]:
        if content.startswith("PROMPT_AUTHORITY_HIERARCHY"):
            return "prompt_authority", "PROMPT_AUTHORITY_HIERARCHY"
        if content.startswith("LONG_TERM_PARTY_MEMORY"):
            return "long_term_memory", "LONG_TERM_PARTY_MEMORY"
        if content.startswith("RP_STORY_MEMORY"):
            return "rp_story_memory", "RP_STORY_MEMORY"
        if content.startswith("WORLD_SYSTEM_PROMPT"):
            return "world_system_prompt", "World system prompt"
        if content.startswith("PLAYER_CHARACTER"):
            return "player_character", "Персонаж игрока"
        if content.startswith("WORLD_AUTHORS_NOTE"):
            return "world_authors_note", "World author's note"
        if content.startswith("RELEVANT_CHARACTERS"):
            return "relevant_characters", "Relevant characters"
        if content.startswith("RETRIEVED_ARCHIVE_SCENES"):
            return "retrieved_archive_scenes", "Retrieved archive scenes"
        if content.startswith("UNCOMPACTED_ARCHIVE_FALLBACK"):
            return "uncompacted_archive_fallback", "Uncompacted archive fallback"
        if content.startswith("PARTY_LORE_CARDS"):
            return "party_lore_cards", "Party lore cards"
        if content.startswith("ИСПРАВЛЕНИЯ ИГРОКА"):
            return "player_corrections", "Исправления игрока"
        if content.startswith("СОБЫТИЯ МИРА"):
            return "world_events", "События мира"
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

    def memory_inspection(
        self,
        latest: str,
        *,
        fitted_raw_turn_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        revision_seven = self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 7
        revision_eight = self.settings.scenario_type == "rp" and self.settings.rp_contract_revision >= 8
        story_memory = self.rp_story_memory.prompt_snapshot() if self.rp_story_memory else None
        coverage = story_memory if revision_seven else self.store.latest_memory_coverage()
        covered_through = (
            story_memory_safe_coverage(story_memory)
            if revision_eight
            else int(coverage["to_turn_id"])
            if coverage
            else 0
        )
        selected = (
            []
            if revision_eight
            else self.store.memory_for_prompt(self.settings.party_memory_prompt_max_chars)
        )
        selected_keys = {(item.get("memory_type"), item.get("id")) for item in selected}
        all_memory: list[dict[str, Any]] = []
        if not revision_eight:
            legacy = self.store.latest_memory_summary()
            if legacy:
                all_memory.append(legacy | {"memory_type": "legacy_cumulative"})
            all_memory.extend(self.store.memory_chapters())
        included = [self.memory_entry_view(item, "included") for item in all_memory if (item.get("memory_type"), item.get("id")) in selected_keys]
        excluded = [
            self.memory_entry_view(item, "excluded_prompt_budget")
            for item in all_memory
            if (item.get("memory_type"), item.get("id")) not in selected_keys
        ]
        raw_source = (
            raw_history_window(
                self.turns_for_prompt(),
                safe_coverage=covered_through,
                window_turns=self.settings.effective_rp_raw_history_window_turns,
            )
            if revision_eight
            else self.turns_for_prompt(after_turn_id=covered_through)
        )
        if revision_eight and fitted_raw_turn_ids is not None:
            fitted_ids = {int(turn_id) for turn_id in fitted_raw_turn_ids}
            included_raw = [
                turn for turn in raw_source if int(turn["id"]) in fitted_ids
            ]
            omitted_raw = [
                turn for turn in raw_source if int(turn["id"]) not in fitted_ids
            ]
        elif revision_seven:
            omitted_raw, included_raw = [], raw_source
        else:
            omitted_raw, included_raw = split_turns_by_token_budget(
                raw_source,
                max(self.settings.effective_party_history_token_budget - estimate_tokens(latest), 0),
            )
        retrieval = (
            []
            if revision_eight
            else self.store.explain_archived_retrieval(
                latest,
                through_turn_id=covered_through,
                limit=self.settings.party_memory_retrieval_limit,
            )
        )
        inspection = {
            "memory_coverage_through_turn_id": covered_through or None,
            "chapters": {"included": included, "excluded": excluded},
            "raw": {
                "included_turn_ids": turn_ids(included_raw),
                "excluded_turn_ids": turn_ids(omitted_raw),
                "excluded_reason": (
                    "hard_input_budget"
                    if revision_eight and omitted_raw
                    else "uncompacted_archive_fallback"
                    if omitted_raw
                    else None
                ),
            },
            "fallback": {
                "active": bool(omitted_raw) and not revision_eight,
                "turn_ids": [] if revision_eight else turn_ids(omitted_raw),
                "max_chars": self.settings.party_memory_fallback_max_chars,
                "service_jobs": [
                    {
                        "job_type": job["job_type"],
                        "status": job["status"],
                        "attempts": job["attempts"],
                        "max_attempts": job["max_attempts"],
                        "last_error": job["last_error"],
                    }
                    for job in self.store.service_jobs(limit=4)
                    if job["job_type"] == "memory"
                ],
            },
            "retrieval": [
                {
                    "turn_id": item["id"],
                    "score": item["retrieval_score"],
                    "lexical_score": item["lexical_score"],
                    "stem_hits": item["stem_hits"],
                    "fuzzy_score": item["fuzzy_score"],
                    "match_mode": item["match_mode"],
                    "matched_terms": item["matched_terms"],
                }
                for item in retrieval
            ],
        }
        if self.settings.scenario_type == "rp":
            story_stats = self.rp_story_memory.stats() if self.rp_story_memory else {}
            inspection["story_memory"] = self.story_memory_diagnostics(story_memory, story_stats)
        return inspection

    def story_memory_diagnostics(
        self,
        story_memory: dict[str, Any] | None,
        story_stats: dict[str, Any],
    ) -> dict[str, Any]:
        pending_threshold = story_stats.get(
            "pending_turn_threshold",
            self.settings.rp_story_memory_update_turns,
        )
        pending_turns = int(story_stats.get("pending_turns") or 0)
        threshold_exceeded = bool(
            story_stats.get(
                "pending_turn_threshold_exceeded",
                pending_turns >= int(pending_threshold),
            )
        )
        return {
            "enabled": True,
            "revision": story_memory.get("revision") if story_memory else None,
            "covered_turn_ids": (
                [story_memory.get("from_turn_id"), story_memory.get("to_turn_id")]
                if story_memory
                else None
            ),
            "prompt_max_chars": self.settings.rp_story_memory_prompt_max_chars,
            "reserved_tokens": self.settings.rp_story_memory_reserve_tokens,
            "pending_turns": pending_turns,
            "pending_tokens": int(story_stats.get("pending_tokens") or 0),
            "pending_turn_threshold": int(pending_threshold),
            "pending_turn_threshold_exceeded": threshold_exceeded,
            "hard_overflow": bool(story_stats.get("hard_overflow", False)),
            "operator_status": story_stats.get("operator_status") or (
                "lagging" if threshold_exceeded else "normal"
            ),
            "force_refresh": {
                "attempted": bool(story_stats.get("force_refresh_attempted", False)),
                "request_id": story_stats.get("force_refresh_request_id"),
                "batches": int(story_stats.get("force_refresh_batches") or 0),
                "terminal_result": story_stats.get("force_refresh_terminal_result"),
                "coverage_before": story_stats.get("force_refresh_coverage_before"),
                "coverage_after": story_stats.get("force_refresh_coverage_after"),
            },
        }

    def turns_for_prompt(
        self,
        *,
        after_turn_id: int = 0,
        to_turn_id: int | None = None,
    ) -> list[dict[str, Any]]:
        revision_seven = (
            self.settings.scenario_type == "rp"
            and self.settings.rp_contract_revision >= 7
        )
        turns = self.store.turns_for_memory(
            after_turn_id=after_turn_id,
            to_turn_id=to_turn_id,
            include_noncanonical_fallback=revision_seven,
        )
        if not revision_seven:
            return turns
        if self.settings.rp_contract_revision >= 8:
            return eligible_rp_turns(turns)
        return unresolved_noncanonical_fallback_turns(self.store.get_state(), turns)

    def character_aliases(self) -> dict[str, list[str]] | None:
        if self.settings.scenario_type != "rp" or self.settings.rp_contract_revision < 7:
            return None
        return normalized_aliases(self.relationship_model or {})

    def authored_stable_affiliations(self) -> dict[str, str] | None:
        if self.settings.scenario_type != "rp" or self.settings.rp_contract_revision < 7:
            return None
        values = (self.scene_contract or {}).get("stable_affiliations")
        if not isinstance(values, dict):
            return None
        return {
            str(character_id): affiliation
            for character_id, affiliation in values.items()
            if isinstance(character_id, str) and isinstance(affiliation, str)
        }

    def memory_entry_view(self, item: dict[str, Any], status: str) -> dict[str, Any]:
        return {
            "id": item.get("id"),
            "memory_type": item.get("memory_type", "legacy_cumulative"),
            "from_turn_id": item.get("from_turn_id"),
            "to_turn_id": item.get("to_turn_id"),
            "estimated_tokens": estimate_tokens(str(item.get("summary_text") or "")),
            "status": status,
        }


def turn_ids(turns: list[dict[str, Any]]) -> list[int]:
    return [int(turn["id"]) for turn in turns]
