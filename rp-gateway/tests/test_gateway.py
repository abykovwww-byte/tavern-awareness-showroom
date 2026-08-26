from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import shutil
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import narrative as narrative_service
from app.services import rule_engine as rule_engine_service
from app.core.config import Settings
from app.main import apply_party_narrator_settings, create_app, party_chat_request, settings_for_party
from app.models.schemas import (
    ChatCompletionRequest,
    ChatMessage,
    Intent,
    Outcome,
    PartyMessageRequest,
    WORLD_MARKDOWN_MAX_CHARS,
    WORLD_PROMPT_MAX_CHARS,
)
from app.services.adjudicator import Adjudicator
from app.services.intent_parser import IntentParser
from app.services.memory import MemorySummarizer
from app.services.narrative import NarrativeClient, provider_rate_limit_error, response_text
from app.services.provider_catalog import (
    OPENROUTER_FEATURED_MODELS,
    enrich_openrouter_profile_params,
    fetch_provider_api_profiles,
    narrator_control_capabilities,
    prices_are_free,
    provider_model_is_suitable,
)
from app.services.prompt_tools import PromptInspector
from app.services.relationship_store import RelationshipStore
from app.services.relationship_extraction import RelationshipExtractionService
from app.services.rp_gm import RPGMService
from app.services.rp_history import AUTO_START_HISTORY_MESSAGE
from app.services.rule_engine import RuleEngine
from app.services.service_models import SERVICE_MODEL_SETTING_KEY, service_model_settings
from app.services.service_model_client import ServiceModelClient
from app.services.state_store import StateStore
from app.services.validator import OutputValidator, safe_fallback


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
        "showroom_cover_dir": str(tmp_path / "showroom-covers"),
        "worldpacks_path": str(tmp_path / "worldpacks"),
        "llm_api_base": f"mock://{mode}",
        "llm_api_key": api_key,
        "gemini_api_base": f"mock://{mode}",
        "gemini_api_key": api_key,
        "service_model_choice": "or-qwen-3.5-flash",
        "openrouter_api_base": f"mock://{mode}",
        "service_openrouter_api_key": api_key,
        # Never inherit the production container's local-runner switch. Tests
        # that exercise local Gemma enable it explicitly in settings_overrides.
        "local_llm_enabled": False,
        "post_turn_helpers_inline": True,
        "auth_enabled": False,
        "rp_contract_observed_revision": 6,
    }
    settings_kwargs.update(settings_overrides)
    settings = Settings(**settings_kwargs)
    return TestClient(create_app(settings))


def test_app_lifespan_resumes_persisted_work_without_legacy_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    c = client(tmp_path)
    party_store = c.app.state.party_store
    calls: list[str] = []
    monkeypatch.setattr(party_store, "list_parties", lambda: calls.append("parties") or [])
    monkeypatch.setattr(party_store, "list_all_party_branches", lambda: calls.append("branches") or [])
    monkeypatch.setattr(party_store, "resumable_autotest_runs", lambda: calls.append("autotests") or [])

    assert c.app.router.on_startup == []
    assert c.app.router.on_shutdown == []

    with c:
        pass

    assert calls == ["parties", "branches", "autotests"]


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


def write_worldpack(
    root: Path,
    pack_id: str = "demo-world",
    supported_modes: list[str] | None = None,
    *,
    rp_revision: int | None = None,
    lore_cards: list[dict[str, object]] | None = None,
    world_clock: dict[str, object] | None = None,
) -> Path:
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
        if supported_modes == ["training"]:
            manifest["showroom_result"] = {"metric": "state_path", "state_path": "meta.turn"}
    if rp_revision is not None:
        manifest["scenario_types"] = {"recommended": "rp", "supported": ["rp"]}
        manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": rp_revision}
    if lore_cards is not None:
        manifest["files"]["lore_cards"] = "lore-cards"
    if world_clock is not None:
        manifest["files"]["world_clock"] = "world-clock.json"
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
    if lore_cards is not None:
        (pack_dir / "lore-cards").mkdir()
        (pack_dir / "lore-cards" / "core.json").write_text(
            json.dumps(
                {
                    "schema_version": "rp-gateway.worldpack-lore-cards.v1",
                    "cards": lore_cards,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    if world_clock is not None:
        (pack_dir / "world-clock.json").write_text(
            json.dumps(world_clock, ensure_ascii=False),
            encoding="utf-8",
        )
    return pack_dir


def create_demo_party(
    c: TestClient,
    title: str = "Demo Party",
    character_name: str = "Mira",
    scenario_type: str = "rp",
    worldpack_id: str = "demo-world",
) -> dict[str, object]:
    model_id = c.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    character = c.post(
        "/api/player-characters",
        json={"worldpack_id": worldpack_id, "name": character_name, "description": "Investigator", "profile": {}},
    ).json()["player_character"]
    return c.post(
        "/api/parties",
        json={
            "title": title,
            "scenario_type": scenario_type,
            "worldpack_id": worldpack_id,
            "player_character_id": character["id"],
            "model_profile_id": model_id,
        },
    ).json()["party"]


def latest_turn_metadata(store: StateStore) -> dict[str, object]:
    with sqlite3.connect(store.sqlite_path) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
            (store.campaign_id,),
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


def seed_incorrect_story_fact(store: StateStore) -> None:
    store.record_rp_story_memory(
        from_turn_id=0,
        to_turn_id=0,
        state_version=store.current_version() or 1,
        memory={
            "schema_version": "rp-gateway.rp-story-memory.v2",
            "canon": [
                {
                    "fact_id": "fact:incorrect-gate",
                    "text": "The gate is closed.",
                    "status": "active",
                    "authority": "inference",
                    "source_turn_ids": [0],
                }
            ],
            "rules_and_abilities": [],
            "inventory_and_assets": [],
            "characters": [],
            "active_threads": [],
            "resolved_threads": [],
            "unresolved_hooks": [],
            "current_situation": None,
            "chronology": [],
        },
        model="seeded-test-memory",
    )


def seed_revision_nine_raw_turn(c: TestClient, party_id: str) -> tuple[StateStore, int]:
    store = c.app.state.party_store.store_for_party(party_id)
    state = store.get_state()
    version = (store.current_version() or 0) + 1
    state["meta"]["state_version"] = version
    state["meta"]["turn"] = 1
    state["last_turn"] = {
        "turn": 1,
        "player_message": "Я слушаю летописца.",
        "narrator_response": "Варн говорит, что он придворный летописец.",
        "state_patch_id": "test:raw-turn",
    }
    store.insert_state_version(state, "test_revision_nine_raw_turn")
    turn_id = store.record_turn(
        "seed-rev9-raw",
        "seed-rev9-raw-request",
        "Я слушаю летописца.",
        "Варн говорит, что он придворный летописец.",
        {},
        state_version=version,
        metadata={"turn_kind": "narrative"},
        party_turn=1,
    )
    return store, turn_id


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


def test_public_showroom_keeps_scenarios_separate_from_worlds_and_users(tmp_path: Path):
    write_worldpack(tmp_path, supported_modes=["rp", "training"])
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]

    first = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Night investigation",
            "description": "Investigate one difficult night.",
            "status": "published",
            "scenario_type": "rp",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "demo-world",
            "leaderboard_enabled": True,
            "leaderboard_metric": "state_path",
            "leaderboard_state_path": "meta.turn",
            "leaderboard_label": "Turns",
        },
    )
    assert first.status_code == 200, first.text
    first_scenario = first.json()["scenario"]
    second = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Quiet character drama",
            "description": "A different scenario in the same world.",
            "status": "published",
            "scenario_type": "training",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "demo-world",
            "leaderboard_enabled": True,
            "leaderboard_metric": "turn_count",
            "leaderboard_state_path": "meta.turn",
            "leaderboard_label": "Turns",
        },
    )
    assert second.status_code == 200, second.text
    second_scenario = second.json()["scenario"]
    assert first_scenario["id"] != second_scenario["id"]
    assert first_scenario["worldpack_id"] == second_scenario["worldpack_id"] == "demo-world"
    assert first_scenario["title"] != first_scenario["world"]["title"]

    public = TestClient(admin.app)
    assert public.get("/api/admin/showroom/scenarios").status_code == 401
    listed = public.get("/api/showroom/scenarios")
    assert listed.status_code == 200
    listed_scenarios = listed.json()["scenarios"]
    assert {item["title"] for item in listed_scenarios} == {"Night investigation", "Quiet character drama"}
    assert all("model_profile_id" not in item for item in listed_scenarios)
    assert public.get("/api/parties").status_code == 401

    run_response = public.post(
        f"/api/showroom/scenarios/{first_scenario['id']}/runs",
        json={
            "character_name": "Anonymous Hero",
            "character_prompt": "A careful investigator.",
            "leaderboard_opt_in": True,
            "client_request_id": "browser-request-1",
        },
    )
    assert run_response.status_code == 200, run_response.text
    run = run_response.json()["run"]
    assert "party_id" not in run
    assert admin.app.state.settings.showroom_visitor_cookie_name in public.cookies
    assert public.get("/api/showroom/runs").json()["runs"][0]["id"] == run["id"]

    repeated = public.post(
        f"/api/showroom/scenarios/{first_scenario['id']}/runs",
        json={
            "character_name": "Anonymous Hero",
            "character_prompt": "A careful investigator.",
            "leaderboard_opt_in": True,
            "client_request_id": "browser-request-1",
        },
    )
    assert repeated.json()["run"]["id"] == run["id"]

    renamed = admin.patch(
        f"/api/admin/showroom/scenarios/{first_scenario['id']}",
        json={"title": "Renamed storefront card"},
    )
    assert renamed.status_code == 200, renamed.text
    historical_run = public.get(f"/api/showroom/runs/{run['id']}").json()["run"]
    assert historical_run["scenario"]["title"] == "Night investigation"

    started = public.post(
        f"/api/showroom/runs/{run['id']}/start",
        json={"idempotency_key": f"showroom-start:{run['id']}"},
    )
    assert started.status_code == 200, started.text
    assert "party_id" not in started.json()
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        technical_owner = connection.execute(
            """
            SELECT parties.owner_user_id
            FROM showroom_runs JOIN parties ON parties.id = showroom_runs.party_id
            WHERE showroom_runs.id = ?
            """,
            (run["id"],),
        ).fetchone()[0]
    assert technical_owner == "__showroom__"
    history = public.get(f"/api/showroom/runs/{run['id']}/history")
    assert history.status_code == 200
    assert len(history.json()["turns"]) == 1

    player_turn = public.post(
        f"/api/showroom/runs/{run['id']}/messages",
        json={"content": "I inspect the room.", "idempotency_key": "showroom-turn-1"},
    )
    assert player_turn.status_code == 200, player_turn.text
    assert "party_id" not in player_turn.json()

    leaderboard = public.get(f"/api/showroom/scenarios/{first_scenario['id']}/leaderboard")
    assert leaderboard.status_code == 200
    assert leaderboard.json()["entries"][0]["display_name"] == "Anonymous Hero"
    assert leaderboard.json()["entries"][0]["score"] == 1
    other_board = public.get(f"/api/showroom/scenarios/{second_scenario['id']}/leaderboard")
    assert other_board.json()["entries"] == []

    intruder = TestClient(admin.app)
    assert intruder.get(f"/api/showroom/runs/{run['id']}").status_code == 404


def test_showroom_rejects_novel_create_and_update_with_422(tmp_path: Path):
    write_worldpack(tmp_path, supported_modes=["rp", "training"])
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    payload = {
        "title": "Active scenario contract",
        "description": "Only active modes are accepted.",
        "status": "draft",
        "scenario_type": "novel",
        "model_profile_id": model_id,
        "world_source": "preset",
        "worldpack_id": "demo-world",
    }

    rejected_create = admin.post("/api/admin/showroom/scenarios", json=payload)
    assert rejected_create.status_code == 422, rejected_create.text

    created = admin.post(
        "/api/admin/showroom/scenarios",
        json={**payload, "scenario_type": "rp"},
    )
    assert created.status_code == 200, created.text
    rejected_update = admin.patch(
        f"/api/admin/showroom/scenarios/{created.json()['scenario']['id']}",
        json={"scenario_type": "novel"},
    )
    assert rejected_update.status_code == 422, rejected_update.text


def test_legacy_novel_showroom_is_archived_terminal_and_run_history_stays_readable(tmp_path: Path):
    write_worldpack(tmp_path, supported_modes=["rp"])
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    scenario = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Legacy Showroom",
            "description": "Stored legacy scenario.",
            "status": "published",
            "scenario_type": "rp",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "demo-world",
        },
    ).json()["scenario"]
    visitor = TestClient(admin.app)
    run_response = visitor.post(
        f"/api/showroom/scenarios/{scenario['id']}/runs",
        json={"character_name": "Legacy Hero", "character_prompt": "Historical run."},
    )
    assert run_response.status_code == 200, run_response.text
    run = run_response.json()["run"]

    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        party_id = connection.execute(
            "SELECT party_id FROM showroom_runs WHERE id = ?",
            (run["id"],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE showroom_scenarios SET scenario_type = 'novel', status = 'published' WHERE id = ?",
            (scenario["id"],),
        )
        connection.execute(
            "UPDATE parties SET scenario_type = 'novel', status = 'active' WHERE id = ?",
            (party_id,),
        )

    for _ in range(2):
        admin.app.state.party_store.init_db()
        admin.app.state.showroom_store.init_db()

    stored = next(
        item
        for item in admin.get("/api/admin/showroom/scenarios").json()["scenarios"]
        if item["id"] == scenario["id"]
    )
    assert stored["scenario_type"] == "novel"
    assert stored["status"] == "archived"
    assert all(item["id"] != scenario["id"] for item in visitor.get("/api/showroom/scenarios").json()["scenarios"])
    assert visitor.get(f"/api/showroom/runs/{run['id']}").status_code == 200
    assert visitor.get(f"/api/showroom/runs/{run['id']}/history").status_code == 200

    republish = admin.patch(
        f"/api/admin/showroom/scenarios/{scenario['id']}",
        json={"status": "published"},
    )
    assert republish.status_code == 400
    assert republish.json()["detail"] == "archived showroom scenario is terminal"
    new_run = visitor.post(
        f"/api/showroom/scenarios/{scenario['id']}/runs",
        json={"character_name": "Blocked", "character_prompt": "Must not run."},
    )
    assert new_run.status_code == 400
    assert visitor.post(f"/api/showroom/runs/{run['id']}/start").status_code == 404
    assert visitor.post(
        f"/api/showroom/runs/{run['id']}/messages",
        json={"content": "Must not run."},
    ).status_code == 404


def test_showroom_training_capabilities_are_validated_and_snapshotted(tmp_path: Path):
    source = Path(__file__).resolve().parents[2] / "worldpacks" / "awareness-one-day"
    shutil.copytree(source, tmp_path / "worldpacks" / "awareness-one-day")
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    response = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Training with both interaction surfaces",
            "status": "published",
            "scenario_type": "training",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "awareness-one-day",
            "interactive_links_enabled": True,
            "interactive_workspace_enabled": True,
        },
    )
    assert response.status_code == 200, response.text
    scenario = response.json()["scenario"]
    assert scenario["interactive_links_enabled"] is True
    assert scenario["interactive_workspace_enabled"] is True
    assert scenario["training_capabilities"] == {
        "interactive_links_supported": True,
        "interactive_workspace_supported": True,
    }

    public = TestClient(admin.app)
    run_response = public.post(
        f"/api/showroom/scenarios/{scenario['id']}/runs",
        json={
            "character_name": "Employee",
            "character_prompt": "Security-aware employee",
            "employee_position": "Security analyst",
        },
    )
    assert run_response.status_code == 200, run_response.text
    run = run_response.json()["run"]
    assert run["interactive_links_enabled"] is True
    assert run["interactive_workspace_enabled"] is True

    changed = admin.patch(
        f"/api/admin/showroom/scenarios/{scenario['id']}",
        json={"interactive_links_enabled": False, "interactive_workspace_enabled": False},
    )
    assert changed.status_code == 200, changed.text
    unchanged_run = public.get(f"/api/showroom/runs/{run['id']}").json()["run"]
    assert unchanged_run["interactive_links_enabled"] is True
    assert unchanged_run["interactive_workspace_enabled"] is True
    leaderboard = public.get(f"/api/showroom/scenarios/{scenario['id']}/leaderboard").json()
    assert leaderboard["dimensions"] == {
        "interactive_links_enabled": False,
        "interactive_workspace_enabled": False,
    }
    assert leaderboard["entries"] == []

    started = public.post(
        f"/api/showroom/runs/{run['id']}/start",
        json={"idempotency_key": f"showroom-start:{run['id']}"},
    )
    assert started.status_code == 200, started.text
    workspace = public.get(f"/api/showroom/runs/{run['id']}/workspace")
    assert workspace.status_code == 200, workspace.text
    assert {item["file_key"] for item in workspace.json()["workspace"]["files"]} == {"security-policy"}
    assert "resource_classification" not in workspace.text
    with admin.app.state.showroom_store.connect() as connection:
        party_id = connection.execute(
            "SELECT party_id FROM showroom_runs WHERE id = ?",
            (run["id"],),
        ).fetchone()["party_id"]
    dataset_turn = admin.app.state.party_store.list_dataset_turns(party_id, limit=None)[0]
    assert dataset_turn["metadata"]["training_capabilities"] == {
        "interactive_links_enabled": True,
        "interactive_workspace_enabled": True,
    }
    assert [item["file_key"] for item in dataset_turn["workspace_files"]] == ["security-policy"]
    sft = admin.app.state.party_store.dataset_sft_record(dataset_turn)
    assert sft["metadata"]["training_capabilities"]["interactive_workspace_enabled"] is True
    assert [item["file_key"] for item in sft["metadata"]["workspace_files"]] == ["security-policy"]

    unsupported_dir = write_worldpack(tmp_path, pack_id="unsupported-training", supported_modes=["training"])
    unsupported_manifest_path = unsupported_dir / "manifest.json"
    unsupported_manifest = json.loads(unsupported_manifest_path.read_text(encoding="utf-8"))
    unsupported_manifest["showroom_result"] = {"metric": "state_path", "state_path": "meta.turn"}
    unsupported_manifest_path.write_text(json.dumps(unsupported_manifest), encoding="utf-8")
    unsupported = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Unsupported links",
            "status": "draft",
            "scenario_type": "training",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "unsupported-training",
            "interactive_links_enabled": True,
        },
    )
    assert unsupported.status_code == 400
    assert "does not support interactive links" in unsupported.text


