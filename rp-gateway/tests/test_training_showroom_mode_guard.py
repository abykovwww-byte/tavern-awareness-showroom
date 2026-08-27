from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.schemas import ShowroomScenarioCreate
from test_training_gateway_mode_guard import table_counts, training_settings


def scenario_payload(model_profile_id: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "Awareness training",
        "description": "Training-only scenario",
        "status": "published",
        "scenario_type": "training",
        "model_profile_id": model_profile_id,
        "world_source": "preset",
        "worldpack_id": "awareness",
        "leaderboard_enabled": True,
        "leaderboard_metric": "state_path",
        "leaderboard_state_path": "player.resources.awareness-score",
        "leaderboard_label": "Score",
        "interactive_links_enabled": False,
        "interactive_workspace_enabled": False,
        "sort_order": 100,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize(
    ("scenario_type", "world_source"),
    [("rp", "preset"), ("training", "prompt")],
)
def test_admin_schema_rejects_rp_and_prompt_before_showroom_store_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario_type: str,
    world_source: str,
) -> None:
    client = TestClient(create_app(training_settings(tmp_path)))
    model_profile_id = client.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    showroom_store = client.app.state.showroom_store
    monkeypatch.setattr(
        showroom_store,
        "create_scenario",
        lambda *args, **kwargs: pytest.fail("ShowroomStore must not receive an invalid API payload"),
    )

    response = client.post(
        "/api/admin/showroom/scenarios",
        json=scenario_payload(
            model_profile_id,
            scenario_type=scenario_type,
            world_source=world_source,
            worldpack_id=None if world_source == "prompt" else "awareness",
            world_prompt="forbidden" if world_source == "prompt" else None,
        ),
    )

    assert response.status_code == 422
    assert table_counts(Path(client.app.state.settings.sqlite_path), "showroom_scenarios") == {
        "showroom_scenarios": 0
    }
    assert not (Path(client.app.state.settings.party_state_root) / "_generated_worldpacks").exists()


@pytest.mark.parametrize(
    ("scenario_type", "world_source", "error"),
    [
        ("rp", "preset", "scenario_type=training"),
        ("training", "prompt", "world_source=preset"),
    ],
)
def test_direct_showroom_create_guard_precedes_model_and_world_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario_type: str,
    world_source: str,
    error: str,
) -> None:
    app = create_app(training_settings(tmp_path))
    showroom_store = app.state.showroom_store
    request = ShowroomScenarioCreate.model_construct(
        scenario_type=scenario_type,
        world_source=world_source,
    )
    monkeypatch.setattr(
        showroom_store.party_store,
        "require_active_model_profile",
        lambda *args, **kwargs: pytest.fail("model lookup must not run"),
    )
    monkeypatch.setattr(
        showroom_store,
        "resolve_world",
        lambda *args, **kwargs: pytest.fail("world resolution must not run"),
    )

    with pytest.raises(ValueError, match=error):
        showroom_store.create_scenario(request, created_by=None)


@pytest.mark.parametrize(
    "changes",
    [{"scenario_type": "rp"}, {"world_source": "prompt"}],
)
def test_direct_showroom_update_guard_precedes_scenario_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, object],
) -> None:
    app = create_app(training_settings(tmp_path))
    showroom_store = app.state.showroom_store
    monkeypatch.setattr(
        showroom_store,
        "get_scenario",
        lambda *args, **kwargs: pytest.fail("scenario lookup must not run"),
    )

    with pytest.raises(ValueError, match="training ShowRoom accepts only"):
        showroom_store.update_scenario("legacy", changes)


def test_rp_showroom_run_is_hidden_before_visitor_cookie_or_aggregate_creation(tmp_path: Path) -> None:
    client = TestClient(create_app(training_settings(tmp_path)))
    assert client.get("/api/worldpacks").status_code == 200
    model_profile_id = client.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    database_path = Path(client.app.state.settings.sqlite_path)
    timestamp = "2026-08-27T00:00:00Z"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO showroom_scenarios(
                id, slug, title, description, status, scenario_type, model_profile_id,
                world_source, worldpack_id, world_prompt, leaderboard_enabled,
                leaderboard_metric, leaderboard_state_path, leaderboard_label,
                interactive_links_enabled, interactive_workspace_enabled,
                sort_order, revision, created_by, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "scenario_legacy_rp",
                "legacy-rp",
                "Legacy RP",
                "Foreign aggregate",
                "published",
                "rp",
                model_profile_id,
                "preset",
                "awareness",
                None,
                1,
                "state_path",
                "meta.turn",
                "Score",
                0,
                0,
                100,
                1,
                None,
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            UPDATE showroom_scenarios
            SET cover_filename = ?, cover_mime_type = ?
            WHERE id = ?
            """,
            ("scenario_legacy_rp.png", "image/png", "scenario_legacy_rp"),
        )

    cover_path = Path(client.app.state.settings.showroom_cover_dir) / "scenario_legacy_rp.png"
    cover_path.write_bytes(b"\x89PNG\r\n\x1a\nlegacy")

    guarded_tables = (
        "showroom_visitors",
        "showroom_runs",
        "player_characters",
        "parties",
        "campaigns",
        "state_versions",
    )
    before = table_counts(database_path, *guarded_tables)
    state_root = Path(client.app.state.settings.party_state_root)
    state_files_before = sorted(path.relative_to(state_root) for path in state_root.rglob("*") if path.is_file())

    response = client.post(
        "/api/showroom/scenarios/scenario_legacy_rp/runs",
        json={
            "character_name": "Visitor",
            "character_prompt": "Security analyst",
            "employee_position": "Analyst",
            "leaderboard_opt_in": True,
            "client_request_id": "forbidden-rp-run",
        },
    )

    assert response.status_code == 404
    assert client.get("/api/showroom/scenarios/scenario_legacy_rp/cover").status_code == 404
    assert "set-cookie" not in response.headers
    assert table_counts(database_path, *guarded_tables) == before
    assert sorted(path.relative_to(state_root) for path in state_root.rglob("*") if path.is_file()) == state_files_before
    assert client.cookies.get(client.app.state.settings.showroom_visitor_cookie_name) is None
