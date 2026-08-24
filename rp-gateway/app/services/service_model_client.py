"""Shared low-level client and diagnostic trace for global service-model calls."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

import httpx

from app.core.config import Settings
from app.services.provider_auth import outbound_headers
from app.services.trace_redaction import REDACTED, redact_trace_value


DEFAULT_RETENTION_DAYS = 0
RETENTION_ENV = "SERVICE_CALL_LOG_RETENTION_DAYS"
TRACE_SCHEMA_VERSION = "rp-gateway.service-call.v1"

logger = logging.getLogger(__name__)


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
        try:
            self._migrate()
        except Exception as exc:  # noqa: BLE001 - diagnostics cannot decide a service result
            logger.warning(
                "service_call_trace_migration_failed error=%s",
                f"{type(exc).__name__}: {exc}",
            )

    async def complete(
        self,
        *,
        role: str,
        provider: Literal["local", "openrouter"],
        model: str,
        party_id: str | None,
        turn_id: int | None,
        prompt: str,
        request_id: str | None = None,
        party_turn: int | None = None,
        attempt: int | None = None,
        **opts: Any,
    ) -> ServiceCompletion:
        payload = dict(opts.pop("payload", {}))
        payload.update(opts)
        if "messages" not in payload:
            payload["messages"] = [{"role": "user", "content": prompt}]
        payload["model"] = model

        response: httpx.Response | None = None
        data: dict[str, Any] | None = None
        started = time.perf_counter()
        try:
            base_url, api_key = self._route(provider)
            client_options: dict[str, Any] = {
                "timeout": httpx.Timeout(self.settings.model_attempt_timeout_seconds, connect=15.0),
            }
            if self.transport is not None:
                client_options["transport"] = self.transport
            async with httpx.AsyncClient(**client_options) as client:
                response = await client.post(
                    f"{base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=outbound_headers(provider, api_key, None),
                )
            raw_response = response.text
            if response.status_code == 429:
                raise RuntimeError(f"{provider} API returned 429 rate limit")
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Service model response must be a JSON object")
        except Exception as exc:
            raw_response = response.text if response is not None else f"{type(exc).__name__}: {exc}"
            self._safe_record(
                party_id=party_id,
                turn_id=turn_id,
                request_id=request_id,
                party_turn=party_turn,
                role=role,
                prompt=prompt,
                raw_response=raw_response,
                status="error",
                provider=provider,
                model=model,
                attempt=attempt,
                latency_ms=self._elapsed_ms(started),
                http_status=response.status_code if response is not None else None,
                usage=None,
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise

        self._safe_record(
            party_id=party_id,
            turn_id=turn_id,
            request_id=request_id,
            party_turn=party_turn,
            role=role,
            prompt=prompt,
            raw_response=raw_response,
            status="completed",
            provider=provider,
            model=self._model_name(data.get("model") or model),
            attempt=attempt,
            latency_ms=self._elapsed_ms(started),
            http_status=response.status_code,
            usage=data.get("usage"),
            error=None,
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
                    status TEXT NOT NULL,
                    request_id TEXT,
                    party_turn INTEGER,
                    provider TEXT,
                    model TEXT,
                    attempt INTEGER,
                    latency_ms REAL,
                    http_status INTEGER,
                    usage_json TEXT,
                    error_json TEXT,
                    trace_schema_version TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_service_call_log_party_turn
                    ON service_call_log (party_id, turn_id, id);
                CREATE INDEX IF NOT EXISTS idx_service_call_log_created_at
                    ON service_call_log (created_at);
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(service_call_log)")}
            for name, column_type in (
                ("request_id", "TEXT"),
                ("party_turn", "INTEGER"),
                ("provider", "TEXT"),
                ("model", "TEXT"),
                ("attempt", "INTEGER"),
                ("latency_ms", "REAL"),
                ("http_status", "INTEGER"),
                ("usage_json", "TEXT"),
                ("error_json", "TEXT"),
                ("trace_schema_version", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE service_call_log ADD COLUMN {name} {column_type}")
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_service_call_log_party_request
                    ON service_call_log (party_id, request_id, id);
                CREATE INDEX IF NOT EXISTS idx_service_call_log_party_party_turn
                    ON service_call_log (party_id, party_turn, id);
                """
            )

    def _record(
        self,
        *,
        party_id: str | None,
        turn_id: int | None,
        request_id: str | None,
        party_turn: int | None,
        role: str,
        prompt: str,
        raw_response: str,
        status: str,
        provider: str | None,
        model: str | None,
        attempt: int | None,
        latency_ms: float,
        http_status: int | None,
        usage: Any,
        error: dict[str, Any] | None,
    ) -> None:
        created = self._utc_now()
        with sqlite3.connect(self.settings.sqlite_path) as connection:
            connection.execute(
                """
                INSERT INTO service_call_log (
                    party_id, turn_id, role, prompt_text, raw_response, created_at, status,
                    request_id, party_turn, provider, model, attempt, latency_ms,
                    http_status, usage_json, error_json, trace_schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    party_id,
                    turn_id,
                    role,
                    self._redact(prompt),
                    self._redact(raw_response),
                    self._timestamp(created),
                    status,
                    request_id,
                    party_turn,
                    provider,
                    model,
                    attempt,
                    latency_ms,
                    http_status,
                    self._redacted_json(usage),
                    self._redacted_json(error),
                    TRACE_SCHEMA_VERSION,
                ),
            )
            if self.retention_days > 0:
                cutoff = created - timedelta(days=self.retention_days)
                connection.execute(
                    "DELETE FROM service_call_log WHERE created_at < ?",
                    (self._timestamp(cutoff),),
                )

    def _safe_record(self, **values: Any) -> None:
        try:
            self._record(**values)
        except Exception as exc:  # noqa: BLE001 - diagnostics cannot decide a service result
            logger.warning(
                "service_call_trace_failed role=%s request_id=%s error=%s",
                values.get("role"),
                values.get("request_id"),
                f"{type(exc).__name__}: {exc}",
            )

    def _redacted_json(self, value: Any) -> str | None:
        if value is None:
            return None
        return json.dumps(redact_trace_value(value, self._known_secrets()), ensure_ascii=False, separators=(",", ":"))

    def _redact_json_value(self, value: Any) -> Any:
        return redact_trace_value(value, self._known_secrets())

    def _redact(self, value: str) -> str:
        return str(redact_trace_value(value, self._known_secrets()))

    def _known_secrets(self) -> tuple[str | None, ...]:
        return (
            self.settings.llm_api_key,
            self.settings.gemini_api_key,
            self.settings.openrouter_api_key,
            self.settings.service_openrouter_api_key,
        )

    def _route(self, provider: str) -> tuple[str, str]:
        if provider == "local":
            if not self.settings.local_llm_enabled:
                raise RuntimeError("selected local service model is unavailable")
            return self.settings.local_llm_base_url, ""
        if provider == "openrouter":
            return self.settings.openrouter_api_base, self.settings.service_openrouter_api_key
        raise ValueError(f"service provider is retired or unsupported: {provider}")

    def _retention_days(self, explicit: int | None) -> int:
        if explicit is not None:
            return max(int(explicit), 0)
        try:
            return max(int(os.getenv(RETENTION_ENV, str(DEFAULT_RETENTION_DAYS))), 0)
        except ValueError:
            return DEFAULT_RETENTION_DAYS

    @staticmethod
    def _model_name(value: Any) -> str | None:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 2)

    def _utc_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _timestamp(value: datetime) -> str:
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")
