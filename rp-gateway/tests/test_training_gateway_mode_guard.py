from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app, settings_for_party
from app.models.schemas import PartyCreate
from app.services.adjudicator import Adjudicator


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def training_settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "campaign_id": "default",
        "scenario_type": "training",
        "database_url": f"sqlite:///{tmp_path / 'awareness_gateway.db'}",
        "world_state_path": str(tmp_path / "state" / "current.json"),
        "party_state_root": str(tmp_path / "state" / "parties"),
        "state_schema_path": str(PROJECT_ROOT / "state" / "schema.json"),
        "showroom_cover_dir": str(tmp_path / "showroom-covers"),
        "worldpacks_path": str(PROJECT_ROOT / "worldpacks"),
        "llm_api_base": "mock://success",
        "llm_api_key": "test-key",
        "gemini_api_base": "mock://success",
        "gemini_api_key": "test-key",
        "openrouter_api_base": "mock://success",
        "service_openrouter_api_key": "test-key",
        "openrouter_model_catalog_live": False,
        "gemini_model_catalog_live": False,
        "local_llm_enabled": False,
        "post_turn_helpers_inline": True,
        "auth_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def table_counts(database_path: Path, *tables: str) -> dict[str, int]:
    with sqlite3.connect(database_path) as connection:
        return {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def test_standalone_defaults_are_training_and_storage_isolated() -> None:
    defaults = Settings.__dataclass_fields__

    assert defaults["scenario_type"].default == "training"
    assert defaults["campaign_id"].default == "awareness-showroom"
    assert defaults["database_url"].default == "sqlite:////data/awareness_gateway.db"
    assert defaults["state_schema_path"].default == "/app/state-schema.json"
    assert defaults["auth_session_cookie_name"].default == "awareness_gateway_session"
    assert defaults["showroom_visitor_cookie_name"].default == "awareness_showroom_visitor"


def test_openapi_identity_is_awareness_gateway(tmp_path: Path) -> None:
    assert create_app(training_settings(tmp_path)).title == "Awareness Gateway"


def test_non_training_process_fails_before_storage_creation(tmp_path: Path) -> None:
    settings = training_settings(tmp_path, scenario_type="rp")

    with pytest.raises(RuntimeError, match="SCENARIO_TYPE=training"):
        create_app(settings)

    assert not Path(settings.sqlite_path).exists()
    assert not Path(settings.world_state_path).exists()
    assert not Path(settings.party_state_root).exists()
    assert not Path(settings.showroom_cover_dir).exists()


def test_foreign_database_fails_read_only_before_store_migrations(tmp_path: Path) -> None:
    database_path = tmp_path / "foreign_rp_gateway.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE parties(id TEXT PRIMARY KEY, scenario_type TEXT NOT NULL);
            CREATE TABLE showroom_scenarios(
                id TEXT PRIMARY KEY, scenario_type TEXT NOT NULL, world_source TEXT NOT NULL
            );
            CREATE TABLE worldpacks(id TEXT PRIMARY KEY, manifest_json TEXT NOT NULL);
            CREATE TABLE model_profiles(id TEXT PRIMARY KEY, provider TEXT NOT NULL);
            INSERT INTO parties VALUES('party_legacy', 'rp');
            INSERT INTO showroom_scenarios VALUES('scenario_legacy', 'rp', 'prompt');
            INSERT INTO worldpacks VALUES(
                'incident-50',
                '{"scenario_types":{"recommended":"training","supported":["training","rp"]}}'
            );
            INSERT INTO model_profiles VALUES('legacy_nvidia', 'nvidia');
            """
        )
    before = database_path.read_bytes()
    settings = training_settings(tmp_path, database_url=f"sqlite:///{database_path}")

    with pytest.raises(RuntimeError, match="not owned by the training-only Awareness project"):
        create_app(settings)

    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT scenario_type FROM parties").fetchone() == ("rp",)
        assert connection.execute("SELECT scenario_type, world_source FROM showroom_scenarios").fetchone() == (
            "rp",
            "prompt",
        )
        assert connection.execute("SELECT provider FROM model_profiles").fetchone() == ("nvidia",)
    assert not Path(settings.world_state_path).exists()
    assert not Path(settings.party_state_root).exists()
    assert not Path(settings.showroom_cover_dir).exists()


def test_unknown_existing_database_is_not_extended_by_startup(tmp_path: Path) -> None:
    database_path = tmp_path / "unrelated.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE private_notes(id INTEGER PRIMARY KEY, note TEXT NOT NULL)")
        connection.execute("INSERT INTO private_notes(note) VALUES('must stay untouched')")
    before = database_path.read_bytes()
    settings = training_settings(tmp_path, database_url=f"sqlite:///{database_path}")

    with pytest.raises(RuntimeError, match="unrecognized ownership schema"):
        create_app(settings)

    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT note FROM private_notes").fetchone() == ("must stay untouched",)
        assert {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        } == {"private_notes"}
    assert not Path(settings.world_state_path).exists()
    assert not Path(settings.party_state_root).exists()
    assert not Path(settings.showroom_cover_dir).exists()


def test_catalog_exposes_only_runtime_backed_training_worldpacks(tmp_path: Path) -> None:
    client = TestClient(create_app(training_settings(tmp_path)))

    response = client.get("/api/worldpacks")

    assert response.status_code == 200
    assert {pack["id"] for pack in response.json()["worldpacks"]} == {
        "awareness",
        "awareness-one-day",
    }
    assert client.get("/api/worldpacks/incident-50").status_code == 404
    with sqlite3.connect(client.app.state.settings.sqlite_path) as connection:
        registered = {row[0] for row in connection.execute("SELECT id FROM worldpacks")}
    assert registered == {"awareness", "awareness-one-day"}


def test_direct_party_store_rejects_rp_before_worldpack_or_state_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(training_settings(tmp_path))
    party_store = app.state.party_store
    request = PartyCreate.model_construct(
        title="Forbidden RP",
        scenario_type="rp",
        worldpack_id="incident-50",
        player_character_id="pc_missing",
        model_profile_id="profile_missing",
    )
    monkeypatch.setattr(
        party_store,
        "get_worldpack",
        lambda *args, **kwargs: pytest.fail("worldpack lookup must not run for RP"),
    )
    before = table_counts(Path(app.state.settings.sqlite_path), "parties", "campaigns", "state_versions")

    with pytest.raises(ValueError, match="scenario_type=training"):
        party_store.create_party(request)

    assert table_counts(Path(app.state.settings.sqlite_path), *before) == before
    assert list(Path(app.state.settings.party_state_root).iterdir()) == []


def test_party_runtime_rejects_persisted_rp_mode() -> None:
    settings = Settings(scenario_type="training")
    party = SimpleNamespace(scenario_type="rp")

    with pytest.raises(ValueError, match="scenario_type=training"):
        settings_for_party(settings, party)


def test_persisted_training_party_with_ineligible_worldpack_is_hidden_before_state_store_creation(
    tmp_path: Path,
) -> None:
    app = create_app(training_settings(tmp_path))
    party_store = app.state.party_store
    manifest = {
        "id": "incident-50",
        "scenario_types": {"recommended": "training", "supported": ["training", "rp"]},
    }
    model_profile_id = party_store.list_model_profiles()[0].id
    timestamp = "2026-08-27T00:00:00Z"
    with party_store.connect() as connection:
        connection.execute(
            """
            INSERT INTO worldpacks(
                id, title, slug, status, premise, manifest_path, state_seed_path,
                manifest_json, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "incident-50",
                "Incident 50",
                "incident-50",
                "playable",
                "Legacy hybrid pack",
                str(tmp_path / "removed-incident-50" / "manifest.json"),
                str(tmp_path / "removed-incident-50" / "state-seed.json"),
                json.dumps(manifest, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO player_characters(
                id, worldpack_id, name, description, status, profile_json, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pc_incident_legacy",
                "incident-50",
                "Legacy learner",
                "Persisted before the split",
                "active",
                "{}",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO parties(
                id, title, scenario_type, worldpack_id, player_character_id,
                model_profile_id, state_campaign_id, status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "party_incident_legacy",
                "Legacy incident training",
                "training",
                "incident-50",
                "pc_incident_legacy",
                model_profile_id,
                "party_incident_legacy",
                "active",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO party_branches(
                id, party_id, label, source_checkpoint_id, state_campaign_id,
                status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "branch_incident_legacy",
                "party_incident_legacy",
                "Foreign branch",
                1,
                "branch_incident_legacy",
                "active",
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO autotest_runs(
                id, source_party_id, test_party_id, branch_id, player_model_profile_id,
                player_prompt, requested_turns, status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "autotest_incident_legacy",
                "party_incident_legacy",
                "branch:branch_incident_legacy",
                "branch_incident_legacy",
                model_profile_id,
                "Foreign player",
                2,
                "running",
                timestamp,
                timestamp,
            ),
        )

    state_path = party_store.state_path_for("party_incident_legacy")
    assert not state_path.exists()
    with pytest.raises(ValueError, match="party not found"):
        party_store.get_party("party_incident_legacy")
    with pytest.raises(ValueError, match="party not found"):
        party_store.store_for_party("party_incident_legacy")
    assert not state_path.exists()
    assert party_store.list_parties() == []
    assert party_store.list_all_party_branches() == []
    assert party_store.resumable_autotest_runs() == []


def test_prompt_world_and_legacy_chat_routes_are_absent_without_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TestClient(create_app(training_settings(tmp_path)))
    database_path = Path(client.app.state.settings.sqlite_path)
    tables = ("worldpacks", "turn_requests", "turns", "audit_events")
    before = table_counts(database_path, *tables)
    provider_called = False

    async def unexpected_provider_call(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal provider_called
        provider_called = True
        return {}

    monkeypatch.setattr(Adjudicator, "handle_chat", unexpected_provider_call)

    prompt_response = client.post(
        "/api/worldpacks/prompt",
        json={"title": "Forbidden generated world", "prompt": "Create a world"},
    )
    chat_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert prompt_response.status_code == 404
    assert chat_response.status_code == 404
    assert table_counts(database_path, *tables) == before
    assert not (Path(client.app.state.settings.party_state_root) / "_generated_worldpacks").exists()
    assert provider_called is False


def test_light_gui_api_surface_is_not_registered_or_mutating(tmp_path: Path) -> None:
    client = TestClient(create_app(training_settings(tmp_path)))
    database_path = Path(client.app.state.settings.sqlite_path)
    guarded_tables = ("campaigns", "state_versions", "turns", "audit_events", "parties")
    before = table_counts(database_path, *guarded_tables)
    forbidden_prefixes = (
        "/api/parties",
        "/api/player-characters",
        "/api/state",
        "/api/world",
        "/api/turn",
        "/api/turn-traces",
    )

    assert not any(
        path == prefix or path.startswith(f"{prefix}/")
        for path in client.app.openapi()["paths"]
        for prefix in forbidden_prefixes
    )
    attempts = (
        ("/api/parties/party_training/world/instruct", {"instruction": "bypass", "use_llm": False}),
        ("/api/parties/party_training/lore-cards", {"title": "bypass"}),
        ("/api/player-characters/draft", {"prompt": "bypass"}),
        ("/api/state/patch/apply", {"patch": []}),
        ("/api/world/instruct", {"instruction": "bypass"}),
        ("/api/turn/rollback", {}),
    )
    for path, payload in attempts:
        assert client.post(path, json=payload).status_code == 404

    assert table_counts(database_path, *guarded_tables) == before


@pytest.mark.parametrize("scenario_type", ["rp", "novel"])
def test_dataset_export_rejects_non_training_filters_before_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario_type: str,
) -> None:
    client = TestClient(create_app(training_settings(tmp_path)))
    monkeypatch.setattr(
        client.app.state.party_store,
        "export_dataset_records",
        lambda *args, **kwargs: pytest.fail("non-training export must not query dataset rows"),
    )

    response = client.get(f"/api/admin/datasets/export.jsonl?scenario_type={scenario_type}")

    assert response.status_code == 400
    assert response.json()["detail"] == "scenario_type must be training"
