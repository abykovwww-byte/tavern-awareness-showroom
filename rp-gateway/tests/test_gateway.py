from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app, party_chat_request, party_start_narrative_state, party_start_state_patch, settings_for_party
from app.models.schemas import ChatCompletionRequest, ChatMessage, Intent, Outcome, PartyMessageRequest
from app.services.adjudicator import Adjudicator
from app.services.intent_parser import IntentParser
from app.services.memory import MemorySummarizer
from app.services.narrative import NarrativeClient, provider_rate_limit_error
from app.services.nvidia_catalog import (
    OPENROUTER_FEATURED_MODELS,
    enrich_openrouter_profile_params,
    parse_build_catalog,
    prices_are_free,
    provider_model_is_suitable,
)
from app.services.rule_engine import RuleEngine, awareness_state_after_auto_start
from app.services.state_store import StateStore
from app.services.validator import OutputValidator, awareness_opening_fallback, safe_fallback


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


def client(tmp_path: Path, mode: str = "success", api_key: str = "test-key", **settings_overrides: object) -> TestClient:
    state_path = tmp_path / "state" / "current.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(base_state(), ensure_ascii=False), encoding="utf-8")
    settings_kwargs = {
        "app_env": "test",
        "campaign_id": "default",
        "database_url": f"sqlite:///{tmp_path / 'rp_gateway.db'}",
        "world_state_path": str(state_path),
        "party_state_root": str(tmp_path / "state" / "parties"),
        "worldpacks_path": str(tmp_path / "worldpacks"),
        "nvidia_api_base": f"mock://{mode}",
        "nvidia_api_key": api_key,
        "post_turn_helpers_inline": True,
        "auth_enabled": False,
    }
    settings_kwargs.update(settings_overrides)
    settings = Settings(**settings_kwargs)
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


def assert_no_gateway_service_text(content: str) -> None:
    lowered = content.lower()
    for marker in [
        "the action resolves as",
        "fixed outcome",
        "bounded desired outcome",
        "hard world constraints",
        "the narration preserves",
        "gateway check",
        "authoritative_outcome",
        "анализ:",
        "рекомендация:",
    ]:
        assert marker not in lowered


def write_worldpack(root: Path, pack_id: str = "demo-world", supported_modes: list[str] | None = None) -> Path:
    pack_dir = root / "worldpacks" / pack_id
    pack_dir.mkdir(parents=True)
    manifest = {
        "id": pack_id,
        "title": "Demo World",
        "player_role": "Field investigator with limited authority.",
        "files": {
            "state_seed": "state-seed.json",
            "gm_system": "prompts/gm-system.md",
            "authors_note": "prompts/authors-note.md",
            "opening_scene": "prompts/opening-scene.md",
            "world_info": "world-info/index.md",
        },
    }
    if supported_modes:
        manifest["scenario_types"] = {"recommended": supported_modes[0], "supported": supported_modes}
    seed = base_state()
    seed["meta"]["campaign_id"] = pack_id
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (pack_dir / "state-seed.json").write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    (pack_dir / "prompts").mkdir()
    (pack_dir / "prompts" / "gm-system.md").write_text("DEMO_WORLD_SYSTEM_RULE", encoding="utf-8")
    (pack_dir / "prompts" / "authors-note.md").write_text("DEMO_WORLD_AUTHORS_NOTE", encoding="utf-8")
    (pack_dir / "prompts" / "opening-scene.md").write_text("Rain taps the glass. What do you do?", encoding="utf-8")
    (pack_dir / "world-info").mkdir()
    (pack_dir / "world-info" / "index.md").write_text("# Demo World\n", encoding="utf-8")
    return pack_dir


def create_demo_party(
    c: TestClient,
    title: str = "Demo Party",
    character_name: str = "Mira",
    scenario_type: str = "rp",
) -> dict[str, object]:
    model_id = c.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    character = c.post(
        "/api/player-characters",
        json={"worldpack_id": "demo-world", "name": character_name, "description": "Investigator", "profile": {}},
    ).json()["player_character"]
    return c.post(
        "/api/parties",
        json={
            "title": title,
            "scenario_type": scenario_type,
            "worldpack_id": "demo-world",
            "player_character_id": character["id"],
            "model_profile_id": model_id,
        },
    ).json()["party"]


def login(c: TestClient, username: str = "admin", password: str = "admin-secret") -> dict[str, object]:
    response = c.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_auth_login_allows_two_character_usernames(tmp_path: Path):
    c = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="RP",
        bootstrap_admin_password="rp-secret",
    )
    user = login(c, "rp", "rp-secret")
    assert user["username"] == "rp"

    invalid = TestClient(c.app)
    response = invalid.post("/api/auth/login", json={"username": "r", "password": "rp-secret"})
    assert response.status_code == 401


def test_bootstrap_admin_is_added_when_configured_user_is_missing(tmp_path: Path):
    first = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(first)

    settings = replace(
        first.app.state.settings,
        bootstrap_admin_username="RP",
        bootstrap_admin_password="rp-secret",
    )
    second = TestClient(create_app(settings))
    users = second.app.state.auth_store.list_users()

    assert {user.username for user in users} == {"admin", "rp"}
    assert login(second, "rp", "rp-secret")["role"] == "admin"


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
            "scenario_type": "rp",
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


def test_party_requires_manual_scenario_type_and_rejects_unsupported_mode(tmp_path: Path):
    write_worldpack(tmp_path, supported_modes=["novel"])
    c = client(tmp_path)
    model_id = c.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    character = c.post(
        "/api/player-characters",
        json={"worldpack_id": "demo-world", "name": "Mira", "description": "Writer", "profile": {}},
    ).json()["player_character"]
    base_payload = {
        "title": "Manual mode",
        "worldpack_id": "demo-world",
        "player_character_id": character["id"],
        "model_profile_id": model_id,
    }

    assert c.post("/api/parties", json=base_payload).status_code == 422
    assert c.post("/api/parties", json={**base_payload, "scenario_type": "rp"}).status_code == 400
    created = c.post("/api/parties", json={**base_payload, "scenario_type": "novel"})
    assert created.status_code == 200
    assert created.json()["party"]["scenario_type"] == "novel"