def test_showroom_rp_start_ignores_semantic_validator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_worldpack(tmp_path)
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    scenario = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Fallback showroom scenario",
            "status": "published",
            "scenario_type": "rp",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "demo-world",
        },
    ).json()["scenario"]
    public = TestClient(admin.app)
    run = public.post(
        f"/api/showroom/scenarios/{scenario['id']}/runs",
        json={"character_name": "Hero", "character_prompt": "Investigator"},
    ).json()["run"]

    monkeypatch.setattr(
        OutputValidator,
        "validate",
        lambda *args, **kwargs: SimpleNamespace(
            valid=False,
            violations=["forced test violation"],
            repair_instruction="repair",
        ),
    )
    started = public.post(
        f"/api/showroom/runs/{run['id']}/start",
        json={"idempotency_key": f"showroom-start:{run['id']}"},
    )

    assert started.status_code == 200, started.text
    assert started.json()["raw"]["choices"][0]["finish_reason"] == "stop"
    history = public.get(f"/api/showroom/runs/{run['id']}/history").json()["turns"]
    assert len(history) == 1
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        showroom_party_id = connection.execute(
            "SELECT party_id FROM showroom_runs WHERE id = ?", (run["id"],)
        ).fetchone()[0]
    metadata = latest_turn_metadata(admin.app.state.party_store.store_for_party(showroom_party_id))
    assert metadata["validator_valid"] is None
    assert metadata["fallback"] is False
    assert metadata["transport_status"] == "ok"


def test_showroom_prompt_world_and_cover_are_scenario_owned_runtime_content(tmp_path: Path):
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    created = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Station at the edge",
            "description": "A public card title independent of the generated world.",
            "status": "published",
            "scenario_type": "training",
            "model_profile_id": model_id,
            "world_source": "prompt",
            "world_prompt": "A remote scientific station loses contact during a polar night.",
            "leaderboard_enabled": True,
            "leaderboard_metric": "turn_count",
            "leaderboard_state_path": "meta.turn",
            "leaderboard_label": "Turns",
        },
    )
    assert created.status_code == 200, created.text
    scenario = created.json()["scenario"]
    assert scenario["id"] != scenario["worldpack_id"]
    assert scenario["title"] != scenario["world"]["title"]
    assert scenario["world_source"] == "prompt"

    png = b"\x89PNG\r\n\x1a\n" + (b"mock" * 10)
    uploaded = admin.put(
        f"/api/admin/showroom/scenarios/{scenario['id']}/cover",
        content=png,
        headers={"Content-Type": "image/png"},
    )
    assert uploaded.status_code == 200, uploaded.text
    public = TestClient(admin.app)
    cover = public.get(f"/api/showroom/scenarios/{scenario['id']}/cover")
    assert cover.status_code == 200
    assert cover.headers["content-type"].startswith("image/png")
    assert cover.content == png


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


def test_revision_nine_uncertain_route_and_rejected_gm_draft_do_not_mutate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    write_worldpack(tmp_path, rp_revision=9)
    c = client(
        tmp_path,
        local_llm_enabled=True,
        local_llm_base_url="mock://success",
        rp_contract_observed_revision=9,
    )
    party = create_demo_party(c)
    store, turn_id = seed_revision_nine_raw_turn(c, str(party["id"]))
    before_version = store.current_version()
    before_history = c.get(f"/api/parties/{party['id']}/history").json()["turns"]

    async def uncertain_intent(
        _service: RPGMService,
        _content: str,
        *,
        request_id: str,
    ) -> dict[str, object]:
        assert request_id
        return {"label": "uncertain", "target": None, "reason": "test_uncertain"}

    monkeypatch.setattr(RPGMService, "classify", uncertain_intent)
    route = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Это мастеру или в сцену?", "idempotency_key": "rev9-route"},
    )

    assert route.status_code == 200, route.text
    assert route.json()["status"] == "route_required"
    assert route.json()["routing"]["options"] == ["gm", "scene"]
    assert store.current_version() == before_version
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == before_history

    draft = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": "Варн служит Ждану, а не является летописцем.",
            "channel": "gm",
            "gm_target_slot": f"raw:{turn_id}",
            "idempotency_key": "rev9-draft-reject",
        },
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["status"] == "gm_draft"
    assert store.current_version() == before_version

    reject = c.post(
        f"/api/parties/{party['id']}/gm-corrections/decide",
        json={"decision": "reject", "proposal": draft.json()["gm_patch_draft"]},
    )
    assert reject.status_code == 200, reject.text
    assert reject.json()["status"] == "rejected"
    assert store.current_version() == before_version
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == before_history


def test_revision_nine_confirmed_gm_correction_is_idempotent_and_out_of_scene(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    write_worldpack(tmp_path, rp_revision=9)
    c = client(
        tmp_path,
        local_llm_enabled=True,
        local_llm_base_url="mock://success",
        post_turn_helpers_inline=False,
        rp_contract_observed_revision=9,
    )
    monkeypatch.setattr(
        Adjudicator,
        "schedule_service_jobs",
        lambda _self, _authorization=None: None,
    )
    party = create_demo_party(c)
    store, turn_id = seed_revision_nine_raw_turn(c, str(party["id"]))
    before_state = store.get_state()
    before_version = store.current_version() or 0

    draft = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": "Варн служит Ждану, а не является летописцем.",
            "channel": "gm",
            "gm_target_slot": f"raw:{turn_id}",
            "idempotency_key": "rev9-draft-confirm",
        },
    )
    assert draft.status_code == 200, draft.text
    proposal = draft.json()["gm_patch_draft"]
    decision = {
        "decision": "confirm",
        "proposal": proposal,
        "idempotency_key": "rev9-confirm",
    }
    first = c.post(
        f"/api/parties/{party['id']}/gm-corrections/decide",
        json=decision,
        headers={"X-Request-ID": "rev9-confirm-request"},
    )
    second = c.post(
        f"/api/parties/{party['id']}/gm-corrections/decide",
        json=decision,
        headers={"X-Request-ID": "rev9-confirm-request"},
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    state = store.get_state()
    assert state["meta"]["state_version"] == before_version + 1
    assert state["meta"]["turn"] == before_state["meta"]["turn"]
    assert state.get("scene_state") == before_state.get("scene_state")
    history = c.get(f"/api/parties/{party['id']}/history").json()["turns"]
    assert len(history) == 2
    correction_turn = history[-1]
    assert correction_turn["turn_kind"] == "gm_correction"
    assert correction_turn["party_turn"] == 1
    assert correction_turn["player_correction"]["status"] == "active"
    jobs = c.get(f"/api/parties/{party['id']}/service-jobs").json()["jobs"]
    correction_jobs = [job for job in jobs if job["request_id"] == "rev9-confirm-request"]
    assert len(correction_jobs) == 1
    assert correction_jobs[0]["job_type"] == "rp_story_memory"
    assert correction_jobs[0]["max_attempts"] == 2


def test_rp_core_v2_turn_has_no_hidden_check_or_random_result(tmp_path: Path):
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    c = client(tmp_path)
    party = create_demo_party(c)

    preview = c.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"content": "Стоп. Почему мы не можем выйти наружу?", "source": "current"},
    )
    assert preview.status_code == 200
    preview_payload = preview.json()["preview"]
    assert preview_payload["intent"]["action_type"] == "narrative"
    assert preview_payload["outcome"]["result"] == "narrative_continuation"
    assert preview_payload["outcome"]["roll"] == preview_payload["outcome"]["difficulty"] == 0

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Стоп. Почему мы не можем выйти наружу?"},
    )

    assert response.status_code == 200
    assert party["rp_contract_version"] == "rp-core.v2"
    assert party["rp_contract_revision"] == 6
    store = c.app.state.party_store.store_for_party(str(party["id"]))
    metadata = latest_turn_metadata(store)
    assert metadata["outcome"] == {
        "check_id": metadata["outcome"]["check_id"],
        "action_type": "narrative",
        "actor": "player",
        "target": None,
        "result": "narrative_continuation",
        "roll": 0,
        "difficulty": 0,
        "modifiers": {},
        "final_score": 0,
        "blocked_reasons": [],
        "consequences": metadata["outcome"]["consequences"],
        "forbidden_reinterpretations": metadata["outcome"]["forbidden_reinterpretations"],
        "authoritative_block": metadata["outcome"]["authoritative_block"],
    }
    with sqlite3.connect(store.sqlite_path) as connection:
        checks = connection.execute(
            "SELECT COUNT(*) FROM checks WHERE campaign_id = ?",
            (store.campaign_id,),
        ).fetchone()[0]
    assert checks == 0


def test_rp_revision_one_and_training_never_call_random_system_rng(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ForbiddenRng:
        def __init__(self) -> None:
            raise AssertionError("SystemRandom must not be constructed")

    monkeypatch.setattr(rule_engine_service.random, "SystemRandom", ForbiddenRng)
    intent = Intent(action_type="narrative", desired_outcome="Продолжаю сцену")

    rp_outcome, _ = RuleEngine().resolve(
        base_state(),
        intent,
        "rp-no-rng",
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=1,
    )
    training_outcome, _ = RuleEngine().resolve(
        base_state(),
        intent,
        "training-no-rng",
        scenario_type="training",
    )

    assert rp_outcome.result == "narrative_continuation"
    assert training_outcome.result == "deterministic_resolution"
    assert rp_outcome.roll == training_outcome.roll == 0


def test_candidate_revision_is_branch_only_until_observed(tmp_path: Path) -> None:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 6}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    c = client(tmp_path, rp_contract_observed_revision=0)
    party = create_demo_party(c)
    assert party["rp_contract_revision"] == 0

    checkpoint = c.post(
        f"/api/parties/{party['id']}/checkpoints",
        json={"label": "ADR 026 candidate"},
    ).json()["checkpoint"]
    branch_response = c.post(
        f"/api/parties/{party['id']}/branches",
        json={
            "checkpoint_id": checkpoint["id"],
            "label": "candidate revision 6",
            "rp_contract_revision": 6,
        },
    )

    assert branch_response.status_code == 200, branch_response.text
    assert branch_response.json()["branch"]["rp_contract_revision"] == 6
    assert c.get(f"/api/parties/{party['id']}").json()["party"]["rp_contract_revision"] == 0


def test_rp_core_v2_manual_check_endpoint_is_neutral_compatibility_input(tmp_path: Path):
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    c = client(tmp_path)
    party = create_demo_party(c)

    response = c.post(
        f"/api/parties/{party['id']}/checks",
        json={"check_type": "persuasion", "target": "advisor", "skill": 99, "difficulty": 1, "goal": "meeting"},
    )

    assert response.status_code == 200
    store = c.app.state.party_store.store_for_party(str(party["id"]))
    outcome = latest_turn_metadata(store)["outcome"]
    assert outcome["action_type"] == "narrative"
    assert outcome["result"] == "narrative_continuation"
    assert outcome["roll"] == outcome["difficulty"] == outcome["final_score"] == 0


def test_rp_core_v2_validator_rejects_declared_absolute_rule_violation() -> None:
    state = base_state()
    state["world_constraints"] = [
        {
            "id": "absolute-power",
            "text": "The power affects living matter.",
            "scope": "power",
            "turn": 0,
            "kind": "absolute",
            "source": "worldpack:test",
            "forbidden_claims": ["power fails on living matter"],
        }
    ]
    result = OutputValidator().validate(
        "The power fails on living matter.",
        Outcome(
            check_id="neutral",
            action_type="narrative",
            actor="player",
            result="narrative_continuation",
            roll=0,
            difficulty=0,
            modifiers={},
            final_score=0,
            authoritative_block="neutral",
        ),
        state,
        scenario_type="rp",
    )

    assert result.valid is False
    assert result.violations == [
        "Narrative contradicts absolute WorldPack rule absolute-power: power fails on living matter"
    ]


@pytest.mark.parametrize("world_slug", ["merchant-sviatoslav", "starosta"])
def test_authored_player_agency_rule_allows_sensory_feedback_but_rejects_player_choice(
    world_slug: str,
) -> None:
    worldpacks = Path(__file__).resolve().parents[2] / "worldpacks"
    state = json.loads((worldpacks / world_slug / "state-seed.json").read_text(encoding="utf-8"))
    outcome = Outcome(
        check_id="player-agency-sensory-regression",
        action_type="narrative",
        actor="player",
        result="narrative_continuation",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="neutral",
    )
    validator = OutputValidator()

    sensory = validator.validate(
        "Ты чувствуешь её вес — добрых три пуда.",
        outcome,
        state,
        scenario_type="rp",
    )
    player_choice = validator.validate(
        "Ты решаешь принять товар без торга.",
        outcome,
        state,
        scenario_type="rp",
    )

    assert sensory.valid is True
    assert player_choice.valid is False
    assert player_choice.violations == [
        "Narrative contradicts absolute WorldPack rule player-agency: ты решаешь"
    ]


@pytest.mark.parametrize(
    "narration",
    [
        (
            "Стою посреди двора, глядя на закрывшуюся калитку.\n\n"
            "Подхожу к столу, сажусь на лавку и начинаю объяснять Милаве свой план."
        ),
        (
            "Смотрю на Милаву и касаюсь её плеча.\n\n"
            "Убираю руку, отступаю на шаг и решаю, что сейчас пора спать."
        ),
    ],
)
def test_rp_validator_rejects_sustained_russian_first_person_player_voice(
    narration: str,
) -> None:
    outcome = Outcome(
        check_id="first-person-player-voice",
        action_type="narrative",
        actor="player",
        result="narrative_continuation",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="neutral",
    )

    result = OutputValidator().validate(
        narration,
        outcome,
        base_state(),
        scenario_type="rp",
    )

    assert result.valid is False
    assert result.violations == [
        "Narrative switched into the player character's first-person voice."
    ]
    assert "Не пиши от первого лица персонажа игрока" in str(result.repair_instruction)


def test_rp_validator_does_not_treat_npc_first_person_dialogue_as_player_voice() -> None:
    outcome = Outcome(
        check_id="npc-first-person-dialogue",
        action_type="narrative",
        actor="player",
        result="narrative_continuation",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="neutral",
    )

    result = OutputValidator().validate(
        "— Я стою здесь с рассвета, — говорит Ратибор.\n\n"
        "— Смотрю на ворота и жду твоего ответа.\n\n"
        "Он замолкает и оставляет следующий шаг тебе.",
        outcome,
        base_state(),
        scenario_type="rp",
    )

    assert result.valid is True


def test_rp_core_v2_repeated_absolute_rule_violation_never_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    llm_calls = 0

    async def violating_complete(*args: object, **kwargs: object) -> dict:
        nonlocal llm_calls
        llm_calls += 1
        return {
            "id": f"violation-{llm_calls}",
            "choices": [{"message": {"role": "assistant", "content": "The power fails on living matter."}}],
        }

    monkeypatch.setattr(NarrativeClient, "complete", violating_complete)
    c = client(tmp_path)
    party = create_demo_party(c)
    store = c.app.state.party_store.store_for_party(str(party["id"]))
    state = store.get_state()
    state["world_constraints"] = [
        {
            "id": "absolute-power",
            "text": "The power affects living matter.",
            "scope": "power",
            "turn": 0,
            "kind": "absolute",
            "source": "worldpack:test",
            "forbidden_claims": ["power fails on living matter"],
        }
    ]
    state["meta"]["state_version"] = int(store.current_version() or 0) + 1
    store.insert_state_version(state, "test:absolute-rule")
    version_before = store.current_version()

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Use the power.", "idempotency_key": "absolute-rule-failure"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "LLM response failed narrative validation"
    assert llm_calls == 2
    assert store.current_version() == version_before
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == []


