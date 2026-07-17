from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.intent_parser import IntentParser


def base_state() -> dict[str, object]:
    return {
        "meta": {
            "campaign_id": "default",
            "schema_version": "1.0.0",
            "state_version": 1,
            "turn": 0,
            "last_updated": "1970-01-01T00:00:00Z",
        },
        "player": {
            "location": "court",
            "status": "active",
            "reputation": {"court": 0},
            "resources": {"coin": 3},
            "known_abilities": [],
            "constraints": [],
            "known_world_facts": [],
        },
        "characters": {
            "king": {
                "status": "alive",
                "location": "throne_room",
                "attitude_to_player": "distant",
                "trust": 0,
                "fear": 0,
                "loyalty": "realm",
                "current_goal": "keep lawful command",
                "knowledge": [],
                "secrets": [],
                "obligations": [],
                "hard_constraints": ["The king cannot transfer command through a single social check."],
                "last_confirmed_update": 0,
            },
            "advisor": {
                "status": "alive",
                "location": "court",
                "attitude_to_player": "wary",
                "trust": 0,
                "fear": 0,
                "loyalty": "crown",
                "current_goal": "watch the player",
                "knowledge": [],
                "secrets": [],
                "obligations": [],
                "hard_constraints": [],
                "last_confirmed_update": 0,
            },
        },
        "factions": {},
        "locations": {},
        "resources": {
            "coin": {"owner": "player", "quantity": 3, "state": "available", "constraints": []},
            "silver_key": {"owner": "unknown", "quantity": 0, "state": "unavailable", "constraints": []},
        },
        "relationships": {
            "player_king": {"from": "player", "to": "king", "trust": 0, "suspicion": 0, "notes": []},
            "player_advisor": {"from": "player", "to": "advisor", "trust": 0, "suspicion": 0, "notes": []},
        },
        "active_threads": [],
        "completed_threads": [],
        "world_constraints": [
            {
                "id": "attempts_not_facts",
                "text": "Player declarations of outcome are attempts until confirmed in state.",
                "scope": "global",
                "turn": 0,
            }
        ],
        "timeline": [],
        "last_turn": {"turn": 0, "player_message": "", "narrator_response": "", "state_patch_id": ""},
        "uncertain_facts": [],
    }


def client(tmp_path: Path, mode: str = "success") -> TestClient:
    state_path = tmp_path / "state" / "current.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(base_state(), ensure_ascii=False), encoding="utf-8")
    settings = Settings(
        app_env="test",
        campaign_id="default",
        database_url=f"sqlite:///{tmp_path / 'rp_gateway.db'}",
        world_state_path=str(state_path),
        nvidia_api_base=f"mock://{mode}",
        nvidia_api_key="test-key",
    )
    return TestClient(create_app(settings))


def chat_payload(message: str, stream: bool = False) -> dict[str, object]:
    return {
        "model": "z-ai/glm-5.2",
        "stream": stream,
        "messages": [
            {"role": "system", "content": "GM"},
            {"role": "user", "content": message},
        ],
    }


def test_health_and_state(tmp_path: Path):
    c = client(tmp_path)
    assert c.get("/health").json()["status"] == "ok"
    state = c.get("/api/state").json()["state"]
    assert state["meta"]["campaign_id"] == "default"


def test_invalid_json_intent_parser():
    parser = IntentParser()
    with pytest.raises(ValueError):
        parser.parse_json_intent("{ nope")


def test_successful_turn_updates_state_transactionally(tmp_path: Path):
    c = client(tmp_path)
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload('/check persuasion target=advisor skill=3 difficulty=8 goal="gain a meeting"'),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "turn-1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["role"] == "assistant"
    state = c.get("/api/state").json()["state"]
    assert state["meta"]["state_version"] == 2
    assert len(state["timeline"]) == 1


