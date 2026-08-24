from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.service_model_client import TRACE_SCHEMA_VERSION, ServiceModelClient
from app.services.service_models import service_model_choice, service_model_settings


def settings_for(tmp_path: Path, *, api_key: str = "provider-key") -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'service-log.db'}",
        openrouter_api_base="https://service.example/v1",
        service_openrouter_api_key=api_key,
        narrative_model="service/model",
    )


def rows(settings: Settings) -> list[sqlite3.Row]:
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.row_factory = sqlite3.Row
        return list(connection.execute("SELECT * FROM service_call_log ORDER BY id"))


def test_complete_preserves_provider_payload_and_logs_nonempty_trace(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "service/model",
                "choices": [{"message": {"content": "structured result"}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                    "token": "usage-secret",
                },
            },
        )

    settings = settings_for(tmp_path)
    payload = {
        "model": "service/model",
        "temperature": 0.1,
        "stream": False,
        "messages": [
            {"role": "system", "content": "Return JSON."},
            {"role": "user", "content": "Summarize turn 7."},
        ],
    }
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler))

    completion = asyncio.run(
        client.complete(
            role="memory_summary",
            provider="openrouter",
            model="service/model",
            party_id="party_test",
            turn_id=7,
            request_id="req_trace_7",
            party_turn=5,
            attempt=2,
            section_key="situation",
            update_id="story:situation:through-8",
            prompt=json.dumps(payload["messages"], ensure_ascii=False, separators=(",", ":")),
            payload=payload,
        )
    )

    assert captured == payload
    assert completion.status == "completed"
    trace = rows(settings)[0]
    assert trace["party_id"] == "party_test"
    assert trace["turn_id"] == 7
    assert trace["request_id"] == "req_trace_7"
    assert trace["party_turn"] == 5
    assert trace["role"] == "memory_summary"
    assert trace["status"] == "completed"
    assert trace["provider"] == "openrouter"
    assert trace["model"] == "service/model"
    assert trace["attempt"] == 2
    assert trace["section_key"] == "situation"
    assert trace["update_id"] == "story:situation:through-8"
    assert trace["latency_ms"] >= 0
    assert trace["http_status"] == 200
    assert json.loads(trace["usage_json"]) == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "total_tokens": 14,
        "token": "[REDACTED]",
    }
    assert trace["error_json"] is None
    assert trace["trace_schema_version"] == TRACE_SCHEMA_VERSION


def test_complete_uses_explicit_role_provider_and_model_without_payload_leakage(
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "choices": [{"message": {"content": "{}"}}],
            },
        )

    settings = settings_for(tmp_path)
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler))
    asyncio.run(
        client.complete(
            role="rp_story_memory_section",
            provider="openrouter",
            model="deepseek/deepseek-v4-pro",
            party_id="party-rev8",
            turn_id=8,
            section_key="situation",
            update_id="update-rev8",
            prompt="[]",
            payload={
                "model": "must-be-overridden",
                "messages": [{"role": "user", "content": "update"}],
            },
        )
    )

    assert captured["model"] == "deepseek/deepseek-v4-pro"
    assert "provider" not in captured
    assert "section_key" not in captured
    assert "update_id" not in captured
    trace = rows(settings)[0]
    assert trace["provider"] == "openrouter"
    assert trace["model"] == "deepseek/deepseek-v4-pro"
    assert trace["prompt_text"] == "[]"
    assert "deepseek/deepseek-v4-pro" in trace["raw_response"]


def test_complete_logs_error_status_and_raw_provider_response(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, json={"error": "provider unavailable"})

    settings = settings_for(tmp_path)
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            client.complete(
                role="world_instructor",
                provider="openrouter",
                party_id="party_test",
                turn_id=3,
                request_id="req_world_3",
                party_turn=3,
                attempt=1,
                section_key="threads",
                update_id="story:threads:through-8",
                prompt="Draft a state patch.",
                model="service/model",
            )
        )

    trace = rows(settings)[0]
    assert trace["status"] == "error"
    assert "provider unavailable" in trace["raw_response"]
    assert trace["request_id"] == "req_world_3"
    assert trace["party_turn"] == 3
    assert trace["model"] == "service/model"
    assert trace["attempt"] == 1
    assert trace["section_key"] == "threads"
    assert trace["update_id"] == "story:threads:through-8"
    assert trace["http_status"] == 503
    assert json.loads(trace["error_json"])["type"] == "HTTPStatusError"
    assert trace["usage_json"] is None
    assert trace["trace_schema_version"] == TRACE_SCHEMA_VERSION


