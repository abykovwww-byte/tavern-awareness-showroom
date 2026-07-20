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


def client(tmp_path: Path, mode: str = "success", api_key: str = "test-key") -> TestClient:
    state_path = tmp_path / "state" / "current.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(base_state(), ensure_ascii=False), encoding="utf-8")
    settings = Settings(
        app_env="test",
        campaign_id="default",
        database_url=f"sqlite:///{tmp_path / 'rp_gateway.db'}",
        world_state_path=str(state_path),
        party_state_root=str(tmp_path / "state" / "parties"),
        worldpacks_path=str(tmp_path / "worldpacks"),
        nvidia_api_base=f"mock://{mode}",
        nvidia_api_key=api_key,
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


def write_worldpack(root: Path, pack_id: str = "demo-world") -> Path:
    pack_dir = root / "worldpacks" / pack_id
    pack_dir.mkdir(parents=True)
    manifest = {
        "id": pack_id,
        "title": "Demo World",
        "player_role": "Field investigator with limited authority.",
        "files": {"state_seed": "state-seed.json", "world_info": "world-info/index.md"},
    }
    seed = base_state()
    seed["meta"]["campaign_id"] = pack_id
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (pack_dir / "state-seed.json").write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    (pack_dir / "world-info").mkdir()
    (pack_dir / "world-info" / "index.md").write_text("# Demo World\n", encoding="utf-8")
    return pack_dir


def test_health_and_state(tmp_path: Path):
    c = client(tmp_path)
    assert c.get("/health").json()["status"] == "ok"
    state = c.get("/api/state").json()["state"]
    assert state["meta"]["campaign_id"] == "default"


def test_party_flow_creates_state_and_sends_message(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)

    worldpacks = c.get("/api/worldpacks").json()["worldpacks"]
    assert [pack["id"] for pack in worldpacks] == ["demo-world"]

    models = c.get("/api/model-profiles").json()["model_profiles"]
    assert models
    draft = c.post(
        "/api/player-characters/draft",
        json={"worldpack_id": "demo-world", "name": "Mira", "concept": "Careful investigator."},
    )
    assert draft.status_code == 200
    character = c.post(
        "/api/player-characters",
        json={
            "worldpack_id": "demo-world",
            "name": draft.json()["draft"]["name"],
            "description": draft.json()["draft"]["description"],
            "profile": draft.json()["draft"]["profile"],
        },
    )
    assert character.status_code == 200

    party = c.post(
        "/api/parties",
        json={
            "title": "Mira at the Gate",
            "worldpack_id": "demo-world",
            "player_character_id": character.json()["player_character"]["id"],
            "model_profile_id": models[0]["id"],
        },
    )
    assert party.status_code == 200
    party_id = party.json()["party"]["id"]

    state = c.get(f"/api/parties/{party_id}/state").json()["state"]
    assert state["meta"]["campaign_id"] == party_id
    assert state["player"]["character_id"] == character.json()["player_character"]["id"]

    message = c.post(
        f"/api/parties/{party_id}/messages",
        json={"content": '/check persuasion target=advisor skill=2 difficulty=8 goal="gain a meeting"'},
        headers={"Authorization": "Bearer test"},
    )
    assert message.status_code == 200
    assert message.json()["message"]["role"] == "assistant"
    assert message.json()["party_id"] == party_id

    history = c.get(f"/api/parties/{party_id}/history").json()["turns"]
    assert len(history) == 1
    assert "gain a meeting" in history[0]["player_message"]


def test_party_world_proposals_are_isolated(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    model_id = c.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    parties: list[str] = []
    for name in ["A", "B"]:
        character = c.post(
            "/api/player-characters",
            json={"worldpack_id": "demo-world", "name": name, "description": name, "profile": {"name": name}},
        ).json()["player_character"]
        party = c.post(
            "/api/parties",
            json={
                "title": f"Party {name}",
                "worldpack_id": "demo-world",
                "player_character_id": character["id"],
                "model_profile_id": model_id,
            },
        ).json()["party"]
        parties.append(party["id"])

    proposal = c.post(
        f"/api/parties/{parties[0]}/world/instruct",
        json={"instruction": "Remember: guard Varn now suspects the player."},
        headers={"Authorization": "Bearer test"},
    )
    assert proposal.status_code == 200
    assert proposal.json()["applied"] is False

    first_history = c.get(f"/api/parties/{parties[0]}/history").json()["state_versions"]
    second_state = c.get(f"/api/parties/{parties[1]}/state").json()["state"]
    assert first_history[-1]["version"] == 1
    assert "varn" not in second_state["characters"]


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


def test_missing_provider_key_does_not_mutate_state(tmp_path: Path):
    c = client(tmp_path, api_key="")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload('/check persuasion target=advisor skill=3 difficulty=8 goal="gain a meeting"'),
        headers={"Idempotency-Key": "missing-key"},
    )
    assert response.status_code == 401
    state = c.get("/api/state").json()["state"]
    assert state["meta"]["state_version"] == 1


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


def test_world_instruction_preview_does_not_apply_until_confirmed(tmp_path: Path):
    c = client(tmp_path)
    response = c.post(
        "/api/world/instruct",
        json={"instruction": "Remember: guard Varn now suspects the player but fears the captain."},
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["applied"] is False
    proposal_id = body["proposal"]["proposal_id"]

    state = c.get("/api/state").json()["state"]
    assert state["meta"]["state_version"] == 1
    assert "varn" not in state["characters"]

    preview = c.post("/api/world/apply", json={"proposal_id": proposal_id, "confirm": False})
    assert preview.status_code == 200
    assert preview.json()["applied"] is False

    applied = c.post("/api/world/apply", json={"proposal_id": proposal_id, "confirm": True})
    assert applied.status_code == 200
    state = applied.json()["state"]
    assert state["meta"]["state_version"] == 2
    assert state["characters"]["varn"]["status"] == "alive"
    assert state["relationships"]["player_varn"]["suspicion"] == 4


def test_world_chat_command_draft_apply_and_discard(tmp_path: Path):
    c = client(tmp_path)
    draft = c.post(
        "/v1/chat/completions",
        json=chat_payload("/world Remember: guard Varn now suspects the player."),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "world-draft"},
    )
    assert draft.status_code == 200
    content = draft.json()["choices"][0]["message"]["content"]
    assert "World proposal ready:" in content
    proposals = c.get("/api/world/proposals").json()["proposals"]
    assert len(proposals) == 1

    discard = c.post(
        "/v1/chat/completions",
        json=chat_payload("/world discard latest"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "world-discard"},
    )
    assert discard.status_code == 200
    assert c.get("/api/world/proposals").json()["proposals"] == []

    c.post(
        "/v1/chat/completions",
        json=chat_payload("/world Remember: guard Varn now suspects the player."),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "world-draft-2"},
    )
    apply = c.post(
        "/v1/chat/completions",
        json=chat_payload("/world apply latest"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "world-apply"},
    )
    assert apply.status_code == 200
    assert "World patch applied" in apply.json()["choices"][0]["message"]["content"]
    show = c.post(
        "/v1/chat/completions",
        json=chat_payload("/world show"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "world-show"},
    )
    assert "World state version:" in show.json()["choices"][0]["message"]["content"]


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
