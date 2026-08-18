from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from test_gateway import client, create_demo_party, login, write_worldpack


def candidate_client(tmp_path: Path, **settings_overrides: object) -> TestClient:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"] = {"schema_version": "rp-core.v2", "revision": 7}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return client(tmp_path, rp_contract_observed_revision=6, **settings_overrides)


def create_revision_seven_branch(api: TestClient, party_id: str) -> dict[str, Any]:
    checkpoint_response = api.post(
        f"/api/parties/{party_id}/checkpoints",
        json={"label": "Revision 7 diagnostic base"},
    )
    assert checkpoint_response.status_code == 200, checkpoint_response.text
    checkpoint = checkpoint_response.json()["checkpoint"]
    branch_response = api.post(
        f"/api/parties/{party_id}/branches",
        json={
            "checkpoint_id": checkpoint["id"],
            "label": "Revision 7 diagnostic branch",
            "rp_contract_revision": 7,
        },
    )
    assert branch_response.status_code == 200, branch_response.text
    branch = branch_response.json()["branch"]
    assert branch["rp_contract_revision"] == 7
    return branch


def store_snapshot(store: Any) -> dict[str, Any]:
    with store.connect() as connection:
        turn_count = connection.execute(
            "SELECT COUNT(*) FROM turns WHERE campaign_id = ?",
            (store.campaign_id,),
        ).fetchone()[0]
        state_version_count = connection.execute(
            "SELECT COUNT(*) FROM state_versions WHERE campaign_id = ?",
            (store.campaign_id,),
        ).fetchone()[0]
    return {
        "turn_count": int(turn_count),
        "state_version_count": int(state_version_count),
        "state": store.get_state(),
    }


def test_diagnostics_without_branch_id_keep_legacy_response_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = candidate_client(tmp_path)
    party = create_demo_party(api, title="Legacy diagnostic surface")
    captures: dict[str, Any] = {}

    def fake_context(store: Any, settings: Any, model_profile: Any) -> dict[str, str]:
        captures["context"] = (store, settings, model_profile)
        return {"marker": "legacy-context"}

    class FakeInspector:
        def __init__(self, settings: Any, store: Any):
            captures["preview"] = (store, settings)

        def preview(self, content: str, source: str = "current") -> dict[str, str]:
            return {"marker": "legacy-preview"}

    monkeypatch.setattr(main_module, "estimate_party_context", fake_context)
    monkeypatch.setattr(main_module, "PromptInspector", FakeInspector)

    context_response = api.get(f"/api/parties/{party['id']}/context")
    preview_response = api.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"content": "Inspect without a branch", "source": "current"},
    )

    assert context_response.status_code == 200, context_response.text
    assert preview_response.status_code == 200, preview_response.text
    assert context_response.content == json.dumps(
        {"party_id": party["id"], "context": {"marker": "legacy-context"}},
        separators=(",", ":"),
    ).encode()
    assert preview_response.content == json.dumps(
        {"party_id": party["id"], "preview": {"marker": "legacy-preview"}},
        separators=(",", ":"),
    ).encode()

    context_store, context_settings, _ = captures["context"]
    preview_store, preview_settings = captures["preview"]
    assert context_store.campaign_id == party["id"]
    assert preview_store.campaign_id == party["id"]
    assert context_settings.rp_contract_revision == 6
    assert preview_settings.rp_contract_revision == 6