def test_existing_parties_migrate_to_compatible_scenario_types(tmp_path: Path):
    database = tmp_path / "rp_gateway.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE parties (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                worldpack_id TEXT NOT NULL,
                player_character_id TEXT NOT NULL,
                model_profile_id TEXT NOT NULL,
                state_campaign_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO parties VALUES(
                'old-awareness', 'Old Awareness', 'awareness', 'pc-a', 'model-a',
                'state-a', 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            );
            INSERT INTO parties VALUES(
                'old-rp', 'Old RP', 'demo-world', 'pc-b', 'model-b',
                'state-b', 'active', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'
            );
            """
        )

    client(tmp_path)
    with sqlite3.connect(database) as connection:
        migrated = dict(connection.execute("SELECT id, scenario_type FROM parties").fetchall())

    assert migrated == {"old-awareness": "training", "old-rp": "rp"}


def test_novel_party_has_no_checks_and_loads_world_prompts(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Shared Novel", scenario_type="novel")

    check = c.post(
        f"/api/parties/{party['id']}/checks",
        json={"check_type": "persuasion", "goal": "convince", "difficulty": 10},
        headers={"Authorization": "Bearer test"},
    )
    assert check.status_code == 400

    turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Я задерживаюсь у двери и отвечаю ей тихо."},
        headers={"Authorization": "Bearer test"},
    )
    assert turn.status_code == 200
    state = c.get(f"/api/parties/{party['id']}/state").json()["state"]
    assert state["timeline"][-1]["event"].startswith("novel turn")

    preview = c.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"content": "Продолжить сцену", "source": "current"},
    )
    assert preview.status_code == 200
    payload = preview.json()["preview"]
    assert payload["outcome"]["result"] == "narrative_continuation"
    assert payload["outcome"]["roll"] == 0
    block_ids = {block["id"] for block in payload["blocks"]}
    assert {"world_system_prompt", "world_authors_note"}.issubset(block_ids)


def test_scenario_system_prompts_have_distinct_contracts():
    rp_rules = NarrativeClient(Settings(scenario_type="rp")).scenario_rules()
    novel_rules = NarrativeClient(Settings(scenario_type="novel")).scenario_rules()
    training_rules = NarrativeClient(Settings(scenario_type="training")).scenario_rules()

    assert "D20" in rp_rules
    assert "collaborative novel" in novel_rules
    assert "There are no dice" in novel_rules
    assert "deterministic training" in training_rules
    assert "Do not coach, hint, assess" in training_rules


def test_training_party_is_deterministic_and_disables_manual_checks(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Training", scenario_type="training")

    check = c.post(
        f"/api/parties/{party['id']}/checks",
        json={"check_type": "information", "goal": "inspect", "difficulty": 10},
        headers={"Authorization": "Bearer test"},
    )
    assert check.status_code == 400
    preview = c.post(f"/api/parties/{party['id']}/prompt/preview", json={"content": "Проверяю факты"})
    assert preview.status_code == 200
    outcome = preview.json()["preview"]["outcome"]
    assert outcome["result"] == "deterministic_resolution"
    assert outcome["roll"] == 0


def test_auth_required_and_parties_are_user_scoped(tmp_path: Path):
    write_worldpack(tmp_path)
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    assert admin.get("/api/parties").status_code == 401
    login(admin)

    created = admin.post(
        "/api/admin/users",
        json={"username": "alice", "password": "alice-secret", "role": "user"},
    )
    assert created.status_code == 200, created.text

    admin_party = create_demo_party(admin, title="Admin Party", character_name="Admin Hero")
    alice = TestClient(admin.app)
    login(alice, "alice", "alice-secret")
    assert alice.get("/api/parties").json()["parties"] == []
    assert alice.get(f"/api/parties/{admin_party['id']}").status_code == 404

    alice_party = create_demo_party(alice, title="Alice Party", character_name="Alice Hero")
    alice_parties = alice.get("/api/parties").json()["parties"]
    assert [party["id"] for party in alice_parties] == [alice_party["id"]]
    assert admin.get("/api/parties").json()["parties"][0]["id"] == admin_party["id"]

    users = admin.get("/api/admin/users").json()["users"]
    alice_summary = next(user for user in users if user["username"] == "alice")
    assert alice_summary["party_count"] == 1


def test_admin_user_lifecycle_and_data_delete(tmp_path: Path):
    write_worldpack(tmp_path)
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    user = admin.post(
        "/api/admin/users",
        json={"username": "bob", "password": "bob-secret", "role": "user"},
    ).json()["user"]
    bob = TestClient(admin.app)
    login(bob, "bob", "bob-secret")
    party = create_demo_party(bob, title="Bob Party", character_name="Bob Hero")

    assert admin.patch(f"/api/admin/users/{user['id']}/password", json={"password": "next-secret"}).status_code == 200
    assert bob.get("/api/parties").status_code == 401
    login(bob, "bob", "next-secret")
    assert bob.get("/api/parties").json()["parties"][0]["id"] == party["id"]

    assert admin.patch(f"/api/admin/users/{user['id']}/status", json={"status": "disabled"}).status_code == 200
    disabled = TestClient(admin.app)
    assert disabled.post("/api/auth/login", json={"username": "bob", "password": "next-secret"}).status_code == 401
    assert admin.patch(f"/api/admin/users/{user['id']}/status", json={"status": "active"}).status_code == 200

    deleted = admin.request("DELETE", f"/api/admin/users/{user['id']}", json={"delete_data": True})
    assert deleted.status_code == 200, deleted.text
    assert admin.get("/api/admin/users").json()["users"][0]["username"] == "admin"
    assert admin.app.state.party_store.list_parties(owner_user_id=user["id"]) == []


def test_admin_autotest_runs_isolated_llm_player_with_separate_local_model(tmp_path: Path):
    write_worldpack(tmp_path)
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
        local_llm_enabled=True,
        local_llm_base_url="mock://success",
        local_llm_model_alias="gemma-4-26b-a4b-it-rp-q4",
        local_llm_context_tokens=32_768,
    )
    assert admin.get("/api/admin/autotests").status_code == 401
    login(admin)
    source_party = create_demo_party(admin, title="Autotest source")

    profiles_response = admin.get("/api/admin/autotests/models")
    assert profiles_response.status_code == 200
    profiles = profiles_response.json()["model_profiles"]
    local_profile = next(profile for profile in profiles if profile["provider"] == "local")
    assert {profile["provider"] for profile in profiles} <= {"local", "openrouter"}

    rejected = admin.post(
        "/api/admin/autotests",
        json={
            "source_party_id": source_party["id"],
            "player_prompt": "Act as a cautious investigator.",
            "turn_count": 31,
            "player_model_profile_id": local_profile["id"],
        },
    )
    assert rejected.status_code == 422

    started = admin.post(
        "/api/admin/autotests",
        json={
            "source_party_id": source_party["id"],
            "player_prompt": "Act as a cautious investigator and respond only with the next action.",
            "turn_count": 1,
            "player_model_profile_id": local_profile["id"],
        },
    )
    assert started.status_code == 200, started.text
    run = started.json()["run"]
    test_party = started.json()["test_party"]
    assert test_party["id"] != source_party["id"]
    assert test_party["model_profile_id"] == source_party["model_profile_id"]
    assert run["player_model_profile_id"] == local_profile["id"]

    deadline = time.time() + 3
    while time.time() < deadline:
        runs = admin.get("/api/admin/autotests").json()["runs"]
        run = next(item for item in runs if item["id"] == run["id"])
        if run["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert run["status"] == "completed", run
    assert run["completed_turns"] == 1
    assert admin.get(f"/api/parties/{source_party['id']}/history").json()["turns"] == []
    test_history = admin.get(f"/api/parties/{test_party['id']}/history").json()["turns"]
    assert len(test_history) == 2
    assert test_history[-1]["player_message"].startswith("I examine the situation")


def test_managed_provider_api_key_used_without_authorization_header(tmp_path: Path):
    c = client(
        tmp_path,
        api_key="",
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(c)
    response = c.post(
        "/api/admin/api-keys",
        json={"label": "Test NVIDIA", "api_key": "managed-provider-key", "provider": "nvidia", "is_default": True},
    )
    assert response.status_code == 200, response.text
    key = response.json()["api_key"]
    assert "api_key" not in key
    assert key["secret_hint"] == "-key"

    completion = c.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Idempotency-Key": "managed-key"},
    )
    assert completion.status_code == 200, completion.text
    assert completion.json()["choices"][0]["message"]["content"]


def test_party_start_endpoint_is_idempotent_and_party_isolated(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    first = create_demo_party(c, title="Opening A", character_name="A")
    second = create_demo_party(c, title="Opening B", character_name="B")

    before = c.get(f"/api/parties/{first['id']}/state").json()["state"]
    started = c.post(
        f"/api/parties/{first['id']}/start",
        json={"idempotency_key": "start-once"},
        headers={"Authorization": "Bearer test"},
    )
    assert started.status_code == 200
    body = started.json()
    assert body["started"] is True
    assert body["already_started"] is False
    assert body["message"]["role"] == "assistant"

    after = c.get(f"/api/parties/{first['id']}/state").json()["state"]
    assert after["meta"]["state_version"] == before["meta"]["state_version"]
    assert after["meta"]["turn"] == before["meta"]["turn"]

    first_history = c.get(f"/api/parties/{first['id']}/history").json()["turns"]
    assert len(first_history) == 1
    assert first_history[0]["player_message"] == "[AUTO_START] Старт партии"

    repeated = c.post(
        f"/api/parties/{first['id']}/start",
        json={"idempotency_key": "start-once"},
        headers={"Authorization": "Bearer test"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["already_started"] is True
    assert len(c.get(f"/api/parties/{first['id']}/history").json()["turns"]) == 1
    assert c.get(f"/api/parties/{second['id']}/history").json()["turns"] == []


def test_party_start_reports_running_idempotency_request(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Opening Pending")
    party_store = c.app.state.party_store
    party_state_store = party_store.store_for_party(party["id"])
    party_state_store.begin_turn_request("start-pending", "req_start_pending")

    response = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "start-pending"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_start_pending"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "running"
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == []


def test_model_profiles_include_rp_descriptions(tmp_path: Path):
    c = client(tmp_path)
    models = c.get("/api/model-profiles").json()["model_profiles"]
    assert len(models) >= 3
    assert models[0]["model"] == "z-ai/glm-5.2"
    assert models[0]["rp_fit"]
    assert models[0]["context_window"]
    assert "reasoning" in models[0]["tags"]


def test_model_profiles_are_grouped_by_supported_providers_and_filter_small_models(tmp_path: Path):
    c = client(tmp_path)
    models = c.get("/api/model-profiles").json()["model_profiles"]

    assert {model["provider"] for model in models} == {"nvidia"}
    assert all("1M" in model["context_window"] for model in models)
    assert not any(model["model"] == "openai/gpt-oss-20b" for model in models)


def test_small_context_local_vulkan_profile_is_not_selectable(tmp_path: Path):
    c = client(
        tmp_path,
        api_key="",
        local_llm_enabled=True,
        local_llm_base_url="mock://success",
        local_llm_model_alias="gemma-4-26b-a4b-it-rp-q4",
    )

    models = c.get("/api/model-profiles").json()["model_profiles"]
    assert not any(model["provider"] == "local" for model in models)


def test_openrouter_rp_specialists_bypass_generic_size_filter_but_gpt_oss_20b_does_not():
    specialist = {
        "description": "A multi-model roleplaying and storytelling system.",
        "context_length": 131072,
        "architecture": {"output_modalities": ["text"]},
    }
    generic_small = {
        "description": "A small general-purpose reasoning model.",
        "context_length": 131072,
        "architecture": {"output_modalities": ["text"]},
    }

    assert provider_model_is_suitable("openrouter", "aion-labs/aion-3.0-mini", specialist) is True
    assert provider_model_is_suitable("openrouter", "openai/gpt-oss-20b", generic_small) is False
    assert prices_are_free("0", "0.000000") is True


def test_featured_openrouter_models_enrich_cached_catalog_profiles():
    params = {"tags": ["live OpenRouter"], "rp_fit": "stale description"}

    enriched = enrich_openrouter_profile_params("z-ai/glm-5.2", params)

    assert len(OPENROUTER_FEATURED_MODELS) == 10
    assert params == {"tags": ["live OpenRouter"], "rp_fit": "stale description"}
    assert enriched["featured_rank"] == 1
    assert enriched["title_override"] == "GLM 5.2"
    assert enriched["rp_fit"] != "stale description"
    assert enriched["tags"][:2] == ["Избранное", "длинная кампания"]


def test_non_nvidia_party_uses_selected_provider_without_nvidia_model_fallbacks():
    settings = Settings(
        nvidia_fallback_models=("deepseek-ai/deepseek-v4-pro",),
        nvidia_disabled_models=("openai/gpt-oss-20b",),
    )
    party = SimpleNamespace(
        scenario_type="rp",
        worldpack_id="demo-world",
        worldpack=None,
        model_profile=SimpleNamespace(
            provider="gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            model="gemini-3.6-flash",
        ),
    )

    selected = settings_for_party(settings, party)

    assert selected.llm_provider == "gemini"
    assert selected.narrative_model == "gemini-3.6-flash"
    assert selected.nvidia_fallback_models == ()
    assert selected.nvidia_disabled_models == ()


def test_default_memory_policy_is_tuned_for_long_context(monkeypatch: pytest.MonkeyPatch):
    for name in [
        "PARTY_CONTEXT_MAX_TOKENS",
        "PARTY_CONTEXT_LIMIT_TOKENS",
        "PARTY_CONTEXT_COMPLETION_RESERVE_TOKENS",
        "PARTY_CONTEXT_SYSTEM_RESERVE_TOKENS",
        "PARTY_CONTEXT_MIN_HISTORY_TOKENS",
        "MEMORY_SUMMARY_BATCH_TOKENS",
        "PARTY_MEMORY_CHAPTER_MAX_TOKENS",
        "PARTY_MEMORY_CHAPTER_MAX_CHARS",
        "PARTY_MEMORY_PROMPT_MAX_CHARS",
        "PARTY_MEMORY_RETRIEVAL_ENABLED",
        "PARTY_MEMORY_RETRIEVAL_LIMIT",
        "PARTY_MEMORY_RETRIEVAL_MAX_CHARS",
        "JOURNAL_AUTO_MIN_UNSUMMARIZED_TURNS",
        "JOURNAL_MAX_BATCH_TURNS",
        "POST_TURN_HELPERS_INLINE",
    ]:
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.post_turn_helpers_inline is False
    assert settings.party_context_max_tokens == 131_072
    assert settings.effective_party_context_limit_tokens == 131_072
    assert settings.effective_party_history_token_budget == 81_920
    assert settings.memory_summary_batch_tokens == 10_000
    assert settings.memory_llm_provider == "local"
    assert settings.party_memory_chapter_max_tokens == 6_000
    assert settings.party_memory_chapter_max_chars == 24_000
    assert settings.party_memory_prompt_max_chars == 60_000
    assert settings.party_memory_retrieval_enabled is True
    assert settings.journal_auto_min_unsummarized_turns == 24
    assert settings.journal_max_batch_turns == 48


def test_context_overflow_is_omitted_until_episodic_chapter_catches_up(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(
        tmp_path,
        party_context_max_tokens=512,
        party_context_completion_reserve_tokens=128,
        party_context_system_reserve_tokens=256,
        party_context_min_history_tokens=64,
        memory_summary_batch_tokens=2_048,
    )
    party = create_demo_party(c, title="Token Overflow")
    store = c.app.state.party_store.store_for_party(party["id"])
    old_player = "old-player-" + ("x" * 150)
    old_gm = "old-gm-" + ("y" * 150)
    recent_player = "recent-player-" + ("z" * 150)
    recent_gm = "recent-gm-" + ("w" * 150)
    store.record_turn("overflow-1", "overflow-1", old_player, old_gm, {}, 1)
    store.record_turn("overflow-2", "overflow-2", recent_player, recent_gm, {}, 2)

    request = party_chat_request(
        store,
        "z-ai/glm-5.2",
        PartyMessageRequest(content="next action"),
        c.app.state.settings,
    )
    prompt_text = "\n".join(str(message.content) for message in request.messages)
    assert "UNCOMPACTED_ARCHIVE_FALLBACK" in prompt_text
    assert old_player in prompt_text
    assert recent_player in prompt_text

    plan, reason = MemorySummarizer(c.app.state.settings, store).build_plan()
    assert reason == "ready"
    assert plan is not None
    assert [turn["id"] for turn in plan.turns] == [1]

    # A durable chapter replaces its covered raw turn even if a later model
    # profile has a larger context window.
    store.record_memory_chapter(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        summary_text="old event retained in memory",
        key_facts=["old event"],
        open_threads=[],
        relationship_changes=[],
        player_promises=[],
        npc_obligations=[],
        model="mock",
    )
    covered_request = party_chat_request(
        store,
        "z-ai/glm-5.2",
        PartyMessageRequest(content="next action"),
        c.app.state.settings,
    )
    covered_prompt_text = "\n".join(str(message.content) for message in covered_request.messages)
    assert old_player not in covered_prompt_text
    assert recent_player in covered_prompt_text

    rebuilt_plan, rebuilt_reason = MemorySummarizer(c.app.state.settings, store).build_plan(force=True)
    assert rebuilt_reason == "ready"
    assert rebuilt_plan is not None
    assert rebuilt_plan.previous_memory is None
    assert [turn["id"] for turn in rebuilt_plan.turns] == [2]


def test_episodic_chapters_are_immutable_and_archive_retrieval_stays_out_of_raw_tail(tmp_path: Path):
    store = StateStore(str(tmp_path / "chapters.db"), "chapter-test", str(tmp_path / "state.json"))
    store.record_turn("chapter-1", "chapter-1", "Мира нашла серебряный ключ", "Ключ отдан Мире", {}, 1)
    store.record_turn("chapter-2", "chapter-2", "Мира вошла в обсерваторию", "В обсерватории холодно", {}, 2)
    store.record_turn("chapter-3", "chapter-3", "Игрок ждёт у ворот", "Ворота закрыты", {}, 3)
    first = store.record_memory_chapter(1, 1, 1, "Глава: Мира и серебряный ключ", [], [], [], [], [], "mock")
    second = store.record_memory_chapter(2, 2, 2, "Глава: обсерватория", [], [], [], [], [], "mock")

    assert [chapter["id"] for chapter in store.memory_for_prompt(100_000)] == [first["id"], second["id"]]
    assert store.latest_memory_coverage()["to_turn_id"] == 2
    retrieved = store.search_archived_turns("Где Мира и ключ", through_turn_id=2)
    assert [turn["id"] for turn in retrieved] == [1, 2]


def test_hybrid_archive_retrieval_eval_set(tmp_path: Path):
    fixture_path = Path(__file__).parent / "fixtures" / "memory_retrieval_eval.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    store = StateStore(str(tmp_path / "retrieval-eval.db"), "retrieval-eval", str(tmp_path / "state.json"))
    for index, turn in enumerate(fixture["turns"], start=1):
        store.record_turn(f"eval-{index}", f"eval-{index}", turn["player"], turn["narrative"], {}, index)

    for case in fixture["cases"]:
        matches = store.explain_archived_retrieval(case["query"], through_turn_id=len(fixture["turns"]), limit=3)
        first_turn = matches[0]["id"] if matches else None
        assert first_turn == case["expected_first_turn"], case["query"]


def test_party_uses_visible_raw_fallback_when_service_memory_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def fail_summary(*_args, **_kwargs):
        raise RuntimeError("summary unavailable")

    monkeypatch.setattr(MemorySummarizer, "generate", fail_summary)
    write_worldpack(tmp_path)
    c = client(
        tmp_path,
        party_context_max_tokens=512,
        party_context_completion_reserve_tokens=128,
        party_context_system_reserve_tokens=256,
        party_context_min_history_tokens=64,
        memory_summary_batch_tokens=2_048,
    )
    party = create_demo_party(c, title="Memory must not drop context")
    store = c.app.state.party_store.store_for_party(party["id"])
    store.record_turn("memory-failure-1", "memory-failure-1", "old-" + ("x" * 150), "old-" + ("y" * 150), {}, 1)
    store.record_turn("memory-failure-2", "memory-failure-2", "recent-" + ("z" * 150), "recent-" + ("w" * 150), {}, 2)

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "continue", "idempotency_key": "memory-failure-turn"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_memory_failure"},
    )

    assert response.status_code == 200
    assert [turn["id"] for turn in store.turn_history(limit=10)] == [1, 2, 3]
    preview = c.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"content": "continue", "source": "current"},
    ).json()["preview"]
    assert preview["inspection"]["fallback"]["active"] is True
    assert preview["inspection"]["fallback"]["turn_ids"]
    memory_job = next(job for job in store.service_jobs() if job["job_type"] == "memory")
    assert memory_job["status"] == "pending"
    assert memory_job["attempts"] == 1


def test_post_turn_helpers_can_run_without_blocking_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def scenario() -> None:
        settings = Settings(
            app_env="test",
            database_url=f"sqlite:///{tmp_path / 'helpers.db'}",
            world_state_path=str(tmp_path / "state.json"),
            nvidia_api_base="mock://success",
            post_turn_helpers_inline=False,
        )
        store = StateStore(settings.sqlite_path, settings.campaign_id, settings.world_state_path)
        Adjudicator._post_turn_helper_campaigns.discard(store.campaign_id)
        adjudicator = Adjudicator(settings, store)
        started = asyncio.Event()
        finished = asyncio.Event()

        async def slow_helpers(authorization: str | None, wait_for_retries: bool) -> None:
            _ = authorization, wait_for_retries
            started.set()
            await asyncio.sleep(0.1)
            finished.set()

        monkeypatch.setattr(adjudicator, "drain_service_jobs", slow_helpers)
        before = time.perf_counter()
        await adjudicator.after_turn_recorded("Bearer test", "background-helper")
        elapsed = time.perf_counter() - before

        assert elapsed < 0.05
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not finished.is_set()
        await asyncio.wait_for(finished.wait(), timeout=1)
        await asyncio.sleep(0)
        assert store.campaign_id not in Adjudicator._post_turn_helper_campaigns

    asyncio.run(scenario())


def test_service_jobs_are_durable_and_running_jobs_resume_after_restart(tmp_path: Path):
    sqlite_path = str(tmp_path / "durable-jobs.db")
    state_path = str(tmp_path / "state.json")
    store = StateStore(sqlite_path, "durable-jobs", state_path)
    queued = store.enqueue_service_job("memory", "request-1", max_attempts=3)
    running = store.mark_service_job_running(queued["id"])
    assert running["status"] == "running"
    assert running["attempts"] == 1

    restarted = StateStore(sqlite_path, "durable-jobs", state_path)
    recovery = restarted.recover_interrupted_work()
    resumed = restarted.due_service_job()
    assert resumed is not None
    assert resumed["id"] == queued["id"]
    assert resumed["status"] == "pending"
    assert recovery["resumed_jobs"] == 1


def test_interrupted_turn_requests_are_reconciled_on_restart(tmp_path: Path):
    sqlite_path = str(tmp_path / "interrupted-turns.db")
    state_path = str(tmp_path / "state.json")
    store = StateStore(sqlite_path, "interrupted-turns", state_path)
    store.begin_turn_request("lost-request", "req-lost")
    store.begin_turn_request("saved-request", "req-saved")
    store.record_turn(
        "saved-request",
        "req-saved",
        "player",
        "saved response",
        {"message": {"content": "saved response"}},
        1,
    )

    recovery = store.recover_interrupted_work()

    assert recovery == {"completed_requests": 1, "failed_requests": 1, "resumed_jobs": 0}
    assert store.get_turn_request("req-saved")["status"] == "completed"
    assert store.get_turn_request("req-saved")["response"]["message"]["content"] == "saved response"
    assert store.get_turn_request("req-lost")["status"] == "failed"
    assert "restarted" in store.get_turn_request("req-lost")["error"]


def test_party_model_can_be_changed(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    models = c.get("/api/model-profiles").json()["model_profiles"]
    character = c.post(
        "/api/player-characters",
        json={"worldpack_id": "demo-world", "name": "Mira", "description": "Investigator", "profile": {}},
    ).json()["player_character"]
    party = c.post(
        "/api/parties",
        json={
            "title": "Model Switch",
            "scenario_type": "rp",
            "worldpack_id": "demo-world",
            "player_character_id": character["id"],
            "model_profile_id": models[0]["id"],
        },
    ).json()["party"]

    changed = c.patch(
        f"/api/parties/{party['id']}/model",
        json={"model_profile_id": models[1]["id"]},
    )
    assert changed.status_code == 200
    assert changed.json()["party"]["model_profile_id"] == models[1]["id"]
    assert changed.json()["party"]["model_profile"]["model"] == models[1]["model"]


def test_party_context_estimate_reports_usage_and_history_window(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    models = c.get("/api/model-profiles").json()["model_profiles"]
    character = c.post(
        "/api/player-characters",
        json={"worldpack_id": "demo-world", "name": "Mira", "description": "Investigator", "profile": {}},
    ).json()["player_character"]
    party = c.post(
        "/api/parties",
        json={
            "title": "Context Estimate",
            "scenario_type": "rp",
            "worldpack_id": "demo-world",
            "player_character_id": character["id"],
            "model_profile_id": models[0]["id"],
        },
    ).json()["party"]

    for index in range(7):
        response = c.post(
            f"/api/parties/{party['id']}/messages",
            json={"content": f'/check information skill=1 difficulty=5 goal="clue {index}"'},
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200

    estimate = c.get(f"/api/parties/{party['id']}/context").json()["context"]
    assert estimate["context_limit_tokens"] == 131_072
    assert estimate["estimated_prompt_tokens"] > 0
    assert estimate["estimated_total_tokens"] >= estimate["estimated_prompt_tokens"]
    assert estimate["history_turns_total"] == 7
    assert estimate["history_source_turn_limit"] is None
    assert estimate["message_prompt_limit"] is None
    assert estimate["history_token_budget"] == 81_920
    assert estimate["prompt_source"] == "recorded_last_turn"
    assert estimate["direct_history_messages"] == 12
    assert estimate["history_limited"] is False
    assert estimate["omitted_history_turns_estimate"] == 0
    assert estimate["memory_summary_tokens"] == 0


def test_party_prompt_preview_returns_blocks_without_mutating_state(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Prompt Preview")

    first_turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": '/check information skill=1 difficulty=5 goal="find the old map"',
            "idempotency_key": "prompt-preview-history",
        },
        headers={"Authorization": "Bearer test"},
    )
    assert first_turn.status_code == 200

    before = c.get(f"/api/parties/{party['id']}/state").json()["state"]
    before_version = before["meta"]["state_version"]
    preview = c.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"content": '/check persuasion target=advisor skill=1 difficulty=8 goal="borrow the map"', "source": "current"},
    )
    assert preview.status_code == 200
    body = preview.json()["preview"]
    assert body["dry_run"] is True
    assert body["mutation"] == "none"
    block_ids = {block["id"] for block in body["blocks"]}
    assert {"system_rules", "state_summary", "authoritative_outcome", "raw_turns"}.issubset(block_ids)
    assert body["estimated_prompt_tokens"] > 0

    after = c.get(f"/api/parties/{party['id']}/state").json()["state"]
    assert after["meta"]["state_version"] == before_version
    assert len(c.get(f"/api/parties/{party['id']}/history").json()["turns"]) == 1


def test_party_prompt_preview_defaults_to_last_recorded_prompt(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Last Prompt")

    turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": '/check information skill=1 difficulty=5 goal="find the old map"'},
        headers={"Authorization": "Bearer test"},
    )
    assert turn.status_code == 200

    preview = c.post(f"/api/parties/{party['id']}/prompt/preview", json={})
    assert preview.status_code == 200
    body = preview.json()["preview"]
    assert body["source"] == "recorded_last_turn"
    assert body["dry_run"] is False
    assert body["input"] == '/check information skill=1 difficulty=5 goal="find the old map"'
    assert body["estimated_prompt_tokens"] > 0


def test_party_prompt_preview_explains_chapter_and_archive_retrieval(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Inspectable Memory")
    store = c.app.state.party_store.store_for_party(party["id"])
    store.record_turn("memory-inspector-1", "memory-inspector-1", "Мира нашла астролябию", "Астролябия осталась у Миры", {}, 1)
    store.record_memory_chapter(
        from_turn_id=1,
        to_turn_id=1,
        state_version=1,
        summary_text="Мира нашла астролябию.",
        key_facts=[],
        open_threads=[],
        relationship_changes=[],
        player_promises=[],
        npc_obligations=[],
        model="mock",
    )

    preview = c.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"content": "Где астролябия?", "source": "current"},
    )

    assert preview.status_code == 200
    body = preview.json()["preview"]
    inspection = body["inspection"]
    assert inspection["chapters"]["included"][0]["from_turn_id"] == 1
    assert inspection["raw"]["included_turn_ids"] == []
    assert inspection["retrieval"][0]["turn_id"] == 1
    assert inspection["retrieval"][0]["lexical_score"] == 1
    assert inspection["retrieval"][0]["stem_hits"] >= 1
    assert inspection["retrieval"][0]["match_mode"] == "exact+fuzzy"
    assert inspection["retrieval"][0]["matched_terms"] == ["астролябия"]
    assert "retrieved_archive_scenes" in {block["id"] for block in body["blocks"]}


def test_party_lore_cards_are_manual_retrievable_and_party_isolated(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    first = create_demo_party(c, title="Lore A", character_name="A")
    second = create_demo_party(c, title="Lore B", character_name="B")

    created = c.post(
        f"/api/parties/{first['id']}/lore-cards",
        json={
            "title": "Астролябия Миры",
            "content": "Мира хранит найденную астролябию в синем футляре.",
            "keywords": ["астролябия", "Мира"],
            "always_on": False,
            "enabled": True,
            "source_turn_ids": [1],
        },
    )
    assert created.status_code == 200
    card = created.json()["card"]

    preview = c.post(
        f"/api/parties/{first['id']}/prompt/preview",
        json={"content": "Где астролябия?", "source": "current"},
    ).json()["preview"]
    assert "party_lore_cards" in {block["id"] for block in preview["blocks"]}
    assert "синем футляре" in "\n".join(message["content"] for message in preview["messages"])

    isolated = c.post(
        f"/api/parties/{second['id']}/prompt/preview",
        json={"content": "Где астролябия?", "source": "current"},
    ).json()["preview"]
    assert "party_lore_cards" not in {block["id"] for block in isolated["blocks"]}

    disabled = c.patch(
        f"/api/parties/{first['id']}/lore-cards/{card['id']}",
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    hidden = c.post(
        f"/api/parties/{first['id']}/prompt/preview",
        json={"content": "Где астролябия?", "source": "current"},
    ).json()["preview"]
    assert "party_lore_cards" not in {block["id"] for block in hidden["blocks"]}


def test_memory_checkpoint_is_a_non_destructive_party_snapshot(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Checkpoint")
    turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": '/check information skill=1 difficulty=5 goal="checkpoint clue"'},
        headers={"Authorization": "Bearer test"},
    )
    assert turn.status_code == 200

    created = c.post(f"/api/parties/{party['id']}/checkpoints", json={"label": "После первой улики"})
    assert created.status_code == 200
    checkpoint = created.json()["checkpoint"]
    assert checkpoint["label"] == "После первой улики"
    assert checkpoint["through_turn_id"] == 1
    assert checkpoint["state_version"] >= 1
    assert checkpoint["state"]["meta"]["campaign_id"] == party["id"]
    assert len(c.get(f"/api/parties/{party['id']}/history").json()["turns"]) == 1


def test_party_characters_endpoint_returns_npc_sheets(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Characters", character_name="Mira")

    response = c.get(f"/api/parties/{party['id']}/characters")
    assert response.status_code == 200
    sheets = response.json()["characters"]
    assert sheets["player"]["name"] == "Mira"
    by_id = {character["id"]: character for character in sheets["characters"]}
    assert {"advisor", "king"}.issubset(by_id)
    assert by_id["advisor"]["relationship"]["id"] == "player_advisor"
    assert by_id["king"]["hard_constraints"]
    assert by_id["king"]["location_label"] == "throne room"


def test_party_character_manual_edit_creates_pending_patch(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Character Edit")

    draft = c.post(
        f"/api/parties/{party['id']}/characters/edit",
        json={
            "target": "npc",
            "character_id": "varn",
            "name": "Varn",
            "status": "alive",
            "location": "north gate",
            "current_goal": "watch the player",
            "attitude_to_player": "wary but professional",
            "loyalty": "north-watch",
            "knowledge": "Varn saw the player near the archive.",
            "hard_constraints": "Varn cannot leave the gate without a captain order.",
            "secrets": "Varn hides a forged pass.",
        },
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["applied"] is False
    assert c.get(f"/api/parties/{party['id']}/characters").json()["characters"]["counts"]["characters"] == 2

    applied = c.post(f"/api/parties/{party['id']}/world/apply", json={"proposal_id": "latest", "confirm": True})
    assert applied.status_code == 200, applied.text
    sheets = c.get(f"/api/parties/{party['id']}/characters").json()["characters"]
    by_id = {character["id"]: character for character in sheets["characters"]}
    assert by_id["varn"]["name"] == "Varn"
    assert by_id["varn"]["location"] == "north gate"
    assert by_id["varn"]["attitude_to_player"] == "wary but professional"
    assert by_id["varn"]["loyalty"] == "north-watch"
    assert by_id["varn"]["hard_constraints"] == ["Varn cannot leave the gate without a captain order."]
    assert by_id["varn"]["secrets"] == ["Varn hides a forged pass."]


def test_party_character_manual_edit_can_apply_immediately(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Character Apply")

    response = c.post(
        f"/api/parties/{party['id']}/characters/edit",
        json={
            "target": "npc",
            "name": "Gate Clerk",
            "status": "alive",
            "location": "ledger archive",
            "current_goal": "verify travel papers",
            "confirm": True,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    sheets = c.get(f"/api/parties/{party['id']}/characters").json()["characters"]
    by_id = {character["id"]: character for character in sheets["characters"]}
    assert by_id["gate-clerk"]["name"] == "Gate Clerk"
    assert by_id["gate-clerk"]["current_goal"] == "verify travel papers"


def test_new_npc_without_location_does_not_inherit_player_location(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Character Unknown Location")

    response = c.post(
        f"/api/parties/{party['id']}/characters/edit",
        json={"target": "npc", "name": "Stable Boy", "confirm": True},
    )

    assert response.status_code == 200, response.text
    sheets = c.get(f"/api/parties/{party['id']}/characters").json()["characters"]
    by_id = {character["id"]: character for character in sheets["characters"]}
    assert by_id["stable-boy"]["location"] == "unknown"


def test_party_character_llm_generate_applies_immediately(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Character LLM Generate")

    response = c.post(
        f"/api/parties/{party['id']}/characters/generate",
        json={
            "target": "npc",
            "name": "Трактирщик",
            "location": "таверна",
        },
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["applied"] is True
    assert response.json()["character_id"] == "трактирщик"
    sheets = c.get(f"/api/parties/{party['id']}/characters").json()["characters"]
    by_id = {character["id"]: character for character in sheets["characters"]}
    assert by_id["трактирщик"]["name"] == "Трактирщик"
    assert by_id["трактирщик"]["location"] == "таверна"
    assert by_id["трактирщик"]["current_goal"]
    assert c.get(f"/api/parties/{party['id']}/world/proposals").json()["proposals"] == []


def test_party_character_llm_generate_without_location_uses_unknown(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Character LLM Unknown Location")

    response = c.post(
        f"/api/parties/{party['id']}/characters/generate",
        json={"target": "npc", "name": "Трактирщик"},
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200, response.text
    sheets = c.get(f"/api/parties/{party['id']}/characters").json()["characters"]
    by_id = {character["id"]: character for character in sheets["characters"]}
    assert by_id["трактирщик"]["location"] == "unknown"


def test_party_character_llm_generate_provider_http_error_does_not_apply(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path, mode="http-503")
    party = create_demo_party(c, title="Character LLM Failure")

    response = c.post(
        f"/api/parties/{party['id']}/characters/generate",
        json={"target": "npc", "name": "Трактирщик"},
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Narrative provider HTTP 503"
    sheets = c.get(f"/api/parties/{party['id']}/characters").json()["characters"]
    by_id = {character["id"]: character for character in sheets["characters"]}
    assert "трактирщик" not in by_id


def test_party_memory_auto_summary_is_party_isolated(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(
        tmp_path,
        party_context_max_tokens=512,
        party_context_completion_reserve_tokens=128,
        party_context_system_reserve_tokens=256,
        party_context_min_history_tokens=64,
        memory_summary_batch_tokens=2_048,
    )
    first = create_demo_party(c, title="Memory A", character_name="A")
    second = create_demo_party(c, title="Memory B", character_name="B")

    for index in range(18):
        response = c.post(
            f"/api/parties/{first['id']}/messages",
            json={
                "content": f'/check information skill=1 difficulty=5 goal="old clue {index}"',
                "idempotency_key": f"memory-a-{index}",
            },
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200

    first_history = c.get(f"/api/parties/{first['id']}/history", params={"limit": 50}).json()["turns"]
    assert len(first_history) == 18

    first_memory = c.get(f"/api/parties/{first['id']}/memory").json()
    assert first_memory["memory"] is not None
    assert first_memory["memory"]["to_turn_id"] < 18
    assert first_memory["chapters"][0]["from_turn_id"] == 1
    assert "old clue 0" in first_memory["chapters"][0]["summary_text"]
    assert all(
        current["from_turn_id"] == previous["to_turn_id"] + 1
        for previous, current in zip(first_memory["chapters"], first_memory["chapters"][1:])
    )

    second_memory = c.get(f"/api/parties/{second['id']}/memory").json()
    assert second_memory["memory"] is None
    assert second_memory["stats"]["total_turns"] == 0


def test_party_journal_auto_summary_is_party_isolated(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(
        tmp_path,
        journal_auto_min_unsummarized_turns=6,
        journal_max_batch_turns=18,
    )
    first = create_demo_party(c, title="Journal A", character_name="A")
    second = create_demo_party(c, title="Journal B", character_name="B")

    for index in range(6):
        response = c.post(
            f"/api/parties/{first['id']}/messages",
            json={
                "content": f'/check information skill=1 difficulty=5 goal="journal clue {index}"',
                "idempotency_key": f"journal-a-{index}",
            },
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200

    first_journal = c.get(f"/api/parties/{first['id']}/journal").json()
    assert first_journal["journal"] is not None
    assert first_journal["journal"]["from_turn_id"] == 1
    assert first_journal["journal"]["to_turn_id"] == 6
    assert "journal clue 0" in first_journal["journal"]["recap_text"]

    second_journal = c.get(f"/api/parties/{second['id']}/journal").json()
    assert second_journal["journal"] is None
    assert second_journal["stats"]["total_turns"] == 0


def test_party_memory_manual_summarize_and_clear_latest(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(
        tmp_path,
        party_context_max_tokens=512,
        party_context_completion_reserve_tokens=128,
        party_context_system_reserve_tokens=256,
        party_context_min_history_tokens=64,
        memory_summary_batch_tokens=2_048,
    )
    party = create_demo_party(c, title="Manual Memory")

    for index in range(9):
        response = c.post(
            f"/api/parties/{party['id']}/messages",
            json={
                "content": f'/check information skill=1 difficulty=5 goal="manual clue {index}"',
                "idempotency_key": f"memory-manual-{index}",
            },
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200

    before = c.get(f"/api/parties/{party['id']}/memory").json()
    assert before["memory"] is not None
    assert before["stats"]["history_token_budget"] == 128

    generated = c.post(
        f"/api/parties/{party['id']}/memory/summarize",
        json={"force": True},
        headers={"Authorization": "Bearer test"},
    )
    assert generated.status_code == 200
    assert generated.json()["generated"] is True
    assert generated.json()["memory"]["from_turn_id"] == before["memory"]["to_turn_id"] + 1

    deleted = c.delete(f"/api/parties/{party['id']}/memory/latest")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["memory"] is not None
    assert deleted.json()["memory"]["to_turn_id"] == before["memory"]["to_turn_id"]


def test_party_journal_manual_summarize_and_clear_latest(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Manual Journal")

    for index in range(2):
        response = c.post(
            f"/api/parties/{party['id']}/messages",
            json={
                "content": f'/check information skill=1 difficulty=5 goal="manual journal {index}"',
                "idempotency_key": f"journal-manual-{index}",
            },
            headers={"Authorization": "Bearer test"},
        )
        assert response.status_code == 200

    before = c.get(f"/api/parties/{party['id']}/journal").json()
    assert before["journal"] is None

    generated = c.post(
        f"/api/parties/{party['id']}/journal/summarize",
        json={"force": True},
        headers={"Authorization": "Bearer test"},
    )
    assert generated.status_code == 200
    assert generated.json()["generated"] is True
    assert generated.json()["journal"]["to_turn_id"] == 2

    deleted = c.delete(f"/api/parties/{party['id']}/journal/latest")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["journal"] is None


def test_narrative_prompt_includes_long_term_party_memory():
    outcome = Outcome(
        check_id="memory-prompt",
        action_type="feasibility",
        actor="player",
        result="partial_success",
        roll=10,
        difficulty=10,
        modifiers={},
        final_score=10,
        consequences=["The fixed outcome remains authoritative."],
        authoritative_block="AUTHORITATIVE_OUTCOME: partial success.",
    )
    memory = {
        "from_turn_id": 1,
        "to_turn_id": 10,
        "state_version": 12,
        "summary_text": "The old tower burned, and Mira promised to return.",
        "key_facts": ["The old tower burned."],
        "open_threads": ["Who lit the fire remains unresolved."],
        "relationship_changes": [],
        "player_promises": ["Mira promised to return."],
        "npc_obligations": [],
    }
    request = ChatCompletionRequest(
        model="z-ai/glm-5.2",
        messages=[ChatMessage(role="user", content="I inspect the ashes.")],
    )

    messages = NarrativeClient(Settings(nvidia_api_base="mock://success")).narrative_messages(
        request,
        base_state(),
        outcome,
        repair_instruction=None,
        memory_summary=memory,
    )

    memory_blocks = [message["content"] for message in messages if "LONG_TERM_PARTY_MEMORY" in message["content"]]
    assert len(memory_blocks) == 1
    assert "The old tower burned" in memory_blocks[0]
    assert "AUTHORITATIVE_OUTCOME" in "\n".join(message["content"] for message in messages)


def test_narrative_prompt_retrieves_only_relevant_character_state():
    state = base_state()
    state["characters"]["archivist"] = {  # type: ignore[index]
        "name": "Archivist",
        "status": "alive",
        "location": "sealed_archive",
        "current_goal": "protect the forbidden index",
        "secrets": ["The archive door is already open."],
    }
    state["relationships"]["player_archivist"] = {  # type: ignore[index]
        "from": "player",
        "to": "archivist",
        "trust": 0,
        "notes": ["They met years ago."],
    }
    outcome = Outcome(
        check_id="character-retrieval",
        action_type="feasibility",
        actor="player",
        result="partial_success",
        roll=10,
        difficulty=10,
        modifiers={},
        final_score=10,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: partial success.",
    )
    request = ChatCompletionRequest(
        model="z-ai/glm-5.2",
        messages=[ChatMessage(role="user", content="I ask the king about the missing envoy.")],
    )

    messages = NarrativeClient(Settings(nvidia_api_base="mock://success")).narrative_messages(
        request,
        state,
        outcome,
        repair_instruction=None,
    )

    character_blocks = [message["content"] for message in messages if message["content"].startswith("RELEVANT_CHARACTERS")]
    assert len(character_blocks) == 1
    character_block = character_blocks[0]
    assert '"id": "king"' in character_block
    assert '"id": "advisor"' in character_block
    assert "archivist" not in character_block
    assert "archive door" not in character_block
    state_summary = next(message["content"] for message in messages if message["content"].startswith("Relevant state summary:"))
    assert "player_king" in state_summary
    assert "player_advisor" in state_summary
    assert "player_archivist" not in state_summary


def test_build_catalog_parser_discards_non_rp_models():
    html = """
    <a href="/meta/llama-3_3-70b-instruct">Llama</a>
    <a href="/nvidia/llama-3_1-nemoguard-8b-content-safety">Guard</a>
    <a href="/qwen/qwen3.5-122b-a10b">Qwen</a>
    """
    assert parse_build_catalog(html) == ["meta/llama-3.3-70b-instruct", "qwen/qwen3.5-122b-a10b"]


def test_prompt_world_party_and_delete(tmp_path: Path):
    c = client(tmp_path)
    world = c.post(
        "/api/worldpacks/prompt",
        json={
            "title": "Город под стеклянным дождем",
            "prompt": "Неонуарный город, где дождь хранит воспоминания. Игрок расследует пропажу архивариуса.",
        },
    )
    assert world.status_code == 200
    pack = world.json()["worldpack"]
    assert pack["id"].startswith("prompt-")
    assert pack["status"] == "playable"

    model_id = c.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    character = c.post(
        "/api/player-characters",
        json={
            "worldpack_id": pack["id"],
            "name": "Ника",
            "description": "Бывшая городская медиаторка, умеет читать следы памяти в стеклянном дожде.",
            "profile": {"source": "prompt"},
        },
    ).json()["player_character"]
    party = c.post(
        "/api/parties",
        json={
            "title": "Стеклянный дождь",
            "scenario_type": "rp",
            "worldpack_id": pack["id"],
            "player_character_id": character["id"],
            "model_profile_id": model_id,
        },
    ).json()["party"]
    state = c.get(f"/api/parties/{party['id']}/state").json()["state"]
    assert any(item["id"] == "world_prompt" for item in state["world_constraints"])
    assert state["player"]["name"] == "Ника"
    assert "стеклянном дожде" in state["player"]["description"]
    turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "/check information skill=1 difficulty=5 goal=\"inspect the rain\""},
        headers={"Authorization": "Bearer test"},
    )
    assert turn.status_code == 200

    deleted = c.delete(f"/api/parties/{party['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert c.get(f"/api/parties/{party['id']}").status_code == 404
    assert not (tmp_path / "state" / "parties" / party["id"]).exists()


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
                "scenario_type": "rp",
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
    assert_no_gateway_service_text(content)
    assert "transfers command authority" not in content


def test_awareness_attachment_fallback_is_stealthy():
    state = base_state()
    state["meta"]["campaign_id"] = "awareness"  # type: ignore[index]
    state["player"]["resources"]["current-turn-window"] = "ход 1, понедельник, 10:00-14:00"  # type: ignore[index]
    outcome = Outcome(
        check_id="awareness-file",
        action_type="feasibility",
        actor="player",
        result="partial_success",
        roll=10,
        difficulty=10,
        modifiers={},
        final_score=4,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: partial success.",
    )

    text = safe_fallback(outcome, state, "Открываю WorkSchedule_Update.xlsx.exe", scenario_type="training")

    assert "Твой ход" not in text
    assert "ценой" not in text.lower()
    assert "цену" not in text.lower()
    assert "вирус" not in text.lower()
    assert "подозр" not in text.lower()
    assert "без заметных" in text


def test_awareness_double_extension_action_updates_score_counters():
    state = base_state()
    state["meta"]["campaign_id"] = "awareness"  # type: ignore[index]
    state["player"]["resources"]["suspicious-artifacts-opened"] = 0  # type: ignore[index]
    state["player"]["resources"]["unsafe-actions"] = 0  # type: ignore[index]
    intent = Intent(
        action_type="feasibility",
        desired_outcome="Открываю WorkSchedule_Update.xlsx.exe и продолжаю работу",
        methods=["free_text"],
    )

    outcome, patch = RuleEngine().resolve(state, intent, "awareness-file", campaign_id="awareness", scenario_type="training")
    values = {operation.path: operation.value for operation in patch.patch}

    assert values["/player/resources/suspicious-artifacts-opened"] == 1
    assert values["/player/resources/unsafe-actions"] == 1
    assert outcome.result == "deterministic_resolution"
    assert outcome.roll == 0


def test_awareness_training_scores_reports_and_credential_exposure_deterministically():
    state = base_state()
    state["meta"]["campaign_id"] = "awareness"  # type: ignore[index]
    state["player"]["resources"].update(  # type: ignore[index]
        {
            "awareness-score": 0,
            "safe-escalations": 0,
            "reporting-quality": 0,
            "credential-exposure": 0,
            "unsafe-actions": 0,
        }
    )
    report = Intent(
        desired_outcome="Сообщаю в SOC: отправитель, время, тема и вложение указаны в описании.",
        methods=["free_text"],
    )
    _, report_patch = RuleEngine().resolve(
        state,
        report,
        "awareness-report",
        campaign_id="awareness",
        scenario_type="training",
    )
    report_values = {operation.path: operation.value for operation in report_patch.patch}
    assert report_values["/player/resources/safe-escalations"] == 1
    assert report_values["/player/resources/reporting-quality"] == 1
    assert report_values["/player/resources/awareness-score"] == 3

    exposure = Intent(
        desired_outcome="Перехожу по внешней ссылке и ввожу логин, пароль и проверочный код.",
        methods=["free_text"],
    )
    _, exposure_patch = RuleEngine().resolve(
        state,
        exposure,
        "awareness-exposure",
        campaign_id="awareness",
        scenario_type="training",
    )
    exposure_values = {operation.path: operation.value for operation in exposure_patch.patch}
    assert exposure_values["/player/resources/credential-exposure"] == 1
    assert exposure_values["/player/resources/unsafe-actions"] == 1
    assert exposure_values["/player/resources/awareness-score"] == -5


def test_awareness_training_does_not_score_negated_dangerous_actions():
    state = base_state()
    state["meta"]["campaign_id"] = "awareness"  # type: ignore[index]
    state["player"]["resources"]["suspicious-artifacts-opened"] = 0  # type: ignore[index]
    state["player"]["resources"]["credential-exposure"] = 0  # type: ignore[index]
    intent = Intent(
        desired_outcome="Не открываю Report.xlsx.exe и не сообщаю пароль.",
        methods=["free_text"],
    )

    _, patch = RuleEngine().resolve(
        state,
        intent,
        "awareness-negated",
        campaign_id="awareness",
        scenario_type="training",
    )
    paths = {operation.path for operation in patch.patch}

    assert "/player/resources/suspicious-artifacts-opened" not in paths
    assert "/player/resources/credential-exposure" not in paths


def test_awareness_turn_progression_updates_current_window():
    state = base_state()
    state["meta"]["campaign_id"] = "awareness"  # type: ignore[index]
    state["meta"]["turn"] = 1  # type: ignore[index]
    state["player"]["resources"]["current-turn-window"] = "ход 1, понедельник, 10:00-14:00"  # type: ignore[index]
    state["player"]["resources"]["turns-remaining"] = 9  # type: ignore[index]
    intent = Intent(
        action_type="feasibility",
        desired_outcome="Закрываю рабочие вопросы первой половины дня и отвечаю в рамках процедур",
        methods=["free_text"],
    )

    _, patch = RuleEngine().resolve(state, intent, "awareness-next", campaign_id="awareness", scenario_type="training")
    values = {operation.path: operation.value for operation in patch.patch}

    assert patch.turn == 2
    assert values["/player/resources/current-turn-window"] == "ход 2, понедельник, 15:00-18:00"
    assert values["/player/resources/turns-remaining"] == 8


def test_party_settings_keep_worldpack_id_for_awareness_rules():
    party = SimpleNamespace(
        worldpack_id="awareness",
        state_campaign_id="party_123",
        scenario_type="training",
        model_profile=None,
    )

    configured = settings_for_party(Settings(campaign_id="default"), party)

    assert configured.campaign_id == "awareness"
    assert configured.scenario_type == "training"


def test_awareness_party_state_id_still_enables_turn_progression():
    state = base_state()
    state["meta"]["campaign_id"] = "party_awareness_123"  # type: ignore[index]
    state["meta"]["turn"] = 1  # type: ignore[index]
    state["player"]["resources"]["current-turn-window"] = "ход 1, понедельник, 10:00-14:00"  # type: ignore[index]
    state["player"]["resources"]["turns-remaining"] = 9  # type: ignore[index]
    intent = Intent(action_type="feasibility", desired_outcome="Завершаю первую половину дня", methods=["free_text"])

    _, patch = RuleEngine().resolve(
        state,
        intent,
        "awareness-party-next",
        campaign_id="awareness",
        scenario_type="training",
    )
    values = {operation.path: operation.value for operation in patch.patch}

    assert patch.turn == 2
    assert values["/player/resources/current-turn-window"] == "ход 2, понедельник, 15:00-18:00"


def test_awareness_existing_party_auto_start_is_migrated_before_player_turn():
    state = base_state()
    state["meta"]["campaign_id"] = "party_awareness_old"  # type: ignore[index]
    state["player"]["resources"]["current-turn-window"] = "ход 1, понедельник, 10:00-14:00"  # type: ignore[index]
    state["player"]["resources"]["turns-remaining"] = 10  # type: ignore[index]

    migrated = awareness_state_after_auto_start(state, "awareness", has_auto_start=True)

    assert state["meta"]["turn"] == 0  # type: ignore[index]
    assert migrated["meta"]["turn"] == 1
    assert migrated["player"]["resources"]["turns-remaining"] == 9


def test_awareness_party_start_narrative_state_sets_opening_window_without_mutating_original():
    state = base_state()
    state["meta"]["campaign_id"] = "party_awareness_new"  # type: ignore[index]

    patch = party_start_state_patch(state, "party-awareness", "awareness", "training")
    narrative_state = party_start_narrative_state(state, patch)

    assert patch is not None
    assert state["meta"]["turn"] == 0  # type: ignore[index]
    assert "current-turn-window" not in state["player"]["resources"]  # type: ignore[index]
    assert narrative_state["meta"]["turn"] == 1
    assert narrative_state["player"]["resources"]["current-turn-window"] == "ход 1, понедельник, 10:00-14:00"
    assert narrative_state["player"]["resources"]["turns-remaining"] == 9


def test_awareness_validator_rejects_summarized_opening_and_accepts_structured_fallback():
    state = base_state()
    state["meta"]["campaign_id"] = "awareness"  # type: ignore[index]
    state["player"]["resources"]["current-turn-window"] = "ход 1, понедельник, 10:00-14:00"  # type: ignore[index]
    outcome = Outcome(
        check_id="awareness-start",
        action_type="feasibility",
        actor="system",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: opening scene.",
    )

    summarized = (
        "Ход 1. Понедельник, 10:00-14:00.\n\n"
        "Во входящих два письма от коллег, а в рабочем мессенджере Максим просит статус до обеда."
    )
    validation = OutputValidator().validate(summarized, outcome, state, scenario_type="training")

    assert not validation.valid
    assert any("summarized" in violation or "opening" in violation for violation in validation.violations)
    fallback = awareness_opening_fallback(state)
    assert OutputValidator().validate(fallback, outcome, state, scenario_type="training").valid
    assert fallback.count("ПИСЬМО") >= 2
    assert "СООБЩЕНИЕ" in fallback


def test_awareness_validator_rejects_facilitator_blocks_backend_and_repeated_turn():
    state = base_state()
    state["meta"]["campaign_id"] = "party_awareness_456"  # type: ignore[index]
    state["meta"]["turn"] = 2  # type: ignore[index]
    state["player"]["resources"]["current-turn-window"] = "ход 2, понедельник, 15:00-18:00"  # type: ignore[index]
    outcome = Outcome(
        check_id="awareness-messy-output",
        action_type="feasibility",
        actor="player",
        result="success",
        roll=10,
        difficulty=10,
        modifiers={},
        final_score=10,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: continue.",
    )
    messy = """**Ход 1. Понедельник, 10:00–14:00.**

