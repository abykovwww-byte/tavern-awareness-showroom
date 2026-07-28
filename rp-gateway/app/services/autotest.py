"""Admin-only LLM player used by isolated Light GUI auto-test parties."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.models.schemas import ModelProfileSummary
from app.services.narrative import NarrativeClient, provider_rate_limit_error, response_text
from app.services.provider_auth import outbound_headers


logger = logging.getLogger(__name__)
MAX_VISIBLE_TRANSCRIPT_CHARS = 20_000
MAX_VISIBLE_TURNS = 16


class AutoPlayerClient:
    def __init__(self, settings: Settings, profile: ModelProfileSummary):
        self.settings = settings
        self.profile = profile

    async def next_action(
        self,
        *,
        player_prompt: str,
        player_character: Any,
        scenario_type: str,
        history: list[dict[str, Any]],
        request_id: str,
    ) -> str:
        if self.settings.nvidia_api_base.startswith("mock://"):
            return "I examine the situation carefully and take the next action consistent with my role."

        messages = self.visible_messages(player_prompt, player_character, scenario_type, history)
        payload: dict[str, Any] = {
            "model": self.profile.model,
            "messages": messages,
            "temperature": float(self.profile.params.get("temperature") or 0.7),
            "max_tokens": min(int(self.profile.params.get("max_tokens") or 800), 1200),
            "stream": False,
        }
        NarrativeClient(self.settings).apply_prompt_cache_policy(payload)
        headers = outbound_headers(self.settings, None)
        timeout = httpx.Timeout(self.settings.model_attempt_timeout_seconds, connect=15.0)
        started = time.perf_counter()
        logger.info(
            "autotest_player_attempt_start request_id=%s provider=%s model=%s timeout_seconds=%s",
            request_id,
            self.profile.provider,
            self.profile.model,
            self.settings.model_attempt_timeout_seconds,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{self.settings.nvidia_api_base.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
            )
        if response.status_code == 429:
            raise provider_rate_limit_error(response, self.profile.provider, self.profile.model)
        response.raise_for_status()
        action = response_text(response.json()).strip()
        if not action:
            raise RuntimeError("Auto-player returned an empty action")
        if len(action) > 12_000:
            action = action[:12_000].rstrip()
        logger.info(
            "autotest_player_attempt_success request_id=%s provider=%s model=%s elapsed_ms=%s",
            request_id,
            self.profile.provider,
            self.profile.model,
            round((time.perf_counter() - started) * 1000, 2),
        )
        return action

    def visible_messages(
        self,
        player_prompt: str,
        player_character: Any,
        scenario_type: str,
        history: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        character_name = str(getattr(player_character, "name", "") or "Player")
        character_description = str(getattr(player_character, "description", "") or "")
        system = (
            f"You control only the player character {character_name}.\n"
            f"Scenario type: {scenario_type}.\n"
            f"Public character description: {character_description}\n\n"
            "PLAYER BEHAVIOR PROMPT:\n"
            f"{player_prompt.strip()}\n\n"
            "Return only the player's next in-world action or dialogue. Do not narrate the GM response, "
            "do not reveal analysis, do not write system notes, and do not decide NPC actions. "
            "Use only the visible transcript below; you have no access to hidden state, scoring, or answer keys."
        )
        transcript: list[dict[str, str]] = []
        used = 0
        for turn in reversed(history[-MAX_VISIBLE_TURNS:]):
            pair: list[dict[str, str]] = []
            player_message = str(turn.get("player_message") or "")
            narrative_response = str(turn.get("narrative_response") or "")
            if player_message and not player_message.startswith("[AUTO_START]"):
                pair.append({"role": "assistant", "content": player_message})
            if narrative_response:
                pair.append({"role": "user", "content": narrative_response})
            pair_size = sum(len(message["content"]) for message in pair)
            if transcript and used + pair_size > MAX_VISIBLE_TRANSCRIPT_CHARS:
                break
            transcript[0:0] = pair
            used += pair_size
        transcript.append(
            {
                "role": "user",
                "content": "Choose and write the next player action now. Output only that action or dialogue.",
            }
        )
        return [{"role": "system", "content": system}, *transcript]
