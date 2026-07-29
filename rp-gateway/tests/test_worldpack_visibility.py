from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from test_gateway import client, login, write_worldpack


def showroom_scenario_payload(model_id: str) -> dict[str, object]:
    return {
        "title": "Private-world boundary",
        "description": "Visibility boundary test.",
        "status": "published",
        "scenario_type": "rp",
        "model_profile_id": model_id,
        "world_source": "preset",
        "worldpack_id": "demo-world",
        "leaderboard_enabled": True,
        "leaderboard_metric": "state_path",
        "leaderboard_state_path": "meta.turn",
        "leaderboard_label": "Turns",
    }


def test_admin_controls_worldpack_visibility_and_private_worlds_are_admin_only(tmp_path: Path):
    write_worldpack(tmp_path, supported_modes=["rp"])
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    created = admin.post(
        "/api/admin/users",
        json={"username": "player", "password": "player-secret", "role": "user"},
    )
    assert created.status_code == 200, created.text

    player = TestClient(admin.app)
    login(player, "player", "player-secret")
    assert admin.get("/api/worldpacks").json()["worldpacks"][0]["visibility"] == "public"
    assert player.get("/api/worldpacks/demo-world").status_code == 200

    private = admin.patch(
        "/api/admin/worldpacks/demo-world/visibility",
        json={"visibility": "private"},
    )
    assert private.status_code == 200, private.text
    assert private.json()["worldpack"]["visibility"] == "private"
    assert {pack["id"] for pack in admin.get("/api/worldpacks").json()["worldpacks"]} == {"demo-world"}
    assert player.get("/api/worldpacks").json()["worldpacks"] == []
    assert player.get("/api/worldpacks/demo-world").status_code == 404
    assert player.get("/api/worldpacks/demo-world/player-templates").status_code == 404
    assert player.post(
        "/api/player-characters",
        json={"worldpack_id": "demo-world", "name": "Blocked", "description": "Blocked", "profile": {}},
    ).status_code == 400
    assert player.patch(
        "/api/admin/worldpacks/demo-world/visibility",
        json={"visibility": "public"},
    ).status_code == 403

    public = admin.patch(
        "/api/admin/worldpacks/demo-world/visibility",
        json={"visibility": "public"},
    )
    assert public.status_code == 200, public.text
    assert player.get("/api/worldpacks/demo-world").status_code == 200


def test_private_worldpack_cannot_be_used_or_exposed_in_showroom(tmp_path: Path):
    write_worldpack(tmp_path, supported_modes=["rp"])
    admin = client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    scenario_response = admin.post(
        "/api/admin/showroom/scenarios",
        json=showroom_scenario_payload(model_id),
    )
    assert scenario_response.status_code == 200, scenario_response.text
    scenario = scenario_response.json()["scenario"]
    public = TestClient(admin.app)
    assert len(public.get("/api/showroom/scenarios").json()["scenarios"]) == 1

    private = admin.patch(
        "/api/admin/worldpacks/demo-world/visibility",
        json={"visibility": "private"},
    )
    assert private.status_code == 200, private.text
    assert public.get("/api/showroom/scenarios").json()["scenarios"] == []
    blocked_run = public.post(
        f"/api/showroom/scenarios/{scenario['id']}/runs",
        json={
            "character_name": "Blocked visitor",
            "character_prompt": "Should not start.",
            "leaderboard_opt_in": False,
            "client_request_id": "private-world-run",
        },
    )
    assert blocked_run.status_code == 400
    assert "not found" in blocked_run.json()["detail"]

    blocked_scenario = admin.post(
        "/api/admin/showroom/scenarios",
        json={**showroom_scenario_payload(model_id), "title": "Still private"},
    )
    assert blocked_scenario.status_code == 400
    assert "private worldpack" in blocked_scenario.json()["detail"]