Первое письмо выглядит безопасно. Ты понимаешь, что второе имеет двойное расширение и надо сообщить в SOC.
В incident-tracking появляется запись, backend добавляет строку в дашборд с уровнем опасности.

**Мессенджер:**
Руководитель просит статус.

**Блок-сценарий:**
- Пересылаешь письмо в SOC.
- Всё в пределах шаблона: два письма, одно сообщение и точка решения.
"""

    validation = OutputValidator().validate(
        messy,
        outcome,
        state,
        campaign_id="awareness",
        latest_user_message="Отправляю письмо на проверку",
        scenario_type="training",
    )

    assert not validation.valid
    assert any("scheduled header" in violation for violation in validation.violations)
    assert any("facilitator-only" in violation for violation in validation.violations)
    assert any("backend" in violation for violation in validation.violations)
    assert any("thoughts" in violation for violation in validation.violations)


def test_validator_repairs_meta_output_labels(tmp_path: Path):
    c = client(tmp_path, mode="meta-leak")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload("Тайм скип до ближайшего ивента"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "meta-leak"},
    )
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    assert_no_gateway_service_text(content)
    assert "мост" in content.lower()


def test_timeout_and_rate_limit(tmp_path: Path):
    c_timeout = client(tmp_path / "timeout", mode="timeout")
    response = c_timeout.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "timeout"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["finish_reason"] == "provider_fallback"
    assert_no_gateway_service_text(body["choices"][0]["message"]["content"])

    c_rate = client(tmp_path / "rate", mode="rate-limit")
    response = c_rate.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "rate"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["finish_reason"] == "provider_fallback"
    assert_no_gateway_service_text(body["choices"][0]["message"]["content"])


def test_provider_rate_limit_error_preserves_retry_metadata():
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        429,
        request=request,
        headers={"Retry-After": "12"},
        json={
            "error": {
                "message": "Rate limit exceeded",
                "metadata": {"error_type": "rate_limit_exceeded", "provider_code": "rate_limited"},
            }
        },
    )

    error = provider_rate_limit_error(response, "openrouter", "sao10k/l3.3-euryale-70b")

    assert error.details == {
        "provider": "openrouter",
        "model": "sao10k/l3.3-euryale-70b",
        "status": 429,
        "retry_after_seconds": 12,
        "error_type": "rate_limit_exceeded",
        "provider_code": "rate_limited",
        "response_message": "Rate limit exceeded",
    }
    assert error.public_detail()["retry_after_seconds"] == 12


def test_provider_http_error_returns_safe_fallback(tmp_path: Path):
    c = client(tmp_path, mode="http-503")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "provider-http-503"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["finish_reason"] == "provider_fallback"
    assert_no_gateway_service_text(body["choices"][0]["message"]["content"])


def test_party_rate_limit_is_saved_and_reported_to_gui(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path, mode="rate-limit")
    party = create_demo_party(c)

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "/check stealth skill=1 difficulty=10", "idempotency_key": "party-rate-limit"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_party_rate_limit"},
    )

    assert response.status_code == 429
    assert response.json()["detail"] == {
        "code": "provider_rate_limited",
        "message": "The selected model is temporarily rate limited.",
        "provider": "nvidia",
        "model": "z-ai/glm-5.2",
        "retry_after_seconds": 3,
        "error_type": "rate_limit_exceeded",
    }
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        row = connection.execute(
            "SELECT event_json FROM audit_events WHERE event_type = 'llm_rate_limited' AND request_id = ?",
            ("req_party_rate_limit",),
        ).fetchone()
    assert row is not None
    assert json.loads(row[0])["provider_code"] == "mock_rate_limited"


def test_party_message_provider_http_error_fails_without_gateway_fallback(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path, mode="http-503")
    party = create_demo_party(c)

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "/check stealth skill=1 difficulty=10", "idempotency_key": "party-http-503"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_party_http_503"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Narrative provider HTTP 503"
    history = c.get(f"/api/parties/{party['id']}/history").json()
    assert history["turns"] == []
    status = c.get(f"/api/parties/{party['id']}/requests/req_party_http_503").json()
    assert status["status"] == "failed"
    assert "Narrative provider HTTP 503" in status["error"]


def test_party_message_validation_failure_fails_without_gateway_fallback(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path, mode="repair-fail")
    party = create_demo_party(c)

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": '/check persuasion target=king skill=0 difficulty=30 goal="transfer command"',
            "idempotency_key": "party-repair-fail",
        },
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_party_repair_fail"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "LLM response failed narrative validation"
    history = c.get(f"/api/parties/{party['id']}/history").json()
    assert history["turns"] == []
    status = c.get(f"/api/parties/{party['id']}/requests/req_party_repair_fail").json()
    assert status["status"] == "failed"
    assert "LLM response failed narrative validation" in status["error"]


def test_party_start_provider_http_error_returns_502(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path, mode="http-503")
    party = create_demo_party(c)

    response = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "start-provider-http-503"},
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Narrative provider HTTP 503"


def test_default_nvidia_attempt_order_keeps_user_models():
    from app.core.config import Settings
    from app.services.narrative import NarrativeClient

    settings = Settings(nvidia_api_base="mock://success")
    assert NarrativeClient(settings).model_attempts(settings.narrative_model) == [
        "z-ai/glm-5.2",
        "deepseek-ai/deepseek-v4-pro",
        "deepseek-ai/deepseek-v4-flash",
        "qwen/qwen3.5-397b-a17b",
    ]


def test_disabled_primary_model_uses_fallback(tmp_path: Path):
    from app.core.config import Settings
    from app.services.narrative import NarrativeClient
    from app.services.state_store import StateStore
    from app.services.world_instructor import WorldInstructor

    settings = Settings(
        nvidia_fallback_models=("fallback/model", "other/model"),
        nvidia_disabled_models=("primary/model",),
    )
    state_store = StateStore(str(tmp_path / "state.db"), settings.campaign_id, str(tmp_path / "state.json"))
    assert NarrativeClient(settings).model_attempts("primary/model") == ["fallback/model", "other/model"]
    assert WorldInstructor(settings, state_store).model_attempts("primary/model") == ["fallback/model", "other/model"]


def test_idempotency_prevents_duplicate_turn(tmp_path: Path):
    c = client(tmp_path)
    headers = {"Authorization": "Bearer test", "Idempotency-Key": "same-turn"}
    first = c.post("/v1/chat/completions", json=chat_payload("/check stealth skill=1 difficulty=10"), headers=headers)
    second = c.post("/v1/chat/completions", json=chat_payload("/check stealth skill=1 difficulty=10"), headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    state = c.get("/api/state").json()["state"]
    assert state["meta"]["state_version"] == 2


def test_party_message_reports_running_idempotency_request(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Message Pending")
    party_store = c.app.state.party_store
    party_state_store = party_store.store_for_party(party["id"])
    party_state_store.begin_turn_request("message-pending", "req_message_pending")

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "/check stealth skill=1 difficulty=10", "idempotency_key": "message-pending"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_message_pending"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "running"
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == []


def test_party_request_status_recovers_completed_turn(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Recover Turn")

    turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "/check stealth skill=1 difficulty=10", "idempotency_key": "recover-turn"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_recover_turn"},
    )
    assert turn.status_code == 200, turn.text

    status = c.get(f"/api/parties/{party['id']}/requests/req_recover_turn")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "completed"
    assert body["turn"]["request_id"] == "req_recover_turn"
    assert body["turn"]["narrative_response"] == turn.json()["message"]["content"]
    assert body["request"]["status"] == "completed"


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