def test_complete_redacts_secrets_only_when_writing_trace(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "visible answer; access_token=response-token; password=response-password"
                        }
                    }
                ]
            },
        )

    settings = settings_for(tmp_path, api_key="provider-key")
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler))
    prompt = (
        "visible prompt; api_key=prompt-key; password='prompt-password'; "
        "Authorization: Bearer bearer-value; configured=provider-key"
    )

    completion = asyncio.run(
        client.complete(
            role="rp_story_memory",
            provider="openrouter",
            party_id="party_test",
            turn_id=9,
            prompt=prompt,
            model="service/model",
        )
    )

    assert "response-token" in completion.raw_response
    trace = rows(settings)[0]
    persisted = f"{trace['prompt_text']}\n{trace['raw_response']}"
    for secret in (
        "provider-key",
        "prompt-key",
        "prompt-password",
        "bearer-value",
        "response-token",
        "response-password",
    ):
        assert secret not in persisted
    assert "visible prompt" in trace["prompt_text"]
    assert "visible answer" in trace["raw_response"]
    assert "[REDACTED]" in persisted


def test_trace_write_failure_does_not_change_successful_service_result(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "ok"}}]})

    settings = settings_for(tmp_path)
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler))

    def broken_record(**_values: object) -> None:
        raise sqlite3.OperationalError("diagnostic table unavailable")

    client._record = broken_record  # type: ignore[method-assign]
    completion = asyncio.run(
        client.complete(
            role="memory_summary",
            provider="openrouter",
            model="service/model",
            party_id="party_test",
            turn_id=1,
            request_id="req_fail_open",
            prompt="access_token=one refresh_token=two client_secret=three x-api-key=four",
        )
    )

    assert completion.data["choices"][0]["message"]["content"] == "ok"
    assert rows(settings) == []


def test_trace_migration_failure_does_not_block_service_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "ok"}}]})

    def broken_migrate(_self: ServiceModelClient) -> None:
        raise sqlite3.OperationalError("diagnostic migration unavailable")

    monkeypatch.setattr(ServiceModelClient, "_migrate", broken_migrate)
    client = ServiceModelClient(settings_for(tmp_path), transport=httpx.MockTransport(handler))

    completion = asyncio.run(
        client.complete(
            role="memory_summary",
            provider="openrouter",
            model="service/model",
            party_id="party_test",
            turn_id=1,
            request_id="req_migration_fail_open",
            prompt="Summarize.",
        )
    )

    assert completion.data["choices"][0]["message"]["content"] == "ok"


def test_extended_credential_shapes_are_redacted(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "ok"}}]})

    settings = settings_for(tmp_path)
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler))
    asyncio.run(
        client.complete(
            role="memory_summary",
            provider="openrouter",
            model="service/model",
            party_id="party_test",
            turn_id=1,
            prompt="access_token=one refresh_token=two client_secret=three x-api-key=four",
        )
    )

    persisted = rows(settings)[0]["prompt_text"]
    for secret in ("one", "two", "three", "four"):
        assert secret not in persisted


