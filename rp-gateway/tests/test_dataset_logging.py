import json
import sqlite3

from fastapi.testclient import TestClient

from app.services.state_store import StateStore
from test_gateway import client, create_demo_party, write_worldpack


def test_dataset_export_requires_party_and_turn_approval(tmp_path):
    write_worldpack(tmp_path, supported_modes=["rp"])
    c = client(tmp_path)
    party = create_demo_party(c, scenario_type="rp")

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "I listen at the locked door.", "idempotency_key": "dataset-turn-1"},
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 200, response.text
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        connection.execute(
            "UPDATE parties SET scenario_type = 'novel', status = 'archived' WHERE id = ?",
            (party["id"],),
        )

    candidates = c.get(f"/api/admin/datasets/parties/{party['id']}/turns").json()["turns"]
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["scenario_type"] == "novel"
    assert candidate["review_status"] == "review"
    assert {"novel", "main"}.issubset(candidate["auto_tags"])
    assert candidate["metadata"]["schema_version"] == "rp-gateway.turn.v1"
    assert candidate["metadata"]["validator_valid"] is None
    assert candidate["prompt_messages"]

    assert c.get("/api/admin/datasets/export.jsonl").text == ""

    approved_party = c.patch(
        f"/api/admin/datasets/parties/{party['id']}",
        json={"review_status": "approved", "tags": ["LoRA Pilot", "novel", "LoRA Pilot"]},
    )
    assert approved_party.status_code == 200, approved_party.text
    assert approved_party.json()["party"]["dataset_tags"] == ["lora-pilot", "novel"]

    approved_turn = c.put(
        f"/api/admin/datasets/parties/{party['id']}/turns/{candidate['turn_id']}",
        json={"review_status": "approved", "tags": ["continuity"], "notes": "Reviewed."},
    )
    assert approved_turn.status_code == 200, approved_turn.text

    export = c.get("/api/admin/datasets/export.jsonl?scenario_type=novel")
    assert export.status_code == 200
    assert export.headers["x-dataset-approved-turns"] == "1"
    assert export.headers["x-dataset-skipped-missing-prompt"] == "0"
    lines = [line for line in export.text.splitlines() if line]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["messages"][-1]["role"] == "assistant"
    assert record["messages"][-1]["content"] == candidate["assistant_response"]
    assert record["metadata"]["schema_version"] == "rp-gateway.sft.v1"
    assert record["metadata"]["group_id"] == party["state_campaign_id"]
    assert {"novel", "lora-pilot", "continuity", "main"}.issubset(record["metadata"]["tags"])


def test_checkpoint_branch_does_not_duplicate_approved_source_turn(tmp_path):
    write_worldpack(tmp_path, supported_modes=["rp"])
    c = client(tmp_path)
    party = create_demo_party(c, scenario_type="rp")
    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "I wait.", "idempotency_key": "dataset-main-turn"},
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 200, response.text
    turn = c.get(f"/api/admin/datasets/parties/{party['id']}/turns").json()["turns"][0]
    c.patch(
        f"/api/admin/datasets/parties/{party['id']}",
        json={"review_status": "approved", "tags": ["rp"]},
    )
    c.put(
        f"/api/admin/datasets/parties/{party['id']}/turns/{turn['turn_id']}",
        json={"review_status": "approved", "tags": [], "notes": ""},
    )

    checkpoint = c.post(
        f"/api/parties/{party['id']}/checkpoints",
        json={"label": "Dataset fork"},
    ).json()["checkpoint"]
    branch = c.post(
        f"/api/parties/{party['id']}/branches",
        json={"checkpoint_id": checkpoint["id"], "label": "Candidate branch"},
    ).json()["branch"]
    branch_turns = c.get(
        f"/api/admin/datasets/parties/{party['id']}/turns?branch_id={branch['id']}"
    ).json()["turns"]
    assert len(branch_turns) == 1
    assert branch_turns[0]["review_status"] == "review"
    assert "branch" in branch_turns[0]["auto_tags"]

    lines = [line for line in c.get("/api/admin/datasets/export.jsonl").text.splitlines() if line]
    assert len(lines) == 1