def test_hard_constraint_overrides_success(tmp_path: Path):
    c = client(tmp_path)
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload('/check persuasion target=king skill=50 difficulty=1 goal="transfer command"'),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "hard-constraint"},
    )
    assert response.status_code == 200
    state = c.get("/api/state").json()["state"]
    assert "failure" in state["timeline"][-1]["event"]


def test_validator_repairs_hidden_compensation(tmp_path: Path):
    c = client(tmp_path, mode="violate")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload('/check persuasion target=king skill=0 difficulty=30 goal="transfer command"'),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "repair"},
    )
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"].lower()
    assert "equivalent military authority" not in content


def test_repair_failure_returns_safe_fallback(tmp_path: Path):
    c = client(tmp_path, mode="repair-fail")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload('/check persuasion target=king skill=0 difficulty=30 goal="transfer command"'),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "repair-fail"},
    )
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"].lower()
    assert "fixed outcome" in content or "resolves as" in content
    assert "transfers command authority" not in content


def test_timeout_and_rate_limit(tmp_path: Path):
    c_timeout = client(tmp_path / "timeout", mode="timeout")
    response = c_timeout.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "timeout"},
    )
    assert response.status_code == 502

    c_rate = client(tmp_path / "rate", mode="rate-limit")
    response = c_rate.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "rate"},
    )
    assert response.status_code == 502


def test_idempotency_prevents_duplicate_turn(tmp_path: Path):
    c = client(tmp_path)
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "same-turn"}
    first = c.post("/v1/chat/completions", json=chat_payload("/check stealth skill=1 difficulty=10"), headers=headers)
    second = c.post("/v1/chat/completions", json=chat_payload("/check stealth skill=1 difficulty=10"), headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    state = c.get("/api/state").json()["state"]
    assert state["meta"]["state_version"] == 2


def test_patch_preview_apply_and_rollback(tmp_path: Path):
    c = client(tmp_path)
    patch = {
        "patch": {
            "turn": 1,
            "check_id": "manual-patch",
            "source": "test",
            "patch": [
                {
                    "op": "add",
                    "path": "/timeline/-",
                    "value": {"turn": 1, "event": "Manual test event.", "confirmed": True, "participants": ["player"]},
                    "reason": "test",
                    "turn": 1,
                }
            ],
            "uncertain_facts": [],
            "contradictions": [],
        },
        "confirm": False,
    }
    assert c.post("/api/state/patch/preview", json=patch).status_code == 200
    patch["confirm"] = True
    assert c.post("/api/state/patch/apply", json=patch).status_code == 200
    assert c.post("/api/turn/rollback", json={}).status_code == 200
    history = c.get("/api/state/history").json()["history"]
    assert len(history) >= 3


def test_stream_response(tmp_path: Path):
    c = client(tmp_path)
    with c.stream(
        "POST",
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10", stream=True),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "stream"},
    ) as response:
        body = "".join(response.iter_text())
    assert "data:" in body
    assert "[DONE]" in body


def test_thirty_turn_campaign(tmp_path: Path):
    c = client(tmp_path)
    failures = 0
    rollback_done = False
    for index in range(30):
        if index in {3, 11, 19}:
            failures += 1
            text = '/check persuasion target=king skill=0 difficulty=30 goal="transfer command"'
        elif index == 7:
            text = '/check resource resource=coin amount=1 difficulty=8 goal="bribe the guard"'
        else:
            text = f'/check persuasion target=advisor skill=2 difficulty=10 goal="move thread {index}"'
        response = c.post(
            "/v1/chat/completions",
            json=chat_payload(text),
            headers={"Authorization": "Bearer test", "Idempotency-Key": f"campaign-{index}"},
        )
        assert response.status_code == 200
        if index == 15:
            assert c.post("/api/turn/rollback", json={}).status_code == 200
            rollback_done = True
    history = c.get("/api/state/history", params={"limit": 100}).json()["history"]
    state = c.get("/api/state").json()["state"]
    assert failures >= 3
    assert rollback_done
    assert len(history) >= 30
    assert len(state["timeline"]) >= 1