def test_branch_diagnostics_use_branch_store_and_revision_with_source_party_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = candidate_client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    admin_user = login(admin)
    party_store = admin.app.state.party_store
    party_store.upsert_model_profile(
        {
            "id": "branch-source-model",
            "title": "Branch source narrator",
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "model": "deepseek/deepseek-v4-flash",
            "params": {"context_tokens": 384_000, "source": "test"},
            "api_key_source": "server_env_or_managed_key",
        }
    )
    party = create_demo_party(admin, title="Branch diagnostic runtime")
    narrator_settings = {
        "reasoning_effort": "xhigh",
        "temperature": 0.65,
        "top_p": 0.9,
        "max_tokens": 8192,
    }
    updated = admin.patch(
        f"/api/parties/{party['id']}/model",
        json={
            "model_profile_id": "branch-source-model",
            "narrator_settings": narrator_settings,
        },
    )
    assert updated.status_code == 200, updated.text
    party = updated.json()["party"]
    assert party["rp_contract_revision"] == 6
    assert party["owner_user_id"] == admin_user["id"]
    branch = create_revision_seven_branch(admin, str(party["id"]))
    captures: dict[str, Any] = {}

    def fake_context(store: Any, settings: Any, model_profile: Any) -> dict[str, str]:
        captures["context"] = (store, settings, model_profile)
        return {"marker": "branch-context"}

    class FakeInspector:
        def __init__(self, settings: Any, store: Any):
            captures["preview"] = (store, settings)

        def preview(self, content: str, source: str = "current") -> dict[str, str]:
            return {"marker": "branch-preview"}

    monkeypatch.setattr(main_module, "estimate_party_context", fake_context)
    monkeypatch.setattr(main_module, "PromptInspector", FakeInspector)

    context_response = admin.get(
        f"/api/parties/{party['id']}/context",
        params={"branch_id": branch["id"]},
    )
    preview_response = admin.post(
        f"/api/parties/{party['id']}/prompt/preview",
        params={"branch_id": branch["id"]},
        json={"content": "Inspect the candidate", "source": "last"},
    )

    assert context_response.status_code == 200, context_response.text
    assert preview_response.status_code == 200, preview_response.text
    assert context_response.json() == {
        "party_id": party["id"],
        "context": {"marker": "branch-context"},
        "branch_id": branch["id"],
    }
    assert preview_response.json() == {
        "party_id": party["id"],
        "preview": {"marker": "branch-preview"},
        "branch_id": branch["id"],
    }

    context_store, context_settings, context_profile = captures["context"]
    preview_store, preview_settings = captures["preview"]
    for store in (context_store, preview_store):
        assert store.campaign_id == branch["state_campaign_id"]
    for runtime_settings in (context_settings, preview_settings):
        assert runtime_settings.rp_contract_revision == 7
        assert runtime_settings.prompt_cache_session_id == (
            f"rp-party:{party['id']}:branch:{branch['id']}"
        )
        assert runtime_settings.narrative_model == "deepseek/deepseek-v4-flash"
        assert runtime_settings.world_system_prompt == "DEMO_WORLD_SYSTEM_RULE"
        assert runtime_settings.world_authors_note == "DEMO_WORLD_AUTHORS_NOTE"
    assert context_profile.id == "branch-source-model"
    assert context_profile.model == "deepseek/deepseek-v4-flash"

    source_after = admin.get(f"/api/parties/{party['id']}").json()["party"]
    assert source_after["rp_contract_revision"] == 6
    assert source_after["model_profile_id"] == "branch-source-model"
    assert source_after["narrator_settings"] == narrator_settings