def test_feedback_migration_preserves_legacy_likes(tmp_path):
    sqlite_path = tmp_path / "legacy-feedback.db"
    with sqlite3.connect(sqlite_path) as connection:
        connection.executescript(
            """
            CREATE TABLE campaigns (id TEXT PRIMARY KEY, created_at INTEGER NOT NULL);
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_id TEXT NOT NULL,
                player_message TEXT NOT NULL,
                narrative_response TEXT NOT NULL,
                response_json TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(campaign_id, idempotency_key)
            );
            CREATE TABLE turn_feedback (
                campaign_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL,
                liked INTEGER NOT NULL DEFAULT 0,
                source_ui TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(campaign_id, turn_id)
            );
            INSERT INTO campaigns(id, created_at) VALUES('legacy-campaign', 1);
            INSERT INTO turns(
                campaign_id, idempotency_key, request_id, player_message,
                narrative_response, response_json, state_version, created_at
            ) VALUES('legacy-campaign', 'legacy-turn', 'legacy-request', 'Hello', 'Hi', '{}', 1, 1);
            INSERT INTO turn_feedback(
                campaign_id, turn_id, liked, source_ui, created_at, updated_at
            ) VALUES('legacy-campaign', 1, 1, 'light-gui', 1, 1);
            """
        )

    store = StateStore(str(sqlite_path), "legacy-campaign", str(tmp_path / "legacy-state.json"))
    turn = store.turn_history()[0]
    assert turn["player_rating"] == "positive"
    assert turn["player_liked"] is True
    assert turn["player_disliked"] is False
    with sqlite3.connect(sqlite_path) as connection:
        rating = connection.execute("SELECT rating FROM turn_feedback WHERE turn_id = 1").fetchone()[0]
    assert rating == 1