def test_retention_uses_environment_default_and_removes_expired_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("SERVICE_CALL_LOG_RETENTION_DAYS", "7")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "ok"}}]})

    settings = settings_for(tmp_path)
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler), now=lambda: now)
    expired_at = (now - timedelta(days=8)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute(
            """
            INSERT INTO service_call_log (
                party_id, turn_id, role, prompt_text, raw_response, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("party_old", 1, "memory_summary", "old", "old", expired_at, "completed"),
        )

    asyncio.run(
        client.complete(
            role="memory_summary",
            provider="openrouter",
            party_id="party_new",
            turn_id=2,
            prompt="new",
            model="service/model",
        )
    )

    traces = rows(settings)
    assert client.retention_days == 7
    assert [trace["party_id"] for trace in traces] == ["party_new"]


def test_default_and_negative_retention_are_unlimited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    monkeypatch.delenv("SERVICE_CALL_LOG_RETENTION_DAYS", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": "ok"}}]})

    settings = settings_for(tmp_path)
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler), now=lambda: now)
    expired_at = (now - timedelta(days=400)).isoformat(timespec="seconds").replace("+00:00", "Z")
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.execute(
            """
            INSERT INTO service_call_log (
                party_id, turn_id, role, prompt_text, raw_response, created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("party_old", 1, "memory_summary", "old", "old", expired_at, "completed"),
        )

    asyncio.run(
        client.complete(
            role="memory_summary",
            provider="openrouter",
            party_id="party_new",
            turn_id=2,
            prompt="new",
            model="service/model",
        )
    )

    assert client.retention_days == 0
    assert [trace["party_id"] for trace in rows(settings)] == ["party_old", "party_new"]
    assert ServiceModelClient(settings, retention_days=-5).retention_days == 0
    monkeypatch.setenv("SERVICE_CALL_LOG_RETENTION_DAYS", "-5")
    assert ServiceModelClient(settings).retention_days == 0


def test_disabled_local_service_model_never_switches_provider(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'service-log.db'}",
        llm_provider="nvidia",
        local_llm_enabled=False,
        local_llm_base_url="https://local-service.invalid/v1",
    )
    client = ServiceModelClient(settings)

    with pytest.raises(RuntimeError, match="selected local service model is unavailable"):
        asyncio.run(
            client.complete(
                role="memory_summary",
                provider="local",
                model="local-model",
                party_id="party_local_unavailable",
                turn_id=1,
                prompt="Summarize.",
            )
        )

    traces = rows(settings)
    assert [trace["provider"] for trace in traces] == ["local"]
    assert all(trace["provider"] != "nvidia" for trace in traces)


def test_retired_service_model_choice_is_disabled_without_provider_switch(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'service-log.db'}",
        service_model_choice="or-retired-nvidia-choice",
        local_llm_enabled=True,
        service_openrouter_api_key="configured-but-unused",
    )
    ServiceModelClient(settings)

    selected = service_model_choice(settings)
    assert selected["provider"] == "retired"
    assert selected["available"] is False
    with pytest.raises(ValueError, match="service model choice is retired or unsupported"):
        service_model_settings(settings)
    assert rows(settings) == []


def test_migration_adds_trace_columns_to_legacy_table_idempotently(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.executescript(
            """
            CREATE TABLE service_call_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                party_id TEXT,
                turn_id INTEGER,
                role TEXT NOT NULL,
                prompt_text TEXT NOT NULL,
                raw_response TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO service_call_log (
                party_id, turn_id, role, prompt_text, raw_response, created_at, status
            ) VALUES ('party_legacy', 4, 'memory_summary', 'prompt', 'response',
                      '2026-08-01T00:00:00Z', 'completed');
            """
        )

    ServiceModelClient(settings)
    ServiceModelClient(settings)

    with sqlite3.connect(settings.sqlite_path) as connection:
        connection.row_factory = sqlite3.Row
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(service_call_log)")}
        legacy = connection.execute("SELECT * FROM service_call_log WHERE party_id = 'party_legacy'").fetchone()
    assert {
        "request_id",
        "party_turn",
        "provider",
        "model",
        "attempt",
        "latency_ms",
        "http_status",
        "usage_json",
        "error_json",
        "trace_schema_version",
        "section_key",
        "update_id",
    } <= columns
    assert legacy is not None
    assert legacy["prompt_text"] == "prompt"
    assert legacy["raw_response"] == "response"
    assert legacy["request_id"] is None
    assert legacy["trace_schema_version"] is None
    assert legacy["section_key"] is None
    assert legacy["update_id"] is None


def test_global_service_model_consumers_do_not_bypass_service_model_client() -> None:
    app_root = Path(__file__).parents[1] / "app"
    bypasses = []
    for path in app_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "service_model_settings" in source and "httpx.AsyncClient" in source:
            bypasses.append(path.relative_to(app_root).as_posix())

    assert bypasses == []