def test_branch_diagnostics_are_owner_scoped_for_user_and_admin_owned_parties(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = candidate_client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    for username in ("alice", "bob"):
        response = admin.post(
            "/api/admin/users",
            json={"username": username, "password": f"{username}-secret", "role": "user"},
        )
        assert response.status_code == 200, response.text

    alice = TestClient(admin.app)
    bob = TestClient(admin.app)
    anonymous = TestClient(admin.app)
    login(alice, "alice", "alice-secret")
    login(bob, "bob", "bob-secret")

    admin_party = create_demo_party(admin, title="Admin-owned diagnostics", character_name="Admin")
    admin_branch = create_revision_seven_branch(admin, str(admin_party["id"]))
    alice_party = create_demo_party(alice, title="Alice diagnostics", character_name="Alice")
    alice_branch = create_revision_seven_branch(alice, str(alice_party["id"]))
    other_alice_party = create_demo_party(alice, title="Other Alice party", character_name="Alice Two")

    monkeypatch.setattr(main_module, "estimate_party_context", lambda *args: {"marker": "context"})

    class FakeInspector:
        def __init__(self, settings: Any, store: Any):
            pass

        def preview(self, content: str, source: str = "current") -> dict[str, str]:
            return {"marker": "preview"}

    monkeypatch.setattr(main_module, "PromptInspector", FakeInspector)

    def responses(api: TestClient, party_id: str, branch_id: str) -> tuple[Any, Any]:
        return (
            api.get(
                f"/api/parties/{party_id}/context",
                params={"branch_id": branch_id},
            ),
            api.post(
                f"/api/parties/{party_id}/prompt/preview",
                params={"branch_id": branch_id},
                json={"content": "Inspect", "source": "last"},
            ),
        )

    for response in responses(admin, str(admin_party["id"]), str(admin_branch["id"])):
        assert response.status_code == 200, response.text
        assert response.json()["branch_id"] == admin_branch["id"]
    for response in responses(alice, str(alice_party["id"]), str(alice_branch["id"])):
        assert response.status_code == 200, response.text
        assert response.json()["branch_id"] == alice_branch["id"]

    denied_scopes = (
        (bob, alice_party["id"], alice_branch["id"], 404),
        (admin, alice_party["id"], alice_branch["id"], 404),
        (alice, alice_party["id"], "branch_missing", 404),
        (alice, other_alice_party["id"], alice_branch["id"], 404),
        (anonymous, alice_party["id"], alice_branch["id"], 401),
    )
    for api, party_id, branch_id, expected_status in denied_scopes:
        for response in responses(api, str(party_id), str(branch_id)):
            assert response.status_code == expected_status, response.text


def test_recorded_branch_prompt_assembly_has_real_context_and_preview_parity_without_mutation(
    tmp_path: Path,
) -> None:
    admin = candidate_client(
        tmp_path,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    login(admin)
    party = create_demo_party(admin, title="Recorded branch prompt parity")
    assert party["rp_contract_revision"] == 6
    branch = create_revision_seven_branch(admin, str(party["id"]))
    source_store = admin.app.state.party_store.store_for_party(str(party["id"]))
    branch_store = admin.app.state.party_store.store_for_branch(
        str(party["id"]),
        str(branch["id"]),
        owner_user_id=str(party["owner_user_id"]),
    )
    prompt_messages = [
        {
            "role": "system",
            "content": (
                "PROMPT_AUTHORITY_HIERARCHY\n"
                "authoritative_outcome_current_action > uncovered_raw_tail > "
                "rp_story_memory > archive\n"
                "The current action is intent, not an automatic fact."
            ),
        },
        {"role": "user", "content": "Inspect the recorded revision 7 prompt."},
    ]
    prompt_assembly = {
        "schema_version": "rp-gateway.prompt-assembly.v1",
        "rp_contract_revision": 7,
        "authority_order": [
            "authoritative_outcome_current_action",
            "uncovered_raw_tail",
            "rp_story_memory",
            "archive",
        ],
        "story_memory_covered_through_turn_id": 0,
        "raw_tail_turn_ids": [],
        "included_block_ids": ["prompt_authority", "raw_turns"],
        "omitted_blocks": [],
    }
    source_store.record_turn(
        "source-revision-six-recorded-prompt",
        "req_source_revision_six_recorded_prompt",
        "Inspect the source prompt.",
        "Source narration.",
        {},
        source_store.current_version() or 1,
        prompt_messages=prompt_messages,
        metadata={"source_revision": 6},
        party_turn=1,
    )
    branch_store.record_turn(
        "branch-revision-seven-recorded-prompt",
        "req_branch_revision_seven_recorded_prompt",
        "Inspect the recorded revision 7 prompt.",
        "Branch narration.",
        {},
        branch_store.current_version() or 1,
        prompt_messages=prompt_messages,
        metadata={"prompt_assembly": prompt_assembly},
        party_turn=1,
    )
    before = {
        "source": store_snapshot(source_store),
        "branch": store_snapshot(branch_store),
    }

    branch_context_response = admin.get(
        f"/api/parties/{party['id']}/context",
        params={"branch_id": branch["id"]},
    )
    branch_preview_response = admin.post(
        f"/api/parties/{party['id']}/prompt/preview",
        params={"branch_id": branch["id"]},
        json={"source": "last"},
    )
    source_context_response = admin.get(f"/api/parties/{party['id']}/context")
    source_preview_response = admin.post(
        f"/api/parties/{party['id']}/prompt/preview",
        json={"source": "last"},
    )

    for response in (
        branch_context_response,
        branch_preview_response,
        source_context_response,
        source_preview_response,
    ):
        assert response.status_code == 200, response.text
    branch_context = branch_context_response.json()["context"]
    branch_preview = branch_preview_response.json()["preview"]
    assert branch_context["prompt_source"] == "recorded_last_turn"
    assert branch_preview["source"] == "recorded_last_turn"
    assert branch_context["prompt_assembly"] == prompt_assembly
    assert branch_preview["prompt_assembly"] == prompt_assembly
    assert branch_context["prompt_assembly"] == branch_preview["prompt_assembly"]
    assert "prompt_assembly" not in source_context_response.json()["context"]
    assert "prompt_assembly" not in source_preview_response.json()["preview"]

    after = {
        "source": store_snapshot(source_store),
        "branch": store_snapshot(branch_store),
    }
    assert after == before
