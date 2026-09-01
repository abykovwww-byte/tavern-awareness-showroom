from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.schemas import ShowroomScenarioCreate
from test_training_gateway_mode_guard import table_counts, training_settings


def write_catalog(tmp_path: Path, scenarios: list[dict[str, object]]) -> Path:
    path = tmp_path / "catalog" / "scenarios.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "awareness-showroom.catalog.v1",
                "scenarios": scenarios,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def catalog_scenario(**overrides: object) -> dict[str, object]:
    scenario: dict[str, object] = {
        "key": "awareness-course",
        "title": "Awareness course",
        "description": "Published from Git",
        "status": "published",
        "worldpack_id": "awareness-one-day",
        "model": {
            "provider": "openrouter",
            "base_url": "mock://success",
            "model": "deepseek/deepseek-v4-flash",
        },
        "leaderboard_enabled": True,
        "leaderboard_label": "Score",
        "interactive_links_enabled": True,
        "interactive_workspace_enabled": True,
        "sort_order": 10,
        "cover": None,
    }
    scenario.update(overrides)
    return scenario


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


def test_git_catalog_upserts_by_stable_key_and_preserves_removed_rows(tmp_path: Path) -> None:
    cover = b"\x89PNG\r\n\x1a\nrepo-owned-cover"
    catalog_path = write_catalog(
        tmp_path,
        [catalog_scenario(cover="covers/awareness-course.png")],
    )
    cover_source = catalog_path.parent / "covers" / "awareness-course.png"
    cover_source.parent.mkdir()
    cover_source.write_bytes(cover)
    settings = training_settings(
        tmp_path,
        showroom_catalog_path=str(catalog_path),
        openrouter_models=("deepseek/deepseek-v4-flash",),
    )

    first = create_app(settings)
    scenario = first.state.showroom_store.list_scenarios(public_only=False)[0]
    assert scenario["id"] == "scenario_catalog_awareness-course"
    assert scenario["revision"] == 1
    assert scenario["leaderboard_state_path"] == "player.resources.total-score"
    assert scenario["interactive_links_enabled"] is True
    assert scenario["interactive_workspace_enabled"] is True
    assert (Path(settings.showroom_cover_dir) / "scenario_catalog_awareness-course.png").read_bytes() == cover

    second = create_app(settings)
    assert second.state.showroom_store.list_scenarios(public_only=False)[0]["revision"] == 1

    write_catalog(
        tmp_path,
        [catalog_scenario(description="Updated from Git", cover="covers/awareness-course.png")],
    )
    third = create_app(settings)
    updated = third.state.showroom_store.list_scenarios(public_only=False)[0]
    assert updated["description"] == "Updated from Git"
    assert updated["revision"] == 2

    write_catalog(tmp_path, [catalog_scenario(description="Updated from Git")])
    without_cover = create_app(settings)
    assert without_cover.state.showroom_store.list_scenarios(public_only=False)[0]["cover_url"] is None
    assert not (Path(settings.showroom_cover_dir) / "scenario_catalog_awareness-course.png").exists()

    write_catalog(tmp_path, [])
    fourth = create_app(settings)
    retained = fourth.state.showroom_store.list_scenarios(public_only=False)
    assert [item["id"] for item in retained] == ["scenario_catalog_awareness-course"]
    assert retained[0]["revision"] == 2


def test_git_catalog_fails_closed_when_model_tuple_does_not_match(tmp_path: Path) -> None:
    catalog_path = write_catalog(
        tmp_path,
        [
            catalog_scenario(
                model={
                    "provider": "openrouter",
                    "base_url": "https://wrong.example/v1",
                    "model": "deepseek/deepseek-v4-flash",
                }
            )
        ],
    )
    settings = training_settings(
        tmp_path,
        showroom_catalog_path=str(catalog_path),
        openrouter_models=("deepseek/deepseek-v4-flash",),
    )

    with pytest.raises(ValueError, match="requires exactly one active model profile; matched 0"):
        create_app(settings)

    assert table_counts(Path(settings.sqlite_path), "showroom_scenarios") == {"showroom_scenarios": 0}


def test_git_catalog_rejects_stale_profile_removed_from_current_configuration(tmp_path: Path) -> None:
    create_app(
        training_settings(
            tmp_path,
            openrouter_models=("deepseek/deepseek-v4-flash",),
        )
    )
    catalog_path = write_catalog(tmp_path, [catalog_scenario()])
    settings = training_settings(
        tmp_path,
        showroom_catalog_path=str(catalog_path),
        openrouter_models=(),
    )

    with pytest.raises(ValueError, match="requires exactly one active model profile; matched 0"):
        create_app(settings)

    assert table_counts(Path(settings.sqlite_path), "showroom_scenarios") == {"showroom_scenarios": 0}


