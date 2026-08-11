from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.service_model_client import ServiceModelClient


def settings_for(tmp_path: Path, *, api_key: str = "provider-key") -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'service-log.db'}",
        nvidia_api_base="https://service.example/v1",
        nvidia_api_key=api_key,
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
            json={"model": "service/model", "choices": [{"message": {"content": "structured result"}}]},
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
            party_id="party_test",
            turn_id=7,
            prompt=json.dumps(payload["messages"], ensure_ascii=False, separators=(",", ":")),
            payload=payload,
        )
    )

    assert captured == payload
    assert completion.status == "completed"
    trace = rows(settings)[0]
    assert trace["party_id"] == "party_test"
    assert trace["turn_id"] == 7
    assert trace["role"] == "memory_summary"
    assert trace["status"] == "completed"
    assert trace["prompt_text"]
    assert trace["raw_response"]
    assert "Summarize turn 7." in trace["prompt_text"]
    assert "structured result" in trace["raw_response"]


def test_complete_logs_error_status_and_raw_provider_response(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, json={"error": "provider unavailable"})

    settings = settings_for(tmp_path)
    client = ServiceModelClient(settings, transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            client.complete(
                role="world_instructor",
                party_id="party_test",
                turn_id=3,
                prompt="Draft a state patch.",
                model="service/model",
            )
        )

    trace = rows(settings)[0]
    assert trace["status"] == "error"
    assert "provider unavailable" in trace["raw_response"]


def test_complete_redacts_secrets_only_when_writing_trace(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {"message": {"content": "visible answer; token=response-token; password=response-password"}}
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
            party_id="party_new",
            turn_id=2,
            prompt="new",
            model="service/model",
        )
    )

    traces = rows(settings)
    assert client.retention_days == 7
    assert [trace["party_id"] for trace in traces] == ["party_new"]


def test_owned_service_consumers_have_no_direct_httpx_completion_calls() -> None:
    services = Path(__file__).parents[1] / "app" / "services"
    for name in ("memory.py", "rp_story_memory.py", "world_instructor.py"):
        source = (services / name).read_text(encoding="utf-8")
        assert "ServiceModelClient" in source
        assert "httpx.AsyncClient" not in source
        assert "/chat/completions" not in source