def test_player_rating_is_a_separate_audited_dataset_signal(tmp_path):
    write_worldpack(tmp_path, supported_modes=["rp"])
    c = client(tmp_path)
    party = create_demo_party(c, scenario_type="rp")
    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "I offer the guard a convincing alibi.", "idempotency_key": "liked-turn"},
        headers={"Authorization": "Bearer test"},
    )
    assert response.status_code == 200, response.text
    turn = c.get(f"/api/parties/{party['id']}/history").json()["turns"][0]
    assert turn["player_rating"] == "none"
    assert turn["player_liked"] is False
    assert turn["player_disliked"] is False

    liked = c.put(
        f"/api/parties/{party['id']}/turns/{turn['id']}/feedback",
        json={"rating": "positive"},
    )
    assert liked.status_code == 200, liked.text
    assert liked.json()["feedback"]["rating"] == "positive"
    assert liked.json()["feedback"]["liked"] is True
    assert liked.json()["feedback"]["disliked"] is False
    assert liked.json()["feedback"]["source_ui"] == "light-gui"
    history_turn = c.get(f"/api/parties/{party['id']}/history").json()["turns"][0]
    assert history_turn["player_rating"] == "positive"
    assert history_turn["player_liked"] is True

    candidate = c.get(f"/api/admin/datasets/parties/{party['id']}/turns").json()["turns"][0]
    assert "player-liked" in candidate["auto_tags"]
    assert candidate["player_feedback"] == {
        "rating": "positive",
        "liked": True,
        "disliked": False,
        "source_ui": "light-gui",
    }
    assert candidate["review_status"] == "review"

    c.patch(
        f"/api/admin/datasets/parties/{party['id']}",
        json={"review_status": "approved", "tags": ["feedback-pilot"]},
    )
    c.put(
        f"/api/admin/datasets/parties/{party['id']}/turns/{turn['id']}",
        json={"review_status": "approved", "tags": [], "notes": "Positive player signal."},
    )
    exported = json.loads(c.get("/api/admin/datasets/export.jsonl").text.strip())
    assert "player-liked" in exported["metadata"]["tags"]
    assert exported["metadata"]["player_feedback"] == {
        "rating": "positive",
        "liked": True,
        "disliked": False,
        "source_ui": "light-gui",
    }

    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        event = connection.execute(
            "SELECT event_json FROM audit_events WHERE event_type = 'turn_feedback_updated' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert json.loads(event[0]) == {
        "turn_id": turn["id"],
        "rating": "positive",
        "liked": True,
        "disliked": False,
        "source_ui": "light-gui",
    }

    disliked = c.put(
        f"/api/parties/{party['id']}/turns/{turn['id']}/feedback",
        json={"rating": "negative"},
    )
    assert disliked.status_code == 200, disliked.text
    assert disliked.json()["feedback"]["rating"] == "negative"
    assert disliked.json()["feedback"]["liked"] is False
    assert disliked.json()["feedback"]["disliked"] is True
    candidate = c.get(f"/api/admin/datasets/parties/{party['id']}/turns").json()["turns"][0]
    assert "player-liked" not in candidate["auto_tags"]
    assert "player-disliked" in candidate["auto_tags"]
    assert candidate["player_feedback"]["rating"] == "negative"

    cleared = c.put(
        f"/api/parties/{party['id']}/turns/{turn['id']}/feedback",
        json={"rating": "none"},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["feedback"]["rating"] == "none"
    candidate = c.get(f"/api/admin/datasets/parties/{party['id']}/turns").json()["turns"][0]
    assert "player-liked" not in candidate["auto_tags"]
    assert "player-disliked" not in candidate["auto_tags"]


def test_showroom_rating_requires_the_visitor_owned_run(tmp_path):
    write_worldpack(tmp_path, supported_modes=["rp"])
    admin = client(tmp_path)
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    scenario = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Feedback scenario",
            "description": "A public feedback test.",
            "status": "published",
            "scenario_type": "rp",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "demo-world",
            "leaderboard_enabled": False,
            "leaderboard_metric": "turn_count",
            "leaderboard_state_path": "meta.turn",
            "leaderboard_label": "Turns",
        },
    ).json()["scenario"]
    visitor = TestClient(admin.app)
    run = visitor.post(
        f"/api/showroom/scenarios/{scenario['id']}/runs",
        json={
            "character_name": "Visitor Hero",
            "character_prompt": "A careful participant.",
            "leaderboard_opt_in": False,
            "client_request_id": "feedback-run",
        },
    ).json()["run"]
    player_turn = visitor.post(
        f"/api/showroom/runs/{run['id']}/messages",
        json={"content": "I ask the witness to clarify.", "idempotency_key": "showroom-liked-turn"},
    )
    assert player_turn.status_code == 200, player_turn.text
    turn = visitor.get(f"/api/showroom/runs/{run['id']}/history").json()["turns"][0]

    liked = visitor.put(
        f"/api/showroom/runs/{run['id']}/turns/{turn['id']}/feedback",
        json={"rating": "positive"},
    )
    assert liked.status_code == 200, liked.text
    assert liked.json()["feedback"]["rating"] == "positive"
    assert liked.json()["feedback"]["source_ui"] == "showroom"
    history_turn = visitor.get(f"/api/showroom/runs/{run['id']}/history").json()["turns"][0]
    assert history_turn["player_rating"] == "positive"

    disliked = visitor.put(
        f"/api/showroom/runs/{run['id']}/turns/{turn['id']}/feedback",
        json={"rating": "negative"},
    )
    assert disliked.status_code == 200, disliked.text
    assert disliked.json()["feedback"]["rating"] == "negative"

    intruder = TestClient(admin.app)
    forbidden = intruder.put(
        f"/api/showroom/runs/{run['id']}/turns/{turn['id']}/feedback",
        json={"rating": "negative"},
    )
    assert forbidden.status_code == 404