def test_git_catalog_prevalidates_all_entries_and_rejects_cover_escape(tmp_path: Path) -> None:
    escaped_cover = tmp_path / "outside.png"
    escaped_cover.write_bytes(b"\x89PNG\r\n\x1a\noutside")
    catalog_path = write_catalog(
        tmp_path,
        [
            catalog_scenario(key="valid-first"),
            catalog_scenario(key="invalid-second", cover="../outside.png"),
        ],
    )
    settings = training_settings(
        tmp_path,
        showroom_catalog_path=str(catalog_path),
        openrouter_models=("deepseek/deepseek-v4-flash",),
    )

    with pytest.raises(ValueError, match="cover is outside the catalog or missing"):
        create_app(settings)

    assert table_counts(Path(settings.sqlite_path), "showroom_scenarios") == {"showroom_scenarios": 0}


def test_checked_in_catalog_publishes_both_training_courses(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    catalog_candidates = (
        project_root / "configs" / "showroom" / "scenarios.json",
        Path(__file__).resolve().parents[1] / "configs" / "showroom" / "scenarios.json",
    )
    catalog_path = next(path for path in catalog_candidates if path.is_file())
    settings = training_settings(
        tmp_path,
        showroom_catalog_path=str(catalog_path),
        openrouter_api_base="https://openrouter.ai/api/v1",
        openrouter_models=("deepseek/deepseek-v4-flash", "google/gemini-3.6-flash"),
    )

    app = create_app(settings)
    scenarios = app.state.showroom_store.list_scenarios(public_only=False)
    by_id = {scenario["id"]: scenario for scenario in scenarios}

    assert set(by_id) == {
        "scenario_catalog_awareness-week",
        "scenario_catalog_test",
        "scenario_catalog_one-day-ellina",
        "scenario_catalog_one-day-v5",
        "scenario_catalog_one-day-v3",
    }
    assert {scenario_id: scenario["status"] for scenario_id, scenario in by_id.items()} == {
        "scenario_catalog_awareness-week": "published",
        "scenario_catalog_test": "draft",
        "scenario_catalog_one-day-ellina": "published",
        "scenario_catalog_one-day-v5": "draft",
        "scenario_catalog_one-day-v3": "draft",
    }
    public_scenarios = app.state.showroom_store.list_scenarios(public_only=True)
    assert {scenario["title"] for scenario in public_scenarios} == {
        "Awareness. Неделя",
        "Awareness. Один день",
    }
    assert {scenario["id"] for scenario in public_scenarios} == {
        "scenario_catalog_awareness-week",
        "scenario_catalog_one-day-ellina",
    }
    assert all(scenario["status"] == "published" for scenario in public_scenarios)

    weekly = by_id["scenario_catalog_awareness-week"]
    assert weekly["worldpack_id"] == "awareness"
    assert weekly["leaderboard_state_path"] == "player.resources.awareness-score"
    assert weekly["interactive_links_enabled"] is True
    assert weekly["interactive_workspace_enabled"] is True
    one_day_scenarios = [
        scenario for scenario in scenarios if scenario["worldpack_id"] == "awareness-one-day"
    ]
    assert all(scenario["worldpack_id"] == "awareness-one-day" for scenario in one_day_scenarios)
    assert all(
        scenario["leaderboard_state_path"] == "player.resources.total-score"
        for scenario in one_day_scenarios
    )
    assert by_id["scenario_catalog_test"]["interactive_workspace_enabled"] is True
    assert by_id["scenario_catalog_one-day-ellina"]["interactive_workspace_enabled"] is False

    with sqlite3.connect(settings.sqlite_path) as connection:
        model_rows = connection.execute(
            """
            SELECT ss.title, mp.provider, mp.base_url, mp.model
            FROM showroom_scenarios ss
            JOIN model_profiles mp ON mp.id = ss.model_profile_id
            ORDER BY ss.title
            """
        ).fetchall()
    model_by_title = {row[0]: tuple(row[1:]) for row in model_rows}
    assert model_by_title["Тест"] == (
        "openrouter",
        "https://openrouter.ai/api/v1",
        "deepseek/deepseek-v4-flash",
    )
    assert model_by_title["Один день. V3"] == (
        "openrouter",
        "https://openrouter.ai/api/v1",
        "google/gemini-3.6-flash",
    )

    target_cover = Path(settings.showroom_cover_dir) / "scenario_catalog_one-day-ellina.png"
    assert target_cover.stat().st_size == 1_967_339
    assert hashlib.sha256(target_cover.read_bytes()).hexdigest() == (
        "b2517ba22b4319999392fe9d52ea15ebc4dd15614ec1615ceaa0f9fe1d29aac0"
    )
    assert table_counts(
        Path(settings.sqlite_path),
        "showroom_visitors",
        "showroom_runs",
        "parties",
    ) == {"showroom_visitors": 0, "showroom_runs": 0, "parties": 0}