def test_rp_opening_scene_repeated_absolute_rule_violation_never_commits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 6}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    calls = 0

    async def violating_complete(*args: object, **kwargs: object) -> dict:
        nonlocal calls
        calls += 1
        return {
            "id": f"opening-violation-{calls}",
            "choices": [{"message": {"role": "assistant", "content": "The power fails on living matter."}}],
        }

    monkeypatch.setattr(NarrativeClient, "complete", violating_complete)
    c = client(tmp_path)
    party = create_demo_party(c)
    store = c.app.state.party_store.store_for_party(str(party["id"]))
    state = store.get_state()
    state["world_constraints"] = [
        {
            "id": "absolute-power",
            "text": "The power affects living matter.",
            "scope": "power",
            "turn": 0,
            "kind": "absolute",
            "source": "worldpack:test",
            "forbidden_claims": ["power fails on living matter"],
        }
    ]
    state["meta"]["state_version"] = int(store.current_version() or 0) + 1
    store.insert_state_version(state, "test:opening-absolute-rule")
    version_before = store.current_version()

    response = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "opening-absolute-rule-failure"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "LLM response failed narrative validation"
    assert calls == 2
    assert store.current_version() == version_before
    assert store.turn_history() == []


def test_provider_payload_contains_relationship_pressure_on_first_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"role": "assistant", "content": "Сцена продолжается."}}]}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *, json: dict, headers: dict) -> FakeResponse:
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(narrative_service.httpx, "AsyncClient", FakeClient)
    settings = Settings(
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=4,
        llm_api_base="https://provider.invalid/v1",
        llm_api_key="test",
        llm_fallback_models=(),
    )
    request = ChatCompletionRequest(messages=[ChatMessage(role="user", content="Я смотрю на Бажену.")])
    neutral = RuleEngine().resolve(
        base_state(),
        Intent(action_type="narrative", desired_outcome="Я смотрю на Бажену."),
        "provider-pressure",
        scenario_type="rp",
        rp_contract_revision=4,
    )[0]

    asyncio.run(
        NarrativeClient(settings).complete(
            request,
            base_state(),
            neutral,
            None,
            relationship_pressure="RELATIONSHIP_PRESSURE\n- Бажена — расположение.",
        )
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert any(
        message["content"].startswith("RELATIONSHIP_PRESSURE")
        for message in payload["messages"]
    )


def test_prompt_inspector_hides_all_relationship_runtime_blocks() -> None:
    messages = [
        {"role": "system", "content": "RELATIONSHIP_PRESSURE\n- private pressure"},
        {"role": "system", "content": "RELATIONSHIP_EVENT_RESOLUTION\n- private resolution"},
        {"role": "user", "content": "visible action"},
    ]

    assert PromptInspector.public_messages(messages) == [
        {"role": "user", "content": "visible action"}
    ]


@pytest.mark.parametrize(
    "marker",
    [
        "player-consumer",
        "npc-consumer",
        "relationship-consumer",
        "faction-consumer",
        "location-consumer",
        "resource-consumer",
        "active-thread-consumer",
        "completed-thread-consumer",
        "uncertain-consumer",
        "absolute-consumer",
    ],
)
def test_retained_rp_state_paths_have_real_prompt_consumers(marker: str) -> None:
    state = base_state()
    state["player"]["known_world_facts"] = ["player-consumer"]
    state["characters"]["king"].update({"name": "King", "knowledge": ["npc-consumer"]})
    state["relationships"] = {
        "player-king": {
            "from": "player",
            "to": "king",
            "trust": 0,
            "suspicion": 0,
            "notes": ["relationship-consumer"],
        }
    }
    state["factions"] = {"faction-consumer": {"status": "active"}}
    state["locations"] = {"location-consumer": {"status": "known"}}
    state["resources"] = {"resource-consumer": {"state": "available"}}
    state["active_threads"] = ["active-thread-consumer"]
    state["completed_threads"] = ["completed-thread-consumer"]
    state["uncertain_facts"] = ["uncertain-consumer"]
    state["world_constraints"] = [
        {
            "id": "absolute-consumer",
            "text": "The marker remains visible.",
            "kind": "absolute",
            "source": "worldpack:test",
            "forbidden_claims": [],
        }
    ]
    settings = Settings(
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=6,
    )
    intent = Intent(action_type="narrative", target="king", desired_outcome="Ask King")
    outcome_value = RuleEngine().resolve(
        state,
        intent,
        "state-consumers",
        scenario_type="rp",
        rp_contract_revision=6,
    )[0]
    messages = NarrativeClient(settings).narrative_messages(
        ChatCompletionRequest(messages=[ChatMessage(role="user", content="King, answer me.")]),
        state,
        outcome_value,
        repair_instruction=None,
    )
    prompt = "\n".join(message["content"] for message in messages)

    assert marker in prompt
    assert "timeline-consumer" not in prompt


def test_revision_six_prompt_is_at_most_half_of_long_raw_transcript_without_mutation(tmp_path: Path) -> None:
    state_path = tmp_path / "long-party.json"
    state_path.write_text(json.dumps(base_state(), ensure_ascii=False), encoding="utf-8")
    store = StateStore(str(tmp_path / "long-party.db"), "long-party", str(state_path))
    for index in range(1, 81):
        store.record_turn(
            f"long-{index}",
            f"long-request-{index}",
            f"Игрок {index}: " + ("намерение и контекст " * 24),
            f"Нарратор {index}: " + ("последствие и диалог " * 24),
            {},
            index,
            party_turn=index,
        )
    raw_before = store.turn_history(limit=200)
    raw_hash_before = hashlib.sha256(
        json.dumps(raw_before, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    full_transcript_chars = sum(
        len(turn["player_message"]) + len(turn["narrative_response"])
        for turn in raw_before
    )
    settings = Settings(
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=6,
        party_memory_fallback_max_chars=400,
        party_memory_retrieval_enabled=False,
    )
    request = party_chat_request(
        store,
        "mock",
        PartyMessageRequest(content="Текущее действие игрока"),
        settings,
    )
    assert request._raw_transcript_chars == full_transcript_chars
    assert "_raw_transcript_chars" not in request.model_dump()
    outcome_value = RuleEngine().resolve(
        store.get_state(),
        Intent(action_type="narrative", desired_outcome="Текущее действие игрока"),
        "long-prompt",
        scenario_type="rp",
        rp_contract_revision=6,
    )[0]
    messages = NarrativeClient(settings).narrative_messages(
        request,
        store.get_state(),
        outcome_value,
        repair_instruction=None,
    )
    prompt_chars = sum(len(message["content"]) for message in messages)
    raw_after = store.turn_history(limit=200)
    raw_hash_after = hashlib.sha256(
        json.dumps(raw_after, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    assert prompt_chars <= full_transcript_chars * 0.5
    assert messages[-1]["content"] == "Текущее действие игрока"
    transcript_roles = [message["role"] for message in messages if message["role"] != "system"]
    assert transcript_roles[:-1] == ["user", "assistant"] * ((len(transcript_roles) - 1) // 2)
    assert len(raw_after) == 80
    assert raw_hash_after == raw_hash_before


def test_revision_six_keeps_latest_history_pair_when_mandatory_prompt_exceeds_half_raw_transcript(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "short-party.json"
    state_path.write_text(json.dumps(base_state(), ensure_ascii=False), encoding="utf-8")
    store = StateStore(str(tmp_path / "short-party.db"), "short-party", str(state_path))
    first_player = "Я осматриваю горящий склад и запоминаю расположение выходов. " * 30
    first_narrator = "Огонь остаётся во дворе, а люди освобождают проход к воротам. " * 30
    latest_player = "Я освобождаю Ратибора из-под балки. " * 45
    latest_narrator = "Ратибор выбрался и теперь стоит рядом с дружинниками. " * 45
    current_action = "Мы ведём Ратибора со двора."
    for index, (player_message, narrative_response) in enumerate(
        ((first_player, first_narrator), (latest_player, latest_narrator)),
        start=1,
    ):
        store.record_turn(
            f"short-{index}",
            f"short-request-{index}",
            player_message,
            narrative_response,
            {},
            index,
            party_turn=index,
        )
    settings = Settings(
        scenario_type="rp",
        rp_contract_version="rp-core.v2",
        rp_contract_revision=6,
        world_system_prompt="Обязательное правило мира: сохраняй непрерывность сцены. " * 800,
        party_memory_retrieval_enabled=False,
    )
    request = party_chat_request(
        store,
        "mock",
        PartyMessageRequest(content=current_action),
        settings,
    )
    outcome_value = RuleEngine().resolve(
        store.get_state(),
        Intent(action_type="narrative", desired_outcome=current_action),
        "short-prompt",
        scenario_type="rp",
        rp_contract_revision=6,
    )[0]
    messages = NarrativeClient(settings).narrative_messages(
        request,
        store.get_state(),
        outcome_value,
        repair_instruction=None,
    )

    assert request._raw_transcript_chars == sum(
        len(message)
        for message in (first_player, first_narrator, latest_player, latest_narrator)
    )
    assert len(settings.world_system_prompt) > request._raw_transcript_chars // 2
    assert [
        (message["role"], message["content"])
        for message in messages
        if message["role"] != "system"
    ] == [
        ("user", latest_player),
        ("assistant", latest_narrator),
        ("user", current_action),
    ]
    assert messages[-1] == {"role": "user", "content": current_action}


def test_relationship_pressure_is_narrator_only_and_party_apis_do_not_leak_it(tmp_path: Path):
    """Proves prompt placement and the absence of relationship internals from party API surfaces."""
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    model = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "worldpacks"
            / "mechanist-new-world"
            / "relationships"
            / "model.json"
        ).read_text(encoding="utf-8")
    )
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["relationships"] = {
        "schema_version": "rp-relationships.v2",
        "model": "relationships/model.json",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    relationship_dir = pack_dir / "relationships"
    relationship_dir.mkdir()
    model["characters"] = {
        "king": {"aliases": ["King", "the king"]},
        "advisor": {"aliases": ["Advisor"]},
    }
    model["plot"]["discovery_chance_per_turn"] = 0
    (relationship_dir / "model.json").write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")

    c = client(tmp_path)
    party = create_demo_party(c)
    party_id = str(party["id"])
    store = c.app.state.party_store.store_for_party(party_id)
    relationships = RelationshipStore(store, model)
    relationships.add_cause(
        character_id="king",
        axis="loyalty",
        event_id="fixture_seed",
        weight=-80,
        turn_id=0,
        party_turn=0,
        expires_turn=None,
        evidence="fixture relationship seed",
        source="fixture",
    )
    relationships.set_axis_state(character_id="king", axis="loyalty", band="enmity", band_since_turn=0)
    relationships.open_event(
        character_id="king",
        axis="loyalty",
        event_id="plot",
        opened_turn=0,
        due_turn=7,
        payload={
            "accomplice_id": "advisor-secret-id",
            "target_id": "player",
            "strike_form": "sabotage",
        },
    )

    response = c.post(
        f"/api/parties/{party_id}/messages",
        json={"content": "Я наблюдаю за придворными.", "idempotency_key": "relationship-pressure-turn"},
    )
    assert response.status_code == 200, response.text
    recorded = store.latest_turn(include_prompt=True)
    prompt = json.loads(recorded["prompt_json"])
    pressure_index = next(index for index, item in enumerate(prompt) if item["content"].startswith("RELATIONSHIP_PRESSURE"))
    state_index = next(index for index, item in enumerate(prompt) if item["content"].startswith("Relevant state summary:"))
    outcome_index = next(index for index, item in enumerate(prompt) if "AUTHORITATIVE_OUTCOME" in item["content"])
    assert state_index < outcome_index < pressure_index == len(prompt) - 2
    pressure = prompt[pressure_index]["content"]
    assert "King" in pressure and "вражда" in pressure
    for forbidden in ("advisor-secret-id", "sabotage", "target_id", "due_turn", "-70", "plot"):
        assert forbidden not in pressure

    request_id = str(recorded["request_id"])
    api_responses = [
        c.get(f"/api/parties/{party_id}"),
        c.get(f"/api/parties/{party_id}/state"),
        c.get(f"/api/parties/{party_id}/history"),
        c.get(f"/api/parties/{party_id}/memory"),
        c.get(f"/api/parties/{party_id}/context"),
        c.get(f"/api/parties/{party_id}/characters"),
        c.get(f"/api/parties/{party_id}/service-jobs"),
        c.get(f"/api/parties/{party_id}/lore-cards"),
        c.get(f"/api/parties/{party_id}/checkpoints"),
        c.get(f"/api/parties/{party_id}/branches"),
        c.get(f"/api/parties/{party_id}/requests/{request_id}"),
        c.post(f"/api/parties/{party_id}/prompt/preview", json={"content": "Следующий ход"}),
    ]
    assert all(item.status_code == 200 for item in api_responses)
    public_payload = "\n".join(item.text for item in api_responses)
    for forbidden in ("RELATIONSHIP_PRESSURE", "вражда", "advisor-secret-id", '"event_id":"plot"'):
        assert forbidden not in public_payload
    assert any(job["job_type"] == "relationship_extraction" for job in store.service_jobs(limit=10))


def test_trust_gained_reaches_next_prompt_despite_global_turn_id_offset_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Proves rev8 trust_gained -> alias-named pressure -> recorded next prompt."""
    pack_dir = write_worldpack(tmp_path, rp_revision=8)
    state_path = pack_dir / "state-seed.json"
    state_seed = json.loads(state_path.read_text(encoding="utf-8"))
    for character in state_seed["characters"].values():
        character.pop("name", None)
        character.pop("display_name", None)
    state_path.write_text(json.dumps(state_seed, ensure_ascii=False), encoding="utf-8")
    model = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "worldpacks"
            / "starosta"
            / "relationships"
            / "model.json"
        ).read_text(encoding="utf-8")
    )
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["relationships"] = {
        "schema_version": "rp-relationships.v2",
        "model": "relationships/model.json",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    relationship_dir = pack_dir / "relationships"
    relationship_dir.mkdir()
    model["characters"] = {
        "king": {"aliases": ["King", "the king"]},
        "advisor": {"aliases": ["Advisor"]},
    }
    (relationship_dir / "model.json").write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")

    target_party_id: str | None = None
    target_narrations = 0
    original_mock_completion = NarrativeClient.mock_completion

    def narrated_trust(self, *args, **kwargs):
        nonlocal target_narrations
        response = original_mock_completion(self, *args, **kwargs)
        if self.settings.prompt_cache_session_id == f"rp-party:{target_party_id}":
            target_narrations += 1
            if target_narrations == 1:
                response["choices"][0]["message"]["content"] = (
                    "The king now trusts you after you kept your word."
                )
        return response

    async def extracted_event(service, turn, *_args, **_kwargs):
        events = []
        evidence = str(turn.get("narrative_response") or "")
        if service.store.campaign_id == target_party_id and "trusts" in evidence:
            events = [{
                "character_mention": "King",
                "event_id": "trust_gained",
                "evidence": evidence,
            }]
        return {
            "model": "fixture",
            "choices": [{"message": {"content": json.dumps({"events": events})}}],
        }

    monkeypatch.setattr(RelationshipExtractionService, "_complete", extracted_event)
    monkeypatch.setattr(NarrativeClient, "mock_completion", narrated_trust)
    c = client(tmp_path, rp_contract_observed_revision=8)
    offset_party = create_demo_party(c, title="Offset Party", character_name="Offset")
    for index in range(3):
        response = c.post(
            f"/api/parties/{offset_party['id']}/messages",
            json={"content": f"offset turn {index}", "idempotency_key": f"offset-{index}"},
        )
        assert response.status_code == 200, response.text

    target = create_demo_party(c, title="Relationship Party", character_name="Target")
    target_party_id = str(target["id"])
    assert target["rp_contract_revision"] == 8
    store = c.app.state.party_store.store_for_party(target_party_id)
    assert all(
        not character.get("name") and not character.get("display_name")
        for character in store.get_state()["characters"].values()
    )
    first = c.post(
        f"/api/parties/{target_party_id}/messages",
        json={"content": "The king now trusts me after I kept my word.", "idempotency_key": "relationship-positive"},
    )
    assert first.status_code == 200, first.text
    second = c.post(
        f"/api/parties/{target_party_id}/messages",
        json={"content": "I watch his reaction.", "idempotency_key": "relationship-pressure"},
    )
    assert second.status_code == 200, second.text

    prompt = json.loads(store.latest_turn(include_prompt=True)["prompt_json"])
    pressure = next(
        (item["content"] for item in prompt if item["content"].startswith("RELATIONSHIP_PRESSURE")),
        None,
    )
    assert pressure is not None, "RELATIONSHIP_PRESSURE did not appear after trust_gained"
    assert "King" in pressure and "Недавние поступки укрепили доверие" in pressure
    metadata = latest_turn_metadata(store)
    assert "relationship_pressure" in metadata["prompt_assembly"]["included_block_ids"]
    causes = RelationshipStore(store, model).cause_rows("king", "loyalty", 2)
    assert any(row["event_id"] == "trust_gained" and row["weight"] > 0 for row in causes)
    with store.connect() as connection:
        cause = connection.execute(
            "SELECT turn_id, party_turn, expires_turn FROM relationship_causes "
            "WHERE campaign_id = ? AND source = 'extraction' ORDER BY id ASC LIMIT 1",
            (target_party_id,),
        ).fetchone()
    assert cause is not None
    assert int(cause["turn_id"]) > int(cause["party_turn"]) == 1
    assert int(cause["expires_turn"]) == 41
    assert RelationshipStore(store, model).axis_state("king", "loyalty") == {
        "campaign_id": target_party_id,
        "character_id": "king",
        "axis": "loyalty",
        "band": "neutral",
        "band_since_turn": 0,
        "updated_at": RelationshipStore(store, model).axis_state("king", "loyalty")["updated_at"],
    }
    assert RelationshipStore(store, model).event_rows("king", "favour") == []


def test_training_party_ignores_relationship_declaration_and_writes_no_relationship_rows(tmp_path: Path):
    """Proves the full Training lifecycle cannot activate the RP relationship layer."""
    source = Path(__file__).resolve().parents[2] / "worldpacks" / "awareness-one-day"
    target = tmp_path / "worldpacks" / "awareness-one-day"
    shutil.copytree(source, target)
    model_source = (
        Path(__file__).resolve().parents[2]
        / "worldpacks"
        / "mechanist-new-world"
        / "relationships"
        / "model.json"
    )
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["relationships"] = {
        "schema_version": "rp-relationships.v2",
        "model": "relationships/model.json",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    relationship_dir = target / "relationships"
    relationship_dir.mkdir()
    shutil.copy2(model_source, relationship_dir / "model.json")

    c = client(tmp_path, mode="repair-fail")
    party = create_demo_party(
        c,
        title="Training relationship guard",
        character_name="Эллина",
        scenario_type="training",
        worldpack_id="awareness-one-day",
    )
    started = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "training-relationship-start"},
    )
    assert started.status_code == 200, started.text
    message = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Проверяю сообщение.", "idempotency_key": "training-relationship-turn"},
    )
    assert message.status_code == 200, message.text

    store = c.app.state.party_store.store_for_party(str(party["id"]))
    with store.connect() as connection:
        counts = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "relationship_causes",
                "character_badges",
                "narrative_events",
                "character_axis_state",
            )
        }
    assert counts == {table: 0 for table in counts}
    assert all(job["job_type"] != "relationship_extraction" for job in store.service_jobs(limit=20))
    recorded = store.latest_turn(include_prompt=True)
    assert "RELATIONSHIP_PRESSURE" not in str(recorded.get("prompt_json") or "")


def test_party_requires_manual_scenario_type_and_rejects_unsupported_mode(tmp_path: Path):
    write_worldpack(tmp_path, supported_modes=["rp"])
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
    assert c.post("/api/parties", json={**base_payload, "scenario_type": "novel"}).status_code == 422
    assert c.post("/api/parties", json={**base_payload, "scenario_type": "training"}).status_code == 400
    created = c.post("/api/parties", json={**base_payload, "scenario_type": "rp"})
    assert created.status_code == 200
    assert created.json()["party"]["scenario_type"] == "rp"


def test_existing_parties_migrate_without_campaign_specific_scenario_inference(tmp_path: Path):
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
        columns = {row[1] for row in connection.execute("PRAGMA table_info(parties)")}
        narrator_settings = dict(connection.execute("SELECT id, narrator_settings_json FROM parties").fetchall())

    assert migrated == {"old-awareness": "rp", "old-rp": "rp"}
    assert "narrator_settings_json" in columns
    assert narrator_settings == {"old-awareness": "{}", "old-rp": "{}"}


def test_rp_party_after_turn_10_keeps_narrator_response_and_honest_metadata(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Long RP party", scenario_type="rp")
    store = c.app.state.party_store.store_for_party(party["id"])
    state = store.get_state()
    state["meta"]["turn"] = 10
    state["meta"]["state_version"] = 2
    store.insert_state_version(state, "test:rp-turn-10")
    store.write_state_file(state)

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Continue the scene", "idempotency_key": "rp-turn-11"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_rp_turn_11"},
    )

    assert response.status_code == 200, response.text
    expected = "The scene shifts around the attempt, leaving the next opening clear without taking control from the player."
    assert response.json()["message"]["content"] == expected
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        row = connection.execute(
            "SELECT narrative_response, metadata_json FROM turns "
            "WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
            (party["id"],),
        ).fetchone()
    metadata = json.loads(row[1])
    assert row[0] == expected
    assert metadata["scenario_type"] == "rp"
    assert metadata["fallback"] is False
    assert metadata["fallback_reason"] is None
    assert metadata["validator_valid"] is None
    assert metadata["repaired"] is False
    assert metadata["llm_calls"] == 1
    assert metadata["transport_status"] == "ok"


def test_rp_party_runs_twelve_turns_without_training_or_fallback_metadata(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Twelve-turn RP party", scenario_type="rp")

    for turn in range(1, 13):
        response = c.post(
            f"/api/parties/{party['id']}/messages",
            json={"content": f"Continue scene {turn}", "idempotency_key": f"rp-long-{turn}"},
            headers={"Authorization": "Bearer test", "X-Request-ID": f"req_rp_long_{turn}"},
        )
        assert response.status_code == 200, response.text

    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        rows = connection.execute(
            "SELECT metadata_json FROM turns WHERE campaign_id = ? ORDER BY id",
            (party["id"],),
        ).fetchall()
    metadata = [json.loads(row[0]) for row in rows]
    assert len(metadata) == 12
    assert {item["scenario_type"] for item in metadata} == {"rp"}
    assert {item["training_runtime_contract_hash"] for item in metadata} == {None}
    assert all(item["fallback"] is False for item in metadata)
    assert all(item["fallback_reason"] is None for item in metadata)
    assert all(item["validator_valid"] is None for item in metadata)
    assert all(item["repaired"] is False for item in metadata)
    assert {item["transport_status"] for item in metadata} == {"ok"}


def test_party_complete_is_idempotent_retains_history_and_state_and_can_reactivate(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Completable party", scenario_type="rp")
    turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Remember this scene", "idempotency_key": "before-complete"},
        headers={"Authorization": "Bearer test"},
    )
    assert turn.status_code == 200, turn.text
    state_before = c.get(f"/api/parties/{party['id']}/state").json()["state"]
    history_before = c.get(f"/api/parties/{party['id']}/history").json()["turns"]

    first = c.post(f"/api/parties/{party['id']}/complete")
    second = c.post(f"/api/parties/{party['id']}/complete")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["party"]["status"] == "completed"
    assert second.json()["party"]["status"] == "completed"
    assert second.json()["party"]["updated_at"] == first.json()["party"]["updated_at"]
    assert c.get(f"/api/parties/{party['id']}/state").json()["state"] == state_before
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == history_before

    activated = c.post(f"/api/parties/{party['id']}/activate")

    assert activated.status_code == 200, activated.text
    assert activated.json()["party"]["status"] == "active"
    assert c.get(f"/api/parties/{party['id']}/state").json()["state"] == state_before
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == history_before


def test_rp_party_after_turn_10_keeps_narrator_response(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Long RP party", scenario_type="rp")
    store = c.app.state.party_store.store_for_party(party["id"])
    state = store.get_state()
    state["meta"]["turn"] = 10
    state["meta"]["state_version"] = 2
    store.insert_state_version(state, "test:rp-turn-10")
    store.write_state_file(state)

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Continue the scene", "idempotency_key": "rp-turn-11"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_rp_turn_11"},
    )

    assert response.status_code == 200, response.text
    expected = "The scene shifts around the attempt, leaving the next opening clear without taking control from the player."
    assert response.json()["message"]["content"] == expected
    turn = c.get(f"/api/parties/{party['id']}/history").json()["turns"][-1]
    assert turn["narrative_response"] == expected


def test_legacy_novel_party_is_archived_readable_and_terminal(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Legacy Novel", scenario_type="rp")
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        connection.execute(
            "UPDATE parties SET scenario_type = 'novel', status = 'active' WHERE id = ?",
            (party["id"],),
        )

    for _ in range(2):
        c.app.state.party_store.init_db()
    migrated = c
    stored = migrated.get(f"/api/parties/{party['id']}")
    assert stored.status_code == 200, stored.text
    assert stored.json()["party"]["scenario_type"] == "novel"
    assert stored.json()["party"]["status"] == "archived"
    listed = migrated.get("/api/parties").json()["parties"]
    assert any(item["id"] == party["id"] and item["scenario_type"] == "novel" for item in listed)

    activated = migrated.post(f"/api/parties/{party['id']}/activate")
    started = migrated.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "legacy-novel-start"},
    )
    messaged = migrated.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "This must not run."},
    )
    for response in (activated, started, messaged):
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "archived party is terminal"


def test_scenario_system_prompts_have_distinct_contracts():
    rp_rules = NarrativeClient(Settings(scenario_type="rp")).scenario_rules()
    training_rules = NarrativeClient(Settings(scenario_type="training")).scenario_rules()

    assert "D20" in rp_rules
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
    byok = alice.post(
        f"/api/parties/{alice_party['id']}/byok",
        json={"label": "Alice OpenRouter", "api_key": "alice-party-key", "provider": "openrouter"},
    )
    assert byok.status_code == 200, byok.text
    assert alice.get(f"/api/parties/{alice_party['id']}/byok").json()["api_keys"][0]["secret_hint"] == "-key"
    assert admin.get(f"/api/parties/{alice_party['id']}/byok").status_code == 404
    alice_parties = alice.get("/api/parties").json()["parties"]
    assert [party["id"] for party in alice_parties] == [alice_party["id"]]
    assert admin.get("/api/parties").json()["parties"][0]["id"] == admin_party["id"]

    users = admin.get("/api/admin/users").json()["users"]
    alice_summary = next(user for user in users if user["username"] == "alice")
    assert alice_summary["party_count"] == 1


def test_admin_selects_one_service_model_for_entire_stack(tmp_path: Path):
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
        service_openrouter_api_key="stack-openrouter-key",
        local_llm_enabled=True,
        local_llm_base_url="http://local-service/v1",
    )
    login(admin)
    admin.post("/api/admin/users", json={"username": "alice", "password": "alice-secret", "role": "user"})

    before = admin.get("/api/admin/global-settings/service-model")
    assert before.status_code == 200
    assert before.json()["term"] == "Служебная модель"
    assert len(before.json()["choices"]) == 11

    selected = admin.patch(
        "/api/admin/global-settings/service-model",
        json={"choice_id": "or-qwen-3.5-flash"},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["selected"]["model"] == "qwen/qwen3.5-flash-02-23"
    assert admin.app.state.auth_store.get_global_setting(SERVICE_MODEL_SETTING_KEY) == "or-qwen-3.5-flash"

    runtime = service_model_settings(replace(admin.app.state.settings, service_model_choice="or-qwen-3.5-flash"))
    assert runtime.llm_provider == "openrouter"
    assert runtime.llm_api_key == "stack-openrouter-key"
    assert runtime.narrative_model == "qwen/qwen3.5-flash-02-23"

    alice = TestClient(admin.app)
    login(alice, "alice", "alice-secret")
    assert alice.get("/api/admin/global-settings/service-model").status_code == 403
    assert alice.patch(
        "/api/admin/global-settings/service-model",
        json={"choice_id": "local-gemma"},
    ).status_code == 403


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


def test_admin_autotest_forks_checkpoint_branch_with_separate_local_player_model(tmp_path: Path):
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
    source_turn = admin.post(
        f"/api/parties/{source_party['id']}/messages",
        json={"content": "I inspect the room before the branch."},
        headers={"Authorization": "Bearer test"},
    )
    assert source_turn.status_code == 200, source_turn.text
    source_history_before = admin.get(f"/api/parties/{source_party['id']}/history").json()["turns"]
    source_state_before = admin.get(f"/api/parties/{source_party['id']}/state").json()["state"]
    party_ids_before = {party["id"] for party in admin.get("/api/parties").json()["parties"]}

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
    branch = started.json()["branch"]
    checkpoint = started.json()["checkpoint"]
    assert run["source_party_id"] == source_party["id"]
    assert run["branch_id"] == branch["id"]
    assert run["checkpoint_id"] == checkpoint["id"]
    assert branch["party_id"] == source_party["id"]
    assert branch["source_checkpoint_id"] == checkpoint["id"]
    assert run["player_model_profile_id"] == local_profile["id"]
    assert {party["id"] for party in admin.get("/api/parties").json()["parties"]} == party_ids_before
    other_party = create_demo_party(admin, title="Other autotest party")
    scoped_runs = admin.get(f"/api/admin/autotests?source_party_id={source_party['id']}").json()["runs"]
    assert [item["id"] for item in scoped_runs] == [run["id"]]
    assert admin.get(f"/api/admin/autotests?source_party_id={other_party['id']}").json()["runs"] == []
    assert admin.get("/api/admin/autotests?source_party_id=missing-party").status_code == 404

    deadline = time.time() + 3
    while time.time() < deadline:
        runs = admin.get("/api/admin/autotests").json()["runs"]
        run = next(item for item in runs if item["id"] == run["id"])
        if run["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)
    assert run["status"] == "completed", run
    assert run["completed_turns"] == 1
    assert admin.get(f"/api/parties/{source_party['id']}/history").json()["turns"] == source_history_before
    assert admin.get(f"/api/parties/{source_party['id']}/state").json()["state"] == source_state_before
    branch_response = admin.get(f"/api/parties/{source_party['id']}/branches/{branch['id']}")
    assert branch_response.status_code == 200, branch_response.text
    branch_payload = branch_response.json()
    assert branch_payload["state"]["meta"]["campaign_id"] == branch["state_campaign_id"]
    assert branch_payload["state"]["meta"]["branch_parent_campaign_id"] == source_party["id"]
    assert len(branch_payload["turns"]) == len(source_history_before) + 1
    assert branch_payload["turns"][0]["player_message"] == source_history_before[0]["player_message"]
    assert branch_payload["turns"][-1]["player_message"].startswith("I examine the situation")
    listed_branches = admin.get(f"/api/parties/{source_party['id']}/branches").json()["branches"]
    assert [item["id"] for item in listed_branches] == [branch["id"]]
    assert listed_branches[0]["status"] == "completed"


def test_admin_rp_autotest_ignores_semantic_validator_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    write_worldpack(tmp_path)
    admin = client(
        tmp_path,
        mode="repair-fail",
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
        local_llm_enabled=True,
        local_llm_base_url="mock://success",
        local_llm_model_alias="gemma-4-26b-a4b-it-rp-q4",
    )
    login(admin)

    async def failing_check_action(*args: object, **kwargs: object) -> str:
        return "I take the next action."

    def reject_narrative(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            valid=False,
            violations=["forced invalid narrative for fallback coverage"],
            repair_instruction="Return a valid narrative.",
        )

    monkeypatch.setattr("app.main.AutoPlayerClient.next_action", failing_check_action)
    monkeypatch.setattr("app.services.adjudicator.OutputValidator.validate", reject_narrative)
    source_party = create_demo_party(admin, title="Fallback autotest source")
    local_profile = next(
        profile
        for profile in admin.get("/api/admin/autotests/models").json()["model_profiles"]
        if profile["provider"] == "local"
    )

    started = admin.post(
        "/api/admin/autotests",
        json={
            "source_party_id": source_party["id"],
            "player_prompt": "Take the next in-world action only.",
            "turn_count": 1,
            "player_model_profile_id": local_profile["id"],
        },
    )
    assert started.status_code == 200, started.text
    run = started.json()["run"]

    deadline = time.time() + 3
    while time.time() < deadline:
        run = next(
            item
            for item in admin.get("/api/admin/autotests").json()["runs"]
            if item["id"] == run["id"]
        )
        if run["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)

    assert run["status"] == "completed", run
    assert run["completed_turns"] == 1
    assert run["fallback_turns"] == 0
    branch_store = admin.app.state.party_store.store_for_branch(
        source_party["id"],
        run["branch_id"],
        owner_user_id=run["owner_user_id"],
    )
    latest_turn = branch_store.latest_turn(include_response=True)
    response = json.loads(latest_turn["response_json"])
    assert response["choices"][0]["finish_reason"] == "stop"
    metadata = latest_turn_metadata(branch_store)
    assert metadata["validator_valid"] is None
    assert metadata["fallback"] is False
    assert metadata["transport_status"] == "ok"


def test_admin_revision_six_rp_autotest_does_not_commit_validation_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 6}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
        local_llm_enabled=True,
        local_llm_base_url="mock://success",
        local_llm_model_alias="gemma-4-26b-a4b-it-rp-q4",
        rp_contract_observed_revision=6,
    )
    login(admin)

    async def next_action(*args: object, **kwargs: object) -> str:
        return "I take the next action."

    def reject_narrative(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            valid=False,
            violations=["forced repeated absolute-rule violation"],
            repair_instruction="Respect the absolute rule.",
        )

    monkeypatch.setattr("app.main.AutoPlayerClient.next_action", next_action)
    monkeypatch.setattr("app.services.adjudicator.OutputValidator.validate", reject_narrative)
    source_party = create_demo_party(admin, title="Revision 6 invalid autotest")
    assert source_party["rp_contract_revision"] == 6
    local_profile = next(
        profile
        for profile in admin.get("/api/admin/autotests/models").json()["model_profiles"]
        if profile["provider"] == "local"
    )

    started = admin.post(
        "/api/admin/autotests",
        json={
            "source_party_id": source_party["id"],
            "player_prompt": "Take the next in-world action only.",
            "turn_count": 1,
            "player_model_profile_id": local_profile["id"],
        },
    )
    assert started.status_code == 200, started.text
    run = started.json()["run"]

    deadline = time.time() + 3
    while time.time() < deadline:
        run = next(
            item
            for item in admin.get("/api/admin/autotests").json()["runs"]
            if item["id"] == run["id"]
        )
        if run["status"] in {"completed", "failed"}:
            break
        time.sleep(0.02)

    assert run["status"] == "failed", run
    assert run["completed_turns"] == 0
    assert run["fallback_turns"] == 0
    branch_store = admin.app.state.party_store.store_for_branch(
        source_party["id"],
        run["branch_id"],
        owner_user_id=run["owner_user_id"],
    )
    assert branch_store.turn_history(limit=10) == []


def test_party_byok_is_scoped_to_current_party(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(
        tmp_path,
        api_key="",
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(c)
    first = create_demo_party(c, title="BYOK A", character_name="A")
    second = create_demo_party(c, title="BYOK B", character_name="B")
    retired = c.post(
        f"/api/parties/{first['id']}/byok",
        json={"label": "Retired provider", "api_key": "must-not-store", "provider": "nvidia"},
    )
    assert retired.status_code == 422
    response = c.post(
        f"/api/parties/{first['id']}/byok",
        json={"label": "Test OpenRouter", "api_key": "managed-provider-key", "provider": "openrouter", "is_default": True},
    )
    assert response.status_code == 200, response.text
    key = response.json()["api_key"]
    assert "api_key" not in key
    assert key["secret_hint"] == "-key"

    assert c.get(f"/api/parties/{first['id']}/byok").json()["api_keys"][0]["id"] == key["id"]
    assert c.get(f"/api/parties/{second['id']}/byok").json()["api_keys"] == []
    first_party = c.app.state.party_store.get_party(first["id"])
    second_party = c.app.state.party_store.get_party(second["id"])
    assert c.app.state.auth_store.default_provider_secret(
        provider="openrouter", owner_user_id=first_party.owner_user_id, party_id=first["id"]
    ) == "managed-provider-key"
    assert c.app.state.auth_store.default_provider_secret(
        provider="openrouter", owner_user_id=second_party.owner_user_id, party_id=second["id"]
    ) is None


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


def test_revision_eight_party_start_keeps_plain_opening_as_assistant_only_in_next_prompt(
    tmp_path: Path,
) -> None:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 8}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    c = client(tmp_path, rp_contract_observed_revision=8)
    party = create_demo_party(c, title="Revision 8 opening")

    started = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "rev8-opening"},
        headers={"X-Request-ID": "req_rev8_opening"},
    )

    assert started.status_code == 200, started.text
    opening_text = started.json()["message"]["content"]
    assert opening_text
    assert not opening_text.lstrip().startswith(("{", "```json"))

    continued = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Я осматриваю комнату.", "idempotency_key": "rev8-after-opening"},
        headers={"X-Request-ID": "req_rev8_after_opening"},
    )

    assert continued.status_code == 200, continued.text
    store = c.app.state.party_store.store_for_party(str(party["id"]))
    with store.connect() as connection:
        turns = connection.execute(
            """
            SELECT player_message, narrative_response, prompt_json, metadata_json
            FROM turns
            WHERE campaign_id = ?
            ORDER BY id ASC
            """,
            (store.campaign_id,),
        ).fetchall()
        attempt = connection.execute(
            """
            SELECT payload_json
            FROM turn_trace_events
            WHERE campaign_id = ? AND request_id = ?
              AND phase_key = 'narrator:attempt:1'
            """,
            (store.campaign_id, "req_rev8_after_opening"),
        ).fetchone()

    assert len(turns) == 2
    assert turns[0]["player_message"] == AUTO_START_HISTORY_MESSAGE
    assert turns[0]["narrative_response"] == opening_text
    opening_metadata = json.loads(turns[0]["metadata_json"])
    assert opening_metadata["turn_kind"] == "opening_scene"
    assert opening_metadata["rp_contract_revision"] == 8
    for removed_scene_key in ("scene_claims", "scene_state_before", "scene_state_after"):
        assert removed_scene_key not in opening_metadata
    assert "scene_state" not in c.get(f"/api/parties/{party['id']}/state").json()["state"]

    assert attempt is not None
    provider_messages = json.loads(attempt["payload_json"])["input"]["payload"]["messages"]
    stored_messages = json.loads(turns[1]["prompt_json"])
    for messages in (provider_messages, stored_messages):
        assert not any(
            message["role"] == "user" and message["content"] == AUTO_START_HISTORY_MESSAGE
            for message in messages
        )
        assert [
            message
            for message in messages
            if message["role"] == "assistant" and message["content"] == opening_text
        ] == [{"role": "assistant", "content": opening_text}]
        serialized = json.dumps(messages, ensure_ascii=False)
        for removed_scene_marker in (
            "SCENE_STATE",
            "SCENE_TRANSITION_ALLOWANCE",
            "scene_claims",
            "scene_delta",
            "rp-gateway.rp-narrator-bundle",
        ):
            assert removed_scene_marker not in serialized


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
    assert models[0]["model"] == "gemini-3.6-flash"
    assert models[0]["rp_fit"]
    assert models[0]["context_window"]
    assert "reasoning" in models[0]["tags"]


def test_model_profiles_are_grouped_by_supported_providers_and_filter_small_models(tmp_path: Path):
    c = client(tmp_path)
    models = c.get("/api/model-profiles").json()["model_profiles"]

    assert {model["provider"] for model in models} == {"gemini", "openrouter"}
    assert all(model["context_window"] for model in models)
    assert not any(model["model"] == "openai/gpt-oss-20b" for model in models)


def test_historical_nvidia_profile_party_and_log_are_readable_but_runtime_is_disabled(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party = create_demo_party(c, title="Historical provider row")
    ServiceModelClient(c.app.state.settings)
    timestamp = "2026-08-01T00:00:00Z"
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        connection.execute(
            """
            INSERT INTO model_profiles(
                id, title, provider, base_url, model, params_json,
                api_key_source, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-nvidia-profile",
                "Historical NVIDIA profile",
                "nvidia",
                "https://retired-provider.invalid/v1",
                "historical/model",
                json.dumps({"context_tokens": 131072, "source": "historical"}),
                "historical",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            "UPDATE parties SET model_profile_id = ? WHERE id = ?",
            ("legacy-nvidia-profile", party["id"]),
        )
        connection.execute(
            """
            INSERT INTO service_call_log(
                party_id, turn_id, role, prompt_text, raw_response, created_at,
                status, provider, model, trace_schema_version
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                party["id"],
                1,
                "memory_summary",
                "historical prompt",
                "historical response",
                timestamp,
                "completed",
                "nvidia",
                "historical/model",
                "rp-gateway.service-call.v1",
            ),
        )

    stored = c.get(f"/api/parties/{party['id']}")
    assert stored.status_code == 200, stored.text
    assert stored.json()["party"]["model_profile"]["provider"] == "nvidia"
    assert all(
        profile["provider"] != "nvidia"
        for profile in c.get("/api/model-profiles").json()["model_profiles"]
    )
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        historical_log = connection.execute(
            "SELECT provider, model, raw_response FROM service_call_log WHERE party_id = ?",
            (party["id"],),
        ).fetchone()
    assert historical_log == ("nvidia", "historical/model", "historical response")

    rejected_create = c.post(
        "/api/parties",
        json={
            "title": "Must stay disabled",
            "scenario_type": "rp",
            "worldpack_id": party["worldpack_id"],
            "player_character_id": party["player_character_id"],
            "model_profile_id": "legacy-nvidia-profile",
        },
    )
    assert rejected_create.status_code == 400
    assert "provider is retired" in rejected_create.json()["detail"]
    for response in (
        c.post(f"/api/parties/{party['id']}/activate"),
        c.post(f"/api/parties/{party['id']}/start"),
        c.post(f"/api/parties/{party['id']}/messages", json={"content": "Must not route."}),
    ):
        assert response.status_code == 409, response.text
        assert "provider is retired" in response.json()["detail"]


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
    assert provider_model_is_suitable("openrouter", "anthropic/claude-opus-5:batch", specialist) is False
    assert prices_are_free("0", "0.000000") is True


def test_openrouter_batch_profiles_are_hidden_from_model_registries(tmp_path: Path):
    c = client(tmp_path)
    party_store = c.app.state.party_store
    for model_id in ("anthropic/claude-opus-5", "anthropic/claude-opus-5:batch"):
        party_store.upsert_model_profile(
            {
                "id": model_id.replace("/", "-").replace(":", "-"),
                "title": model_id,
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "model": model_id,
                "params": {"context_tokens": 1_000_000, "source": "openrouter_api_live"},
                "api_key_source": "server_env_or_managed_key",
            }
        )

    party_models = c.get("/api/model-profiles").json()["model_profiles"]
    autotest_models = c.get("/api/admin/autotests/models").json()["model_profiles"]

    assert any(model["model"] == "anthropic/claude-opus-5" for model in party_models)
    assert not any(model["model"].endswith(":batch") for model in party_models)
    assert any(model["model"] == "anthropic/claude-opus-5" for model in autotest_models)
    assert not any(model["model"].endswith(":batch") for model in autotest_models)


def test_featured_openrouter_models_enrich_cached_catalog_profiles():
    params = {"tags": ["live OpenRouter"], "rp_fit": "stale description"}

    enriched = enrich_openrouter_profile_params("z-ai/glm-5.2", params)

    assert len(OPENROUTER_FEATURED_MODELS) == 10
    assert params == {"tags": ["live OpenRouter"], "rp_fit": "stale description"}
    assert enriched["featured_rank"] == 1
    assert enriched["title_override"] == "GLM 5.2"
    assert enriched["rp_fit"] != "stale description"
    assert enriched["tags"][:2] == ["Избранное", "длинная кампания"]


def test_openrouter_catalog_preserves_prompt_cache_prices(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {
                        "id": "deepseek/deepseek-v4-pro",
                        "name": "DeepSeek V4 Pro",
                        "description": "Long-context reasoning and roleplay model.",
                        "context_length": 1_048_576,
                        "architecture": {"output_modalities": ["text"]},
                        "pricing": {
                            "prompt": "0.00000063168",
                            "completion": "0.00000126336",
                            "input_cache_read": "0.000000053298",
                            "input_cache_write": "0.0000008",
                            "input_cache_write_1h": "0.000001",
                        },
                    }
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def get(self, url, headers):
            assert url == "https://openrouter.test/api/v1/models"
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_catalog.httpx.Client", FakeClient)

    profiles = fetch_provider_api_profiles(
        Settings(openrouter_api_base="https://openrouter.test/api/v1"),
        "openrouter",
    )

    assert len(profiles) == 1
    assert profiles[0]["params"]["pricing_prompt"] == "0.00000063168"
    assert profiles[0]["params"]["pricing_completion"] == "0.00000126336"
    assert profiles[0]["params"]["pricing_input_cache_read"] == "0.000000053298"
    assert profiles[0]["params"]["pricing_input_cache_write"] == "0.0000008"
    assert profiles[0]["params"]["pricing_input_cache_write_1h"] == "0.000001"


def test_gemini_party_does_not_inherit_openrouter_fallbacks():
    settings = Settings(
        openrouter_fallback_models=("openrouter/auto",),
        llm_disabled_models=("gemini-3.6-flash",),
        model_attempt_timeout_seconds=150,
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
    assert selected.llm_fallback_models == ()
    assert selected.llm_disabled_models == ()
    assert selected.model_attempt_timeout_seconds == 150


def test_openrouter_party_uses_same_provider_fallbacks():
    settings = Settings(openrouter_fallback_models=("openrouter/auto",))
    party = SimpleNamespace(
        scenario_type="rp",
        worldpack_id="demo-world",
        worldpack=None,
        model_profile=SimpleNamespace(
            provider="openrouter",
            base_url="https://openrouter.ai/api/v1",
            model="deepseek/deepseek-v4-flash",
        ),
    )

    selected = settings_for_party(settings, party)

    assert selected.llm_provider == "openrouter"
    assert selected.llm_fallback_models == ("openrouter/auto",)
    assert selected.llm_disabled_models == ()


def test_party_narrator_deadline_overrides_local_service_deadline():
    settings = Settings(
        model_attempt_timeout_seconds=150,
        local_llm_timeout_seconds=240,
    )
    party = SimpleNamespace(
        scenario_type="rp",
        worldpack_id="demo-world",
        worldpack=None,
        model_profile=SimpleNamespace(
            provider="local",
            base_url="http://rp-local-llm:8080/v1",
            model="gemma-local",
        ),
    )

    selected = settings_for_party(settings, party)

    assert selected.llm_provider == "local"
    assert selected.model_attempt_timeout_seconds == 150


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
        "RP_STORY_MEMORY_UPDATE_TURNS",
        "RP_STORY_MEMORY_BATCH_TOKENS",
        "RP_STORY_MEMORY_MAX_TOKENS",
        "RP_STORY_MEMORY_MAX_CHARS",
        "RP_STORY_MEMORY_PROMPT_MAX_CHARS",
        "RP_STORY_MEMORY_RESERVE_TOKENS",
        "POST_TURN_HELPERS_INLINE",
    ]:
        monkeypatch.delenv(name, raising=False)
    settings = Settings()
    assert settings.post_turn_helpers_inline is False
    assert settings.party_context_max_tokens == 131_072
    assert settings.effective_party_context_limit_tokens == 131_072
    assert settings.effective_party_history_token_budget == 71_920
    assert settings.memory_summary_batch_tokens == 10_000
    assert settings.party_memory_chapter_max_tokens == 6_000
    assert settings.party_memory_chapter_max_chars == 24_000
    assert settings.party_memory_prompt_max_chars == 60_000
    assert settings.party_memory_retrieval_enabled is True
    assert settings.rp_story_memory_update_turns == 4
    assert settings.rp_story_memory_batch_tokens == 6_000
    assert settings.rp_story_memory_max_tokens == 6_000
    assert settings.rp_story_memory_prompt_max_chars == 24_000
    assert settings.rp_story_memory_reserve_tokens == 10_000


def test_rp_story_memory_api_fields_are_absent_from_training(tmp_path: Path):
    write_worldpack(tmp_path, supported_modes=["rp", "training"])
    c = client(tmp_path)
    rp_party = create_demo_party(c, title="RP memory", scenario_type="rp")
    training_party = create_demo_party(
        c,
        title="Training without RP memory",
        character_name="Trainee",
        scenario_type="training",
    )

    rp_memory = c.get(f"/api/parties/{rp_party['id']}/memory").json()
    training_memory = c.get(f"/api/parties/{training_party['id']}/memory").json()
    rp_context = c.get(f"/api/parties/{rp_party['id']}/context").json()["context"]
    training_context = c.get(f"/api/parties/{training_party['id']}/context").json()["context"]

    assert "story_memory" in rp_memory
    assert rp_memory["story_memory_stats"]["enabled"] is True
    assert "rp_story_memory_tokens" in rp_context
    assert "story_memory" not in training_memory
    assert "story_memory_stats" not in training_memory
    assert "rp_story_memory_tokens" not in training_context
    assert training_context["history_token_budget"] == 81_920


def test_typed_story_memory_replace_forces_cadence_and_reaches_later_prompt(tmp_path: Path):
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 2}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    c = client(tmp_path, rp_story_memory_update_turns=4)
    party = create_demo_party(c, title="Typed memory correction")
    assert party["rp_contract_revision"] == 2
    store = c.app.state.party_store.store_for_party(party["id"])
    seed_incorrect_story_fact(store)
    correction_payload = {
        "content": "Please continue after applying this explicit memory correction.",
        "idempotency_key": "typed-memory-correction-1",
        "story_memory_corrections": [
            {
                "field": "canon",
                "fact_id": "fact:incorrect-gate",
                "action": "replace",
                "replacement_text": "The gate is open.",
            }
        ],
    }

    corrected = c.post(
        f"/api/parties/{party['id']}/messages",
        json=correction_payload,
        headers={"X-Request-ID": "typed-memory-correction-request-1"},
    )
    assert corrected.status_code == 200, corrected.text
    first_turn = store.turn_history(limit=10)[0]
    metadata = latest_turn_metadata(store)
    assert metadata["story_memory_corrections"] == correction_payload["story_memory_corrections"]

    snapshot = store.latest_rp_story_memory()
    assert snapshot is not None
    assert snapshot["revision"] == 2
    assert snapshot["to_turn_id"] == first_turn["id"]
    canon = snapshot["memory"]["canon"]
    assert canon[0] == {
        "fact_id": "fact:incorrect-gate",
        "text": "The gate is closed.",
        "status": "superseded",
        "authority": "user_correction",
        "source_turn_ids": [first_turn["id"]],
    }
    assert canon[1]["fact_id"] != "fact:incorrect-gate"
    assert canon[1]["text"] == "The gate is open."
    assert canon[1]["status"] == "active"
    assert canon[1]["authority"] == "user_correction"
    assert canon[1]["source_turn_ids"] == [first_turn["id"]]

    duplicate = c.post(
        f"/api/parties/{party['id']}/messages",
        json=correction_payload,
        headers={"X-Request-ID": "typed-memory-correction-request-duplicate"},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert len(store.turn_history(limit=10)) == 1
    assert len(store.rp_story_memories(limit=10)) == 2

    next_turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "I inspect the doorway.", "idempotency_key": "typed-memory-next-turn"},
        headers={"X-Request-ID": "typed-memory-next-request"},
    )
    assert next_turn.status_code == 200, next_turn.text
    with sqlite3.connect(store.sqlite_path) as connection:
        row = connection.execute(
            "SELECT prompt_json FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
            (store.campaign_id,),
        ).fetchone()
    assert row is not None
    prompt = row[0]
    assert "The gate is open." in prompt
    assert "The gate is closed." not in prompt


def test_pending_typed_story_memory_replace_reaches_prompt_before_service_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 2}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        Adjudicator,
        "schedule_service_jobs",
        lambda self, authorization=None: None,
    )
    c = client(tmp_path, post_turn_helpers_inline=False, rp_story_memory_update_turns=4)
    party = create_demo_party(c, title="Pending typed memory correction")
    store = c.app.state.party_store.store_for_party(party["id"])
    seed_incorrect_story_fact(store)

    corrected = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": "Apply this explicit correction and continue.",
            "idempotency_key": "pending-memory-correction",
            "story_memory_corrections": [
                {
                    "field": "canon",
                    "fact_id": "fact:incorrect-gate",
                    "action": "replace",
                    "replacement_text": "The gate is open.",
                }
            ],
        },
    )
    assert corrected.status_code == 200, corrected.text
    with sqlite3.connect(store.sqlite_path) as connection:
        correction_prompt = connection.execute(
            "SELECT prompt_json FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
            (store.campaign_id,),
        ).fetchone()
    assert correction_prompt is not None
    assert "The gate is open." in correction_prompt[0]
    assert "The gate is closed." not in correction_prompt[0]
    persisted = store.latest_rp_story_memory()
    assert persisted is not None
    assert persisted["revision"] == 1
    assert persisted["memory"]["canon"][0]["status"] == "active"
    public_memory = c.get(f"/api/parties/{party['id']}/memory")
    assert public_memory.status_code == 200, public_memory.text
    public_canon = public_memory.json()["story_memory"]["memory"]["canon"]
    assert any(item["text"] == "The gate is open." and item["status"] == "active" for item in public_canon)
    assert not any(item["text"] == "The gate is closed." and item["status"] == "active" for item in public_canon)
    assert any(
        job["job_type"] == "rp_story_memory" and job["status"] == "pending"
        for job in store.service_jobs()
    )

    preview = c.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"content": "I inspect the doorway.", "source": "current"},
    )
    assert preview.status_code == 200, preview.text
    preview_prompt = "\n".join(message["content"] for message in preview.json()["preview"]["messages"])
    assert "The gate is open." in preview_prompt
    assert "The gate is closed." not in preview_prompt

    next_turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "I inspect the doorway.", "idempotency_key": "pending-memory-next-turn"},
    )
    assert next_turn.status_code == 200, next_turn.text
    with sqlite3.connect(store.sqlite_path) as connection:
        row = connection.execute(
            "SELECT prompt_json FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
            (store.campaign_id,),
        ).fetchone()
    assert row is not None
    assert "The gate is open." in row[0]
    assert "The gate is closed." not in row[0]


def test_reconstructed_last_prompt_does_not_apply_later_pending_correction(tmp_path: Path):
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 2}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    c = client(tmp_path)
    party = create_demo_party(c, title="Historical memory reconstruction")
    store = c.app.state.party_store.store_for_party(party["id"])
    seed_incorrect_story_fact(store)
    store.record_turn(
        "historical-pending-correction",
        "historical-pending-correction-request",
        "Apply the correction.",
        "Continuing.",
        {},
        store.current_version() or 1,
        metadata={
            "story_memory_corrections": [
                {
                    "field": "canon",
                    "fact_id": "fact:incorrect-gate",
                    "action": "replace",
                    "replacement_text": "The gate is open.",
                }
            ]
        },
    )

    preview = c.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"source": "last"},
    )

    assert preview.status_code == 200, preview.text
    payload = preview.json()["preview"]
    assert payload["source"] == "reconstructed_last_turn"
    prompt = "\n".join(message["content"] for message in payload["messages"])
    assert "The gate is closed." in prompt
    assert "The gate is open." not in prompt


@pytest.mark.parametrize("scenario_type", ["training"])
def test_typed_story_memory_correction_rejects_training_without_turn(
    tmp_path: Path,
    scenario_type: str,
):
    write_worldpack(tmp_path, supported_modes=[scenario_type])
    c = client(tmp_path)
    party = create_demo_party(c, title="Non-RP correction", scenario_type=scenario_type)
    store = c.app.state.party_store.store_for_party(party["id"])

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": "Invalid correction lane.",
            "story_memory_corrections": [
                {
                    "field": "canon",
                    "fact_id": "fact:not-present",
                    "action": "retract",
                }
            ],
        },
    )

    assert response.status_code == 400
    assert "only available for RP parties" in response.json()["detail"]
    assert store.turn_history(limit=10) == []


def test_typed_story_memory_correction_rejects_rp_revision_one_without_turn(tmp_path: Path):
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 1}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    c = client(tmp_path)
    party = create_demo_party(c, title="Revision one correction")
    store = c.app.state.party_store.store_for_party(party["id"])
    seed_incorrect_story_fact(store)

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": "Invalid correction revision.",
            "story_memory_corrections": [
                {
                    "field": "canon",
                    "fact_id": "fact:incorrect-gate",
                    "action": "retract",
                }
            ],
        },
    )

    assert response.status_code == 400
    assert "revision 2 or newer" in response.json()["detail"]
    assert store.turn_history(limit=10) == []


def test_typed_story_memory_correction_preflight_rejects_unknown_field_and_fact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 2}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    c = client(tmp_path)
    party = create_demo_party(c, title="Invalid typed memory correction")
    store = c.app.state.party_store.store_for_party(party["id"])
    seed_incorrect_story_fact(store)
    provider_calls = 0

    async def unexpected_provider_call(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider must not run for an invalid story-memory correction")

    monkeypatch.setattr("app.services.narrative.NarrativeClient.complete", unexpected_provider_call)
    state_version = store.current_version()

    unknown_field = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": "Invalid field correction.",
            "idempotency_key": "invalid-memory-field",
            "story_memory_corrections": [
                {
                    "field": "current_situation",
                    "fact_id": "fact:incorrect-gate",
                    "action": "retract",
                }
            ],
        },
    )
    assert unknown_field.status_code == 422

    unknown_fact = c.post(
        f"/api/parties/{party['id']}/messages",
        json={
            "content": "Invalid fact correction.",
            "idempotency_key": "invalid-memory-fact",
            "story_memory_corrections": [
                {
                    "field": "canon",
                    "fact_id": "fact:not-present",
                    "action": "retract",
                }
            ],
        },
        headers={"X-Request-ID": "req_invalid_memory_fact"},
    )
    assert unknown_fact.status_code == 400
    assert "active story-memory fact not found" in unknown_fact.json()["detail"]
    assert provider_calls == 0
    assert store.current_version() == state_version
    assert store.turn_history(limit=10) == []
    failed_trace = c.get(
        f"/api/parties/{party['id']}/turn-traces/req_invalid_memory_fact"
    )
    assert failed_trace.status_code == 200, failed_trace.text
    trace = failed_trace.json()["trace"]
    assert trace["status"] == "failed"
    assert trace["turn_id"] is None
    assert any(phase["phase_key"] == "request_failed" for phase in trace["phases"])


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
            llm_api_base="mock://success",
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


def test_supported_openrouter_narrator_settings_are_exposed_validated_and_saved(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
    party_store = c.app.state.party_store
    model_ids = {
        "deepseek": "deepseek/deepseek-v4-flash",
        "luna": "openai/gpt-5.6-luna",
        "luna_pro": "openai/gpt-5.6-luna-pro",
        "unsupported": "anthropic/claude-opus-5",
    }
    for key, model in model_ids.items():
        party_store.upsert_model_profile(
            {
                "id": f"test-{key}",
                "title": model,
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "model": model,
                "params": {"context_tokens": 384_000, "source": "test"},
                "api_key_source": "server_env_or_managed_key",
            }
        )

    profiles = {item["id"]: item for item in c.get("/api/model-profiles").json()["model_profiles"]}
    assert profiles["test-deepseek"]["params"]["narrator_controls"] == narrator_control_capabilities(
        "openrouter", model_ids["deepseek"]
    )
    assert profiles["test-luna"]["params"]["narrator_controls"]["reasoning_efforts"][-1] == "max"
    assert profiles["test-luna_pro"]["params"]["narrator_controls"]["temperature"] is False
    assert "narrator_controls" not in profiles["test-unsupported"]["params"]

    character = c.post(
        "/api/player-characters",
        json={"worldpack_id": "demo-world", "name": "Mira", "description": "Investigator", "profile": {}},
    ).json()["player_character"]
    party = c.post(
        "/api/parties",
        json={
            "title": "Narrator controls",
            "scenario_type": "rp",
            "worldpack_id": "demo-world",
            "player_character_id": character["id"],
            "model_profile_id": "test-deepseek",
        },
    ).json()["party"]
    settings = {
        "reasoning_effort": "xhigh",
        "temperature": 0.65,
        "top_p": 0.9,
        "max_tokens": 8192,
    }
    changed = c.patch(
        f"/api/parties/{party['id']}/model",
        json={"model_profile_id": "test-deepseek", "narrator_settings": settings},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["party"]["narrator_settings"] == settings

    preserved = c.patch(
        f"/api/parties/{party['id']}/model",
        json={"model_profile_id": "test-deepseek"},
    )
    assert preserved.json()["party"]["narrator_settings"] == settings

    assert c.patch(
        f"/api/parties/{party['id']}/model",
        json={"model_profile_id": "test-deepseek", "narrator_settings": {"temperature": 2.1}},
    ).status_code == 422
    incompatible = c.patch(
        f"/api/parties/{party['id']}/model",
        json={"model_profile_id": "test-luna", "narrator_settings": {"temperature": 0.7}},
    )
    assert incompatible.status_code == 400
    assert "temperature is not supported" in incompatible.json()["detail"]

    luna = c.patch(
        f"/api/parties/{party['id']}/model",
        json={
            "model_profile_id": "test-luna",
            "narrator_settings": {"reasoning_effort": "max", "max_tokens": 16384},
        },
    )
    assert luna.status_code == 200
    assert luna.json()["party"]["narrator_settings"] == {
        "reasoning_effort": "max",
        "max_tokens": 16384,
    }

    reset_on_model_change = c.patch(
        f"/api/parties/{party['id']}/model",
        json={"model_profile_id": "test-unsupported"},
    )
    assert reset_on_model_change.status_code == 200
    assert reset_on_model_change.json()["party"]["narrator_settings"] == {}


def test_party_narrator_settings_keep_explicit_request_limits() -> None:
    request = ChatCompletionRequest(
        model="deepseek/deepseek-v4-flash",
        messages=[ChatMessage(role="user", content="Continue.")],
        temperature=0.2,
        max_tokens=2048,
    )

    apply_party_narrator_settings(
        request,
        "openrouter",
        "deepseek/deepseek-v4-flash",
        {
            "reasoning_effort": "xhigh",
            "temperature": 0.8,
            "top_p": 0.85,
            "max_tokens": 8192,
        },
    )

    assert request.temperature == 0.2
    assert request.max_tokens == 2048
    assert request.top_p == 0.85
    assert request.reasoning == {"effort": "xhigh", "exclude": True}
    assert request._narrator_settings_model == "deepseek/deepseek-v4-flash"


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
    assert estimate["history_token_budget"] == 71_920
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


def test_revision8_party_imports_authored_lore_cards_without_runtime_model_call(tmp_path: Path):
    write_worldpack(
        tmp_path,
        rp_revision=8,
        lore_cards=[
            {
                "key": "npc:zhdan",
                "title": "Ждан",
                "content": "Ждан хочет получить долю в лавке, но не признает вину без доказательств.",
                "keywords": ["Ждан", "Ждана", "Ждану", "Жданом"],
                "always_on": False,
                "enabled": True,
            },
            {
                "key": "clue:token",
                "title": "Долговая метка",
                "content": "Метка относится к партии восточного товара.",
                "keywords": ["долговая метка", "долговую метку"],
                "always_on": False,
                "enabled": True,
            },
        ],
    )
    c = client(tmp_path, rp_contract_observed_revision=8)

    party = create_demo_party(c, title="Authored Lore Import")

    assert party["rp_contract_revision"] == 8
    cards = c.get(f"/api/parties/{party['id']}/lore-cards").json()["cards"]
    assert {card["title"] for card in cards} == {"Ждан", "Долговая метка"}
    assert all(card["source_turn_ids"] == [] for card in cards)
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        has_log = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'service_call_log'"
        ).fetchone()
        service_calls = (
            connection.execute("SELECT COUNT(*) FROM service_call_log").fetchone()[0]
            if has_log
            else 0
        )
    assert service_calls == 0


def test_revision10_party_initializes_clock_and_confirms_authored_marker(tmp_path: Path):
    write_worldpack(
        tmp_path,
        rp_revision=10,
        world_clock={
            "schema_version": "rp-gateway.world-clock.v1",
            "initial_date": "0964-04-18T09:00:00Z",
            "step_unit": "iso8601_duration",
            "max_step": "P2D",
            "markers": [
                {
                    "id": "demo.deadline-resolved",
                    "label": "Игрок явно решил вопрос срока",
                }
            ],
            "events": [
                {
                    "id": "demo.deadline-closes",
                    "condition": {
                        "type": "date_gte",
                        "date": "0964-04-20T09:00:00Z",
                    },
                    "summary": "Срок истёк.",
                    "superseded_by": ["demo.deadline-resolved"],
                    "consequences": [
                        {
                            "type": "world_fact",
                            "id": "demo.fact.deadline-closed",
                            "text": "Прежний срок истёк.",
                        }
                    ],
                }
            ],
        },
    )
    c = client(tmp_path, rp_contract_observed_revision=10)

    party = create_demo_party(c, title="World Clock")
    state = c.get(f"/api/parties/{party['id']}/state").json()["state"]
    assert party["rp_contract_revision"] == 10
    assert state["world_clock"]["date"] == "0964-04-18T09:00:00Z"
    assert state["world_clock"]["processed_party_turn"] == 0

    confirmed = c.post(
        f"/api/parties/{party['id']}/world-clock/markers/demo.deadline-resolved/confirm",
        json={"idempotency_key": "confirm-deadline"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["duplicate"] is False
    after = c.get(f"/api/parties/{party['id']}/state").json()["state"]
    assert after["world_clock"]["confirmed_marker_ids"] == ["demo.deadline-resolved"]
    assert after["world_clock"]["event_statuses"]["demo.deadline-closes"]["status"] == "superseded"

    duplicate = c.post(
        f"/api/parties/{party['id']}/world-clock/markers/demo.deadline-resolved/confirm",
        json={"idempotency_key": "confirm-deadline"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True


def test_revision10_candidate_branch_keeps_disabled_authored_event_cards(tmp_path: Path):
    clock = {
        "schema_version": "rp-gateway.world-clock.v1",
        "initial_date": "0964-04-18T09:00:00Z",
        "step_unit": "iso8601_duration",
        "max_step": "P2D",
        "markers": [
            {
                "id": "demo.event-diverted",
                "label": "Игрок явно изменил событие",
            }
        ],
        "events": [
            {
                "id": "demo.event-fires",
                "condition": {
                    "type": "date_gte",
                    "date": "0964-04-19T09:00:00Z",
                },
                "summary": "Событие произошло.",
                "superseded_by": ["demo.event-diverted"],
                "consequences": [
                    {
                        "type": "lore_card",
                        "key": "event:demo-risk",
                        "enabled": True,
                    }
                ],
            }
        ],
    }
    write_worldpack(
        tmp_path,
        rp_revision=10,
        lore_cards=[
            {
                "key": "event:demo-risk",
                "title": "Риск события",
                "content": "Карточка включается только после события.",
                "keywords": ["риск"],
                "always_on": False,
                "enabled": False,
            }
        ],
        world_clock=clock,
    )
    c = client(tmp_path, rp_contract_observed_revision=8)
    party = create_demo_party(c, title="World Clock Candidate")
    assert party["rp_contract_revision"] == 8

    checkpoint_response = c.post(
        f"/api/parties/{party['id']}/checkpoints",
        json={"label": "Clock candidate base"},
    )
    assert checkpoint_response.status_code == 200, checkpoint_response.text
    checkpoint = checkpoint_response.json()["checkpoint"]
    assert len(checkpoint["lore_card_ids"]) == 1
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        connection.execute(
            "UPDATE memory_checkpoints SET lore_card_ids_json = '[]' WHERE id = ?",
            (checkpoint["id"],),
        )
    branch_response = c.post(
        f"/api/parties/{party['id']}/branches",
        json={
            "checkpoint_id": checkpoint["id"],
            "label": "Revision 10 clock candidate",
            "rp_contract_revision": 10,
        },
    )
    assert branch_response.status_code == 200, branch_response.text
    branch = branch_response.json()["branch"]
    branch_store = c.app.state.party_store.store_for_branch(
        party["id"],
        branch["id"],
    )
    cards = branch_store.lore_cards(include_archived=True)
    assert [(card["authored_key"], card["enabled"]) for card in cards] == [
        ("event:demo-risk", False)
    ]
    assert branch_store.get_state()["world_clock"]["date"] == "0964-04-18T09:00:00Z"

    result = branch_store.apply_world_clock_tick(
        clock,
        party_turn=1,
        elapsed="P2D",
        reason="service_model",
        request_id="candidate-clock-turn-1",
    )
    assert [event["id"] for event in result["events"]] == ["demo.event-fires"]
    assert branch_store.lore_cards(include_archived=True)[0]["enabled"] is True


def test_revision8_lore_activation_uses_title_keywords_and_is_exposed_with_turn_metadata(tmp_path: Path):
    write_worldpack(
        tmp_path,
        rp_revision=8,
        lore_cards=[
            {
                "key": "npc:zhdan",
                "title": "Ждан",
                "content": "Скрытая деталь: у Ждана есть синий футляр.",
                "keywords": ["Ждан", "Ждана", "Ждану", "Жданом"],
                "always_on": False,
                "enabled": True,
            }
        ],
    )
    c = client(tmp_path, rp_contract_observed_revision=8)
    party = create_demo_party(c, title="Raised Lore")

    hidden_only = c.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"content": "Где синий футляр?", "source": "current"},
    ).json()["preview"]
    assert hidden_only["prompt_assembly"]["lore_card_ids"] == []
    assert "party_lore_cards" not in {block["id"] for block in hidden_only["blocks"]}

    turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Я спрашиваю Ждана о долге."},
        headers={"Authorization": "Bearer narrator-key"},
    )
    assert turn.status_code == 200, turn.text
    history_turn = c.get(f"/api/parties/{party['id']}/history").json()["turns"][-1]
    lore_card_ids = history_turn["metadata"]["prompt_assembly"]["lore_card_ids"]

    assert len(lore_card_ids) == 1
    assert history_turn["metadata"] == {
        "prompt_assembly": {"lore_card_ids": lore_card_ids}
    }
    assert [card["id"] for card in history_turn["activated_lore_cards"]] == lore_card_ids
    assert history_turn["activated_lore_cards"][0]["title"] == "Ждан"


def test_revision8_lore_card_draft_is_logged_and_persisted_only_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    write_worldpack(tmp_path, rp_revision=8)
    c = client(
        tmp_path,
        api_key="stack-service-key",
        rp_contract_observed_revision=8,
    )
    party = create_demo_party(c, title="Lore Draft")
    store = c.app.state.party_store.store_for_party(party["id"])
    source_turn_id = store.record_turn(
        "lore-source-1",
        "lore-source-1",
        "Я показываю Милаве найденную печать.",
        "Милава узнаёт знак перевозчика и прячет печать в счётную шкатулку.",
        {},
        store.current_version() or 1,
        metadata={"turn_kind": "narrative"},
        party_turn=1,
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            request=request,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Печать перевозчика",
                                    "content": "Милава узнала знак перевозчика и спрятала печать в счётную шкатулку.",
                                    "keywords": ["печать", "Милава", "Милавы", "Милаве", "Милавой"],
                                },
                                ensure_ascii=False,
                            )
                        },
                    }
                ],
            },
        )

    monkeypatch.setattr(
        "app.main.ServiceModelClient",
        lambda settings: ServiceModelClient(settings, transport=httpx.MockTransport(handler)),
    )

    draft_response = c.post(
        f"/api/parties/{party['id']}/lore-cards/draft",
        json={"source_turn_ids": [source_turn_id]},
    )

    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()["draft"]
    assert draft["source_turn_ids"] == [source_turn_id]
    assert c.get(f"/api/parties/{party['id']}/lore-cards").json()["cards"] == []
    assert captured["authorization"] == "Bearer stack-service-key"
    assert captured["payload"]["model"] == "deepseek/deepseek-v4-pro"
    assert captured["payload"]["max_tokens"] == 400
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        log_row = connection.execute(
            """
            SELECT role, provider, model, raw_response, prompt_text
            FROM service_call_log WHERE party_id = ? ORDER BY id DESC LIMIT 1
            """,
            (party["id"],),
        ).fetchone()
    assert log_row is not None
    assert log_row[:3] == ("lore_card_draft", "openrouter", "deepseek/deepseek-v4-pro")
    assert log_row[3]
    assert len(log_row[4]) <= 8_000

    confirmed = c.post(
        f"/api/parties/{party['id']}/lore-cards",
        json={
            **draft,
            "always_on": False,
            "enabled": True,
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["card"]["source_turn_ids"] == [source_turn_id]
    assert len(c.get(f"/api/parties/{party['id']}/lore-cards").json()["cards"]) == 1


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

    forked = c.post(
        f"/api/parties/{party['id']}/branches",
        json={"checkpoint_id": checkpoint["id"], "label": "Альтернативный поиск"},
    )
    assert forked.status_code == 200, forked.text
    branch = forked.json()["branch"]
    branch_payload = c.get(f"/api/parties/{party['id']}/branches/{branch['id']}").json()
    assert branch_payload["branch"]["party_id"] == party["id"]
    assert branch_payload["state"]["meta"]["branch_checkpoint_id"] == checkpoint["id"]
    assert len(branch_payload["turns"]) == 1

    next_turn = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Продолжаю только основную линию."},
        headers={"Authorization": "Bearer test"},
    )
    assert next_turn.status_code == 200
    assert len(c.get(f"/api/parties/{party['id']}/history").json()["turns"]) == 2
    assert len(c.get(f"/api/parties/{party['id']}/branches/{branch['id']}").json()["turns"]) == 1


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
    assert before["stats"]["history_token_budget"] == 64

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

    messages = NarrativeClient(Settings(llm_api_base="mock://success")).narrative_messages(
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

    messages = NarrativeClient(Settings(llm_api_base="mock://success")).narrative_messages(
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
    assert pack["manifest"]["prompt_source"] == "text"
    assert "gm_system" not in pack["manifest"]["files"]

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


def test_markdown_file_creates_large_prompt_world_without_duplicating_full_file_in_state(tmp_path: Path):
    c = client(tmp_path)
    markdown = "# Архипелаг\n\n" + ("- Остров хранит отдельную тайну.\n" * 600)
    assert len(markdown) > WORLD_PROMPT_MAX_CHARS

    response = c.post(
        "/api/worldpacks/prompt",
        json={
            "title": "Архипелаг памяти",
            "prompt": markdown,
            "source": "markdown_file",
            "source_filename": r"C:\fakepath\ARCHIPELAGO.MD",
        },
    )

    assert response.status_code == 200
    pack = response.json()["worldpack"]
    manifest = pack["manifest"]
    assert manifest["prompt_source"] == "markdown_file"
    assert manifest["prompt_source_filename"] == "ARCHIPELAGO.MD"
    assert manifest["prompt_source_characters"] == len(markdown.strip())
    assert manifest["prompt_truncated_in_state"] is True
    assert manifest["files"]["gm_system"] == "world.md"
    assert len(manifest["prompt"]) == WORLD_PROMPT_MAX_CHARS

    generated_root = Path(pack["manifest_path"]).parent
    assert (generated_root / "world.md").read_text(encoding="utf-8") == markdown.strip() + "\n"
    worldpack = c.app.state.party_store.get_worldpack(pack["id"])
    party_stub = SimpleNamespace(
        id="party-markdown",
        state_campaign_id="party-markdown",
        scenario_type="rp",
        worldpack_id=pack["id"],
        worldpack=worldpack,
        model_profile=None,
    )
    assert settings_for_party(c.app.state.settings, party_stub).world_system_prompt == markdown.strip()
    state = json.loads(Path(pack["state_seed_path"]).read_text(encoding="utf-8"))
    state_prompt = next(item["text"] for item in state["world_constraints"] if item["id"] == "world_prompt")
    assert len(state_prompt) == WORLD_PROMPT_MAX_CHARS
    assert markdown.strip() not in json.dumps(state, ensure_ascii=False)


@pytest.mark.parametrize(
    ("payload", "expected_message"),
    [
        (
            {"title": "Too long", "prompt": "x" * (WORLD_PROMPT_MAX_CHARS + 1)},
            "manual world prompt",
        ),
        (
            {
                "title": "Too large",
                "prompt": "x" * (WORLD_MARKDOWN_MAX_CHARS + 1),
                "source": "markdown_file",
                "source_filename": "world.md",
            },
            "String should have at most",
        ),
        (
            {
                "title": "Wrong extension",
                "prompt": "world text",
                "source": "markdown_file",
                "source_filename": "world.txt",
            },
            ".md source_filename",
        ),
    ],
)
def test_prompt_world_source_limits_are_enforced(tmp_path: Path, payload: dict[str, object], expected_message: str):
    response = client(tmp_path).post("/api/worldpacks/prompt", json=payload)

    assert response.status_code == 422
    assert expected_message in response.text


def test_light_gui_markdown_world_import_contract_is_present():
    rp_stack_root = Path(__file__).resolve().parents[2]
    light_gui_root = rp_stack_root / "rp-light-gui"
    if not light_gui_root.is_dir():
        pytest.skip("Light GUI sources are not shipped in the isolated Gateway image")

    html = (light_gui_root / "index.html").read_text(encoding="utf-8")
    javascript = (light_gui_root / "app.js").read_text(encoding="utf-8")
    nginx = (light_gui_root / "nginx.conf").read_text(encoding="utf-8")

    assert 'name="worldSource" value="markdown_file"' in html
    assert 'id="worldMarkdownInput" type="file" accept=".md,text/markdown,text/plain"' in html
    assert 'id="worldPromptInput" rows="5" maxlength="6000"' in html
    assert "const WORLD_MARKDOWN_MAX_CHARS = 200000;" in javascript
    assert 'source: "markdown_file", source_filename: imported.name' in javascript
    assert "Array.isArray(detail)" in javascript
    assert "client_max_body_size 4m;" in nginx


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


def test_rp_returns_nonempty_semantic_output_without_repair(tmp_path: Path):
    c = client(tmp_path, mode="violate")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload('/check persuasion target=king skill=0 difficulty=30 goal="transfer command"'),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "repair"},
    )
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"].lower()
    assert "equivalent military authority" in content


def test_rp_does_not_replace_semantic_output_with_safe_fallback(tmp_path: Path):
    c = client(tmp_path, mode="repair-fail")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload('/check persuasion target=king skill=0 difficulty=30 goal="transfer command"'),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "repair-fail"},
    )
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"].lower()
    assert "transfers command authority" in content


def test_rp_does_not_repair_meta_output_labels(tmp_path: Path):
    c = client(tmp_path, mode="meta-leak")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload("Тайм скип до ближайшего ивента"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "meta-leak"},
    )
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    assert "анализ:" in content.lower()


def test_openrouter_deepseek_flash_uses_supported_throughput_routing(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    class CapturingAsyncClient:
        def __init__(self, **kwargs: object):
            pass

        async def __aenter__(self) -> "CapturingAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            captured.update(kwargs["json"])  # type: ignore[arg-type]
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "Scene."}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", CapturingAsyncClient)
    settings = Settings(
        llm_provider="openrouter",
        llm_api_base="https://openrouter.ai/api/v1",
        llm_api_key="test-key",
        narrative_model="deepseek/deepseek-v4-flash",
        llm_fallback_models=(),
    )
    request = ChatCompletionRequest(
        model=settings.narrative_model,
        messages=[ChatMessage(role="user", content="Continue the scene.")],
    )
    outcome = Outcome(
        check_id="deepseek-flash-policy",
        action_type="feasibility",
        actor="player",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: success.",
    )

    asyncio.run(NarrativeClient(settings).complete(request, base_state(), outcome, None))

    assert "reasoning" not in captured
    assert captured["provider"] == {"sort": "throughput"}
    assert "max_tokens" not in captured


def test_narrative_retries_empty_success_once_on_the_same_deepseek_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempted_models: list[str] = []
    trace_events: list[dict[str, object]] = []

    class EmptyThenNarrativeAsyncClient:
        def __init__(self, **kwargs: object):
            pass

        async def __aenter__(self) -> "EmptyThenNarrativeAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            payload = kwargs["json"]
            assert isinstance(payload, dict)
            attempted_models.append(str(payload["model"]))
            provider_request = httpx.Request("POST", url)
            if len(attempted_models) == 1:
                return httpx.Response(
                    200,
                    request=provider_request,
                    json={
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": None,
                                    "reasoning": "Internal reasoning without a final answer.",
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                request=provider_request,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Сцена продолжается."},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", EmptyThenNarrativeAsyncClient)
    settings = Settings(
        llm_provider="openrouter",
        llm_api_base="https://openrouter.ai/api/v1",
        llm_api_key="test-key",
        narrative_model="deepseek/deepseek-v4-flash",
        llm_fallback_models=("openrouter/auto",),
    )
    request = ChatCompletionRequest(
        model=settings.narrative_model,
        messages=[ChatMessage(role="user", content="Continue the scene.")],
    )
    outcome = Outcome(
        check_id="deepseek-empty-final-retry",
        action_type="narrative",
        actor="player",
        result="narrative_continuation",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="neutral",
    )

    result = asyncio.run(
        NarrativeClient(settings, trace_recorder=trace_events.append).complete(
            request,
            base_state(),
            outcome,
            None,
            request_id="req-deepseek-empty-final-retry",
        )
    )

    assert response_text(result) == "Сцена продолжается."
    assert attempted_models == [settings.narrative_model, settings.narrative_model]
    assert [event["status"] for event in trace_events] == ["failed", "completed"]
    assert trace_events[0]["error"] == {
        "type": "RuntimeError",
        "message": "Narrative provider returned an empty final response",
    }


def test_narrator_settings_require_supported_primary_and_do_not_leak_to_fallback(
    monkeypatch: pytest.MonkeyPatch,
):
    payloads: list[dict[str, object]] = []

    class FallbackAsyncClient:
        def __init__(self, **kwargs: object):
            pass

        async def __aenter__(self) -> "FallbackAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            payload = dict(kwargs["json"])  # type: ignore[arg-type]
            payloads.append(payload)
            provider_request = httpx.Request("POST", url)
            if len(payloads) == 1:
                return httpx.Response(400, request=provider_request, json={"error": {"code": 400}})
            return httpx.Response(
                200,
                request=provider_request,
                json={"choices": [{"message": {"role": "assistant", "content": "Fallback scene."}}]},
            )

    monkeypatch.setattr(httpx, "AsyncClient", FallbackAsyncClient)
    settings = Settings(
        llm_provider="openrouter",
        llm_api_base="https://openrouter.ai/api/v1",
        llm_api_key="test-key",
        narrative_model="deepseek/deepseek-v4-flash",
        llm_fallback_models=("openai/gpt-5.6-luna",),
    )
    request = ChatCompletionRequest(
        model=settings.narrative_model,
        messages=[ChatMessage(role="user", content="Continue the scene.")],
    )
    apply_party_narrator_settings(
        request,
        "openrouter",
        settings.narrative_model,
        {"reasoning_effort": "xhigh", "temperature": 0.7, "top_p": 0.9, "max_tokens": 4096},
    )
    outcome = Outcome(
        check_id="narrator-settings-fallback",
        action_type="feasibility",
        actor="player",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: success.",
    )

    asyncio.run(NarrativeClient(settings).complete(request, base_state(), outcome, None))

    assert payloads[0]["reasoning"] == {"effort": "xhigh", "exclude": True}
    assert payloads[0]["temperature"] == 0.7
    assert payloads[0]["top_p"] == 0.9
    assert payloads[0]["max_tokens"] == 4096
    assert payloads[0]["provider"] == {"sort": "throughput", "require_parameters": True}
    assert payloads[1]["model"] == "openai/gpt-5.6-luna"
    assert not ({"reasoning", "temperature", "top_p", "max_tokens"} & payloads[1].keys())
    assert "provider" not in payloads[1]


@pytest.mark.parametrize("status_code", [403, 410])
def test_narrative_retries_provider_rejection_with_configured_fallback(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
):
    attempted_models: list[str] = []

    class FallbackAsyncClient:
        def __init__(self, **kwargs: object):
            pass

        async def __aenter__(self) -> "FallbackAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            payload = kwargs["json"]
            assert isinstance(payload, dict)
            attempted_models.append(str(payload["model"]))
            request = httpx.Request("POST", url)
            if len(attempted_models) == 1:
                return httpx.Response(status_code, request=request, json={"error": {"code": status_code}})
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "Fallback scene."}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", FallbackAsyncClient)
    settings = Settings(
        llm_api_base="https://provider.example/v1",
        llm_api_key="test-key",
        narrative_model="primary/model",
        llm_fallback_models=("fallback/model",),
    )
    request = ChatCompletionRequest(
        model=settings.narrative_model,
        messages=[ChatMessage(role="user", content="Continue the scene.")],
    )
    outcome = Outcome(
        check_id=f"fallback-{status_code}",
        action_type="feasibility",
        actor="player",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: success.",
    )

    response = asyncio.run(NarrativeClient(settings).complete(request, base_state(), outcome, None))

    assert attempted_models == ["primary/model", "fallback/model"]
    assert response["choices"][0]["message"]["content"] == "Fallback scene."


@pytest.mark.parametrize("fallback_succeeds", [True, False])
def test_narrative_retries_network_error_with_configured_fallback(
    monkeypatch: pytest.MonkeyPatch,
    fallback_succeeds: bool,
) -> None:
    attempted_models: list[str] = []
    trace_attempts: list[dict[str, object]] = []

    class NetworkFallbackAsyncClient:
        def __init__(self, **kwargs: object):
            pass

        async def __aenter__(self) -> "NetworkFallbackAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            payload = kwargs["json"]
            assert isinstance(payload, dict)
            attempted_models.append(str(payload["model"]))
            request = httpx.Request("POST", url)
            if len(attempted_models) == 1 or not fallback_succeeds:
                raise httpx.ConnectError("provider network unavailable", request=request)
            return httpx.Response(
                200,
                request=request,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "Fallback scene."}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", NetworkFallbackAsyncClient)
    settings = Settings(
        llm_api_base="https://provider.example/v1",
        llm_api_key="test-key",
        narrative_model="primary/model",
        llm_fallback_models=("fallback/model",),
    )
    request = ChatCompletionRequest(
        model=settings.narrative_model,
        messages=[ChatMessage(role="user", content="Continue the scene.")],
    )
    outcome = Outcome(
        check_id="network-fallback",
        action_type="feasibility",
        actor="player",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: success.",
    )
    narrative = NarrativeClient(settings, trace_attempts.append)

    if fallback_succeeds:
        response = asyncio.run(
            narrative.complete(
                request,
                base_state(),
                outcome,
                None,
                request_id="req-network-fallback",
            )
        )
        assert response["choices"][0]["message"]["content"] == "Fallback scene."
        assert [attempt["status"] for attempt in trace_attempts] == ["failed", "completed"]
    else:
        with pytest.raises(httpx.ConnectError):
            asyncio.run(
                narrative.complete(
                    request,
                    base_state(),
                    outcome,
                    None,
                    request_id="req-network-fallback",
                )
            )
        assert [attempt["status"] for attempt in trace_attempts] == ["failed", "failed"]

    assert attempted_models == ["primary/model", "fallback/model"]
    assert [attempt["model"] for attempt in trace_attempts] == attempted_models


def test_repair_prompt_is_compact_and_does_not_replay_party_history():
    settings = Settings(
        llm_provider="openrouter",
        narrative_model="deepseek/deepseek-v4-flash",
        scenario_type="training",
    )
    state = base_state()
    state["meta"]["turn"] = 4  # type: ignore[index]
    state["player"]["resources"]["current-turn-window"] = "turn 4, 10:00-12:00"  # type: ignore[index]
    outcome = Outcome(
        check_id="compact-repair",
        action_type="feasibility",
        actor="player",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: keep the scheduled scene.",
    )

    messages = NarrativeClient(settings).repair_messages(
        state,
        outcome,
        "Remove the leaked assessment label.",
        "Analysis: unsafe label. Correct scene text.",
        relationship_pressure="RELATIONSHIP_PRESSURE\n- hidden RP-only pressure",
    )
    encoded = json.dumps(messages, ensure_ascii=False)

    assert len(messages) == 2
    assert "Remove the leaked assessment label" in encoded
    assert "AUTHORITATIVE_OUTCOME: keep the scheduled scene" in encoded
    assert "Correct scene text" in encoded
    assert "The old tower burned" not in encoded
    assert "LONG_TERM_PARTY_MEMORY" not in encoded
    assert "RELATIONSHIP_PRESSURE" not in encoded


def test_rp_repair_prompt_keeps_relationship_pressure_before_the_failed_response():
    settings = Settings(llm_provider="openrouter", narrative_model="test-model", scenario_type="rp")
    outcome = Outcome(
        check_id="rp-repair-pressure",
        action_type="feasibility",
        actor="player",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: continue the scene.",
    )

    messages = NarrativeClient(settings).repair_messages(
        base_state(),
        outcome,
        "Correct the response.",
        "Failed response.",
        relationship_pressure="RELATIONSHIP_PRESSURE\n- Enri — отчуждение.",
    )

    assert [message["role"] for message in messages] == ["system", "system", "user"]
    assert messages[-2]["content"].startswith("RELATIONSHIP_PRESSURE")


def test_narrative_wall_clock_deadline_covers_the_complete_response(
    monkeypatch: pytest.MonkeyPatch,
):
    class SlowAsyncClient:
        def __init__(self, **kwargs: object):
            pass

        async def __aenter__(self) -> "SlowAsyncClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            await asyncio.sleep(0.05)
            request = httpx.Request("POST", url)
            return httpx.Response(200, request=request, json={"choices": []})

    monkeypatch.setattr(httpx, "AsyncClient", SlowAsyncClient)
    settings = Settings(
        llm_api_base="https://provider.example/v1",
        llm_api_key="test-key",
        narrative_model="test/model",
        llm_fallback_models=(),
        model_attempt_timeout_seconds=0.01,
    )
    request = ChatCompletionRequest(
        model=settings.narrative_model,
        messages=[ChatMessage(role="user", content="Continue the scene.")],
    )
    outcome = Outcome(
        check_id="wall-clock-deadline",
        action_type="feasibility",
        actor="player",
        result="success",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        consequences=[],
        authoritative_block="AUTHORITATIVE_OUTCOME: success.",
    )

    with pytest.raises(httpx.TimeoutException, match="wall-clock deadline"):
        asyncio.run(NarrativeClient(settings).complete(request, base_state(), outcome, None))


def test_timeout_and_rate_limit(tmp_path: Path):
    c_timeout = client(tmp_path / "timeout", mode="timeout")
    response = c_timeout.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "timeout"},
    )
    assert response.status_code == 502
    assert response.json()["detail"] == "Narrative provider timed out"

    c_rate = client(tmp_path / "rate", mode="rate-limit")
    response = c_rate.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "rate"},
    )
    assert response.status_code == 502


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


def test_rp_provider_http_error_returns_explicit_error(tmp_path: Path):
    c = client(tmp_path, mode="http-503")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "provider-http-503"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Narrative provider HTTP 503"


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
        "provider": "gemini",
        "model": "gemini-3.6-flash",
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


@pytest.mark.parametrize("operation", ["start", "message"])
def test_rp_rejects_empty_provider_response_after_one_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
):
    write_worldpack(tmp_path)
    llm_calls = 0

    async def empty_complete(*args: object, **kwargs: object) -> dict:
        nonlocal llm_calls
        llm_calls += 1
        return {"id": "empty", "choices": []}

    monkeypatch.setattr(NarrativeClient, "complete", empty_complete)
    c = client(tmp_path)
    party = create_demo_party(c, title=f"Empty RP {operation}", scenario_type="rp")
    request_id = f"req_empty_rp_{operation}"
    if operation == "start":
        response = c.post(
            f"/api/parties/{party['id']}/start",
            json={"idempotency_key": "empty-rp-start"},
            headers={"Authorization": "Bearer test", "X-Request-ID": request_id},
        )
    else:
        response = c.post(
            f"/api/parties/{party['id']}/messages",
            json={"content": "Continue", "idempotency_key": "empty-rp-message"},
            headers={"Authorization": "Bearer test", "X-Request-ID": request_id},
        )

    assert response.status_code == 502
    assert response.json()["detail"] == "Narrative provider returned an invalid response"
    assert llm_calls == 1
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == []
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        audit_rows = connection.execute(
            "SELECT event_json FROM audit_events "
            "WHERE campaign_id = ? AND request_id = ? AND event_type = 'llm_invalid_response'",
            (party["id"], request_id),
        ).fetchall()
        turn_count = connection.execute(
            "SELECT count(*) FROM turns WHERE campaign_id = ?",
            (party["id"],),
        ).fetchone()[0]
    assert len(audit_rows) == 1
    audit = json.loads(audit_rows[0][0])
    assert set(audit) == {"request_id", "model", "reason"}
    assert audit["request_id"] == request_id
    assert audit["reason"] == "empty_response"
    assert isinstance(audit["model"], str) and audit["model"]
    assert turn_count == 0


def test_rp_party_message_skips_semantic_validation_and_uses_one_completion(tmp_path: Path):
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

    assert response.status_code == 200, response.text
    assert "transfers command authority" in response.json()["message"]["content"].lower()
    history = c.get(f"/api/parties/{party['id']}/history").json()
    assert len(history["turns"]) == 1
    metadata = latest_turn_metadata(c.app.state.party_store.store_for_party(party["id"]))
    assert metadata["llm_calls"] == 1
    assert metadata["validator_valid"] is None
    assert metadata["repaired"] is False
    assert metadata["fallback"] is False
    assert metadata["transport_status"] == "ok"
    status = c.get(f"/api/parties/{party['id']}/requests/req_party_repair_fail").json()
    assert status["status"] == "completed"


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


def test_party_start_and_messages_use_separate_attempt_deadlines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    original_complete = NarrativeClient.complete
    observed_timeouts: list[float] = []

    async def observed_complete(self: NarrativeClient, *args: object, **kwargs: object) -> dict:
        observed_timeouts.append(self.settings.model_attempt_timeout_seconds)
        return await original_complete(self, *args, **kwargs)

    monkeypatch.setattr(NarrativeClient, "complete", observed_complete)
    write_worldpack(tmp_path)
    c = client(
        tmp_path,
        model_attempt_timeout_seconds=150,
        party_start_model_attempt_timeout_seconds=300,
    )
    party = create_demo_party(c)

    started = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "separate-timeout-start"},
        headers={"Authorization": "Bearer test"},
    )
    message = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "Continue.", "idempotency_key": "separate-timeout-message"},
        headers={"Authorization": "Bearer test"},
    )

    assert started.status_code == 200, started.text
    assert message.status_code == 200, message.text
    assert observed_timeouts == [300, 150]


def test_party_start_timeout_returns_504_and_marks_request_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    observed_timeouts: list[float] = []

    async def timeout_complete(self: NarrativeClient, *args: object, **kwargs: object) -> dict:
        observed_timeouts.append(self.settings.model_attempt_timeout_seconds)
        raise httpx.ReadTimeout(
            "Narrative provider exceeded the wall-clock deadline",
            request=httpx.Request("POST", "https://provider.example/v1/chat/completions"),
        )

    monkeypatch.setattr(NarrativeClient, "complete", timeout_complete)
    write_worldpack(tmp_path)
    c = client(
        tmp_path,
        model_attempt_timeout_seconds=150,
        party_start_model_attempt_timeout_seconds=300,
    )
    party = create_demo_party(c)

    response = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "start-provider-timeout"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_start_provider_timeout"},
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "Narrative provider exceeded the party-start deadline"
    assert observed_timeouts == [300]
    assert c.get(f"/api/parties/{party['id']}/history").json()["turns"] == []
    status = c.get(f"/api/parties/{party['id']}/requests/req_start_provider_timeout").json()
    assert status["status"] == "failed"
    assert "ReadTimeout" in status["error"]


@pytest.mark.parametrize(
    ("provider_mode", "expected_transport"),
    [("http-503", "provider_error"), ("timeout", "provider_timeout")],
)
def test_training_runtime_party_start_provider_error_uses_world_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_mode: str,
    expected_transport: str,
):
    original_complete = NarrativeClient.complete
    llm_calls = 0

    async def counted_complete(*args: object, **kwargs: object) -> dict:
        nonlocal llm_calls
        llm_calls += 1
        return await original_complete(*args, **kwargs)

    monkeypatch.setattr(NarrativeClient, "complete", counted_complete)
    source = Path(__file__).resolve().parents[2] / "worldpacks" / "awareness-one-day"
    shutil.copytree(source, tmp_path / "worldpacks" / "awareness-one-day")
    c = client(tmp_path, mode=provider_mode)
    party = create_demo_party(
        c,
        title="Runtime provider fallback",
        character_name="Эллина",
        scenario_type="training",
        worldpack_id="awareness-one-day",
    )

    response = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "runtime-start-provider-http-503"},
        headers={"Authorization": "Bearer test", "X-Request-ID": "req_runtime_start_http_503"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["finish_reason"] == "provider_fallback"
    assert response.json()["choices"][0]["message"]["content"].startswith(
        "Ход 1. Понедельник, 09:00-09:30."
    )
    history = c.get(f"/api/parties/{party['id']}/history").json()["turns"]
    assert len(history) == 1
    assert llm_calls == 1
    metadata = latest_turn_metadata(c.app.state.party_store.store_for_party(party["id"]))
    assert metadata["transport_status"] == expected_transport
    deleted = c.delete(f"/api/parties/{party['id']}")
    assert deleted.status_code == 200, deleted.text


@pytest.mark.parametrize(
    ("failure_kind", "expected_calls", "expected_finish_reason"),
    [("soft-deadline", 2, "provider_fallback"), ("hard-sender", 1, "provider_fallback")],
)
def test_training_runtime_repairs_only_soft_validation_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    expected_calls: int,
    expected_finish_reason: str,
):
    llm_calls = 0

    async def training_complete(*args: object, **kwargs: object) -> dict:
        nonlocal llm_calls
        llm_calls += 1
        sender = "Посторонний" if failure_kind == "hard-sender" else "Анна Петрова <petrova@ptsecurity.com>"
        deadline = "утром" if failure_kind == "soft-deadline" and llm_calls == 1 else "09:35"
        content = (
            "ПИСЬМО\n"
            "Канал: корпоративная почта\n"
            f"От: {sender}\n"
            "Кому: Эллина\n"
            "Дата/время: понедельник, 09:12\n"
            "Тема: Первый результат\n"
            "Вложения: нет\n"
            "Ссылки: нет\n"
            "Тело:\n"
            f"К {deadline} пришли первый результат или конкретный вопрос по работе Investigator.\n"
            "Подпись:\nАнна Петрова"
        )
        return {
            "id": f"training-repair-{llm_calls}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(NarrativeClient, "complete", training_complete)
    source = Path(__file__).resolve().parents[2] / "worldpacks" / "awareness-one-day"
    shutil.copytree(source, tmp_path / "worldpacks" / "awareness-one-day")
    c = client(tmp_path)
    party = create_demo_party(
        c,
        title="Runtime repair classification",
        character_name="Эллина",
        scenario_type="training",
        worldpack_id="awareness-one-day",
    )

    response = c.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": f"runtime-repair-{failure_kind}"},
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 200, response.text
    assert llm_calls == expected_calls
    assert response.json()["choices"][0]["finish_reason"] == expected_finish_reason
    metadata = latest_turn_metadata(c.app.state.party_store.store_for_party(party["id"]))
    assert metadata["transport_status"] == "invalid_response"
    if failure_kind == "soft-profile":
        assert "Investigator" in response.json()["choices"][0]["message"]["content"]


def test_configured_attempt_order_keeps_explicit_models():
    from app.core.config import Settings
    from app.services.narrative import NarrativeClient

    settings = Settings(
        llm_api_base="mock://success",
        narrative_model="openrouter/auto",
        llm_fallback_models=("openrouter/free",),
    )
    assert NarrativeClient(settings).model_attempts(settings.narrative_model) == [
        "openrouter/auto",
        "openrouter/free",
    ]


def test_disabled_primary_model_uses_fallback(tmp_path: Path):
    from app.core.config import Settings
    from app.services.narrative import NarrativeClient
    from app.services.state_store import StateStore
    from app.services.world_instructor import WorldInstructor

    settings = Settings(
        llm_fallback_models=("fallback/model", "other/model"),
        llm_disabled_models=("primary/model",),
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
