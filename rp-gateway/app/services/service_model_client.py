"""Shared low-level client and diagnostic trace for global service-model calls."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from app.core.config import Settings
from app.services.provider_auth import outbound_headers


DEFAULT_RETENTION_DAYS = 30
RETENTION_ENV = "SERVICE_CALL_LOG_RETENTION_DAYS"
REDACTED = "[REDACTED]"

_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|authorization|cookie|password|secret|token)[\"']?\s*[:=]\s*)"
    r"(?:\"(?:\\.|[^\"])*\"|'(?:\\.|[^'])*'|[^\s,;}\]]+)"
)


@dataclass(frozen=True)
class ServiceCompletion:
    """One successful OpenAI-compatible service-model response."""

    data: dict[str, Any]
    raw_response: str
    status: str
    status_code: int


def service_prompt_text(payload: dict[str, Any]) -> str:
    """Serialize the exact ordered provider messages used for a completion."""

    return json.dumps(payload.get("messages", []), ensure_ascii=False, separators=(",", ":"))


class ServiceModelClient:
    """The only low-level completion path for the stack-managed service model."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retention_days: int | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self.settings = settings
        self.transport = transport
        self.retention_days = self._retention_days(retention_days)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._migrate()

    async def complete(
        self,
        *,
        role: str,
        party_id: str | None,
        turn_id: int | None,
        prompt: str,
        **opts: Any,
    ) -> ServiceCompletion:
        payload = dict(opts.pop("payload", {}))
        payload.update(opts)
        if "messages" not in payload:
            payload["messages"] = [{"role": "user", "content": prompt}]
        if "model" not in payload:
            payload["model"] = self.settings.narrative_model

        response: httpx.Response | None = None
        try:
            client_options: dict[str, Any] = {
                "timeout": httpx.Timeout(self.settings.model_attempt_timeout_seconds, connect=15.0),
            }
            if self.transport is not None:
                client_options["transport"] = self.transport
            async with httpx.AsyncClient(**client_options) as client:
                response = await client.post(
                    f"{self.settings.nvidia_api_base.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=outbound_headers(self.settings, None),
                )
            raw_response = response.text
            if response.status_code == 429:
                raise RuntimeError(f"{self.settings.llm_provider} API returned 429 rate limit")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Service model response must be a JSON object")
        except Exception as exc:
            raw_response = response.text if response is not None else f"{type(exc).__name__}: {exc}"
            self._record(
                party_id=party_id,
                turn_id=turn_id,
                role=role,
                prompt=prompt,
                raw_response=raw_response,
                status="error",
            )
            raise

        self._record(
            party_id=party_id,
            turn_id=turn_id,
            role=role,
            prompt=prompt,
            raw_response=raw_response,
            status="completed",
        )
        return ServiceCompletion(
            data=data,
            raw_response=raw_response,
            status="completed",
            status_code=response.status_code,
        )

    def _migrate(self) -> None:
        Path(self.settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.settings.sqlite_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS service_call_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    party_id TEXT,
                    turn_id INTEGER,
                    role TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    raw_response TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_service_call_log_party_turn
                    ON service_call_log (party_id, turn_id, id);
                CREATE INDEX IF NOT EXISTS idx_service_call_log_created_at
                    ON service_call_log (created_at);
                """
            )

    def _record(
        self,
        *,
        party_id: str | None,
        turn_id: int | None,
        role: str,
        prompt: str,
        raw_response: str,
        status: str,
    ) -> None:
        created = self._utc_now()
        cutoff = created - timedelta(days=self.retention_days)
        with sqlite3.connect(self.settings.sqlite_path) as connection:
            connection.execute(
                """
                INSERT INTO service_call_log (
                    party_id, turn_id, role, prompt_text, raw_response, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    party_id,
                    turn_id,
                    role,
                    self._redact(prompt),
                    self._redact(raw_response),
                    self._timestamp(created),
                    status,
                ),
            )
            connection.execute(
                "DELETE FROM service_call_log WHERE created_at < ?",
                (self._timestamp(cutoff),),
            )

    def _redact(self, value: str) -> str:
        redacted = value
        known_secrets = {
            self.settings.nvidia_api_key,
            self.settings.service_nvidia_api_key,
            self.settings.service_openrouter_api_key,
        }
        for secret in sorted((item for item in known_secrets if item), key=len, reverse=True):
            redacted = redacted.replace(secret, REDACTED)
        redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", redacted)
        return _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)

    def _retention_days(self, explicit: int | None) -> int:
        if explicit is not None:
            return max(int(explicit), 1)
        try:
            return max(int(os.getenv(RETENTION_ENV, str(DEFAULT_RETENTION_DAYS))), 1)
        except ValueError:
            return DEFAULT_RETENTION_DAYS

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")
