from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.main import party_start_outcome, party_start_prompt, settings_for_party
from app.services.narrative import NarrativeClient
from app.services.party_store import PartyStore
from test_gateway import base_state, client, create_demo_party, write_worldpack


def write_revision11_worldpack(root: Path, *, invalid_seed: bool = False) -> Path:
    pack_dir = write_worldpack(root, rp_revision=11)
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "player_role": "Default opening role",
            "presets": [
                {
                    "id": "first",
                    "title": "First preset",
                    "world_system_prompt": "presets/first/gm-system.md",
                    "world_authors_note": "presets/first/authors-note.md",
                },
                {
                    "id": "default",
                    "title": "Default preset",
                    "world_system_prompt": "presets/default/gm-system.md",
                    "world_authors_note": "presets/default/authors-note.md",
                },
            ],
            "presets_default": "default",
            "openings": [
                {
                    "id": "first",
                    "title": "First opening",
                    "player_role": "First opening role",
                    "prompt": "prompts/openings/first/opening-scene.md",
                    "state_seed": "prompts/openings/first/state-seed.json",
                },
                {
                    "id": "default",
                    "title": "Default opening",
                    "player_role": "Default opening role",
                    "prompt": "prompts/openings/default/opening-scene.md",
                    "state_seed": "prompts/openings/default/state-seed.json",
                },
            ],
            "openings_default": "default",
        }
    )

    preset_text = {
        "first": ("FIRST SYSTEM", "FIRST AUTHORS"),
        "default": ("\ufeffDEFAULT SYSTEM\r\nsecond line", "DEFAULT AUTHORS"),
    }
    for preset_id, (system_text, authors_text) in preset_text.items():
        preset_dir = pack_dir / "presets" / preset_id
        preset_dir.mkdir(parents=True)
        (preset_dir / "gm-system.md").write_text(system_text, encoding="utf-8", newline="")
        (preset_dir / "authors-note.md").write_text(authors_text, encoding="utf-8", newline="")

    seeds: dict[str, dict[str, object]] = {}
    for opening_id, role in (("first", "First opening role"), ("default", "Default opening role")):
        seed = base_state()
        seed["meta"]["campaign_id"] = f"seed-{opening_id}"
        seed["meta"]["state_version"] = 77
        seed["player"]["location"] = f"seed-{opening_id}-location"
        seed["player"]["name"] = "seed name"
        seed["player"]["role"] = "seed role"
        if invalid_seed and opening_id == "default":
            seed.pop("factions")
        seeds[opening_id] = seed
        opening_dir = pack_dir / "prompts" / "openings" / opening_id
        opening_dir.mkdir(parents=True)
        (opening_dir / "opening-scene.md").write_text(
            f"{opening_id.upper()} OPENING",
            encoding="utf-8",
            newline="",
        )
        (opening_dir / "state-seed.json").write_text(
            json.dumps(seed, ensure_ascii=False),
            encoding="utf-8",
            newline="",
        )

    # The four legacy root files are byte-for-byte aliases of the explicit defaults.
    (pack_dir / "prompts" / "gm-system.md").write_bytes(
        (pack_dir / "presets" / "default" / "gm-system.md").read_bytes()
    )
    (pack_dir / "prompts" / "authors-note.md").write_bytes(
        (pack_dir / "presets" / "default" / "authors-note.md").read_bytes()
    )
    (pack_dir / "prompts" / "opening-scene.md").write_bytes(
        (pack_dir / "prompts" / "openings" / "default" / "opening-scene.md").read_bytes()
    )
    (pack_dir / "state-seed.json").write_bytes(
        (pack_dir / "prompts" / "openings" / "default" / "state-seed.json").read_bytes()
    )
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return pack_dir


def model_id(api) -> str:
    return api.get("/api/model-profiles").json()["model_profiles"][0]["id"]


def create_revision11_character(api, *, opening_id: str | None = None, patch: list[dict] | None = None) -> dict:
    draft_payload: dict[str, object] = {
        "worldpack_id": "demo-world",
        "name": "Mira",
        "concept": "",
    }
    if opening_id is not None:
        draft_payload["opening_id"] = opening_id
    draft_response = api.post("/api/player-characters/draft", json=draft_payload)
    assert draft_response.status_code == 200, draft_response.text
    draft = draft_response.json()["draft"]
    if patch is not None:
        draft["starting_state_patch_json"] = json.dumps(patch)
    created = api.post("/api/player-characters", json=draft)
    assert created.status_code == 200, created.text
    return created.json()["player_character"]


def create_revision11_party(
    api,
    character_id: str,
    *,
    preset_id: str | None = None,
    opening_id: str | None = None,
):
    payload: dict[str, object] = {
        "title": "Revision 11",
        "scenario_type": "rp",
        "worldpack_id": "demo-world",
        "player_character_id": character_id,
        "model_profile_id": model_id(api),
    }
    if preset_id is not None:
        payload["preset_id"] = preset_id
    if opening_id is not None:
        payload["opening_id"] = opening_id
    return api.post("/api/parties", json=payload)


def test_revision11_catalog_defaults_bind_character_and_party(tmp_path: Path) -> None:
    write_revision11_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=11)

    pack = api.get("/api/worldpacks/demo-world").json()["worldpack"]
    assert pack["presets_default"] == "default"
    assert pack["openings_default"] == "default"
    assert [item["id"] for item in pack["presets"]] == ["first", "default"]
    assert pack["openings"][1] == {
        "id": "default",
        "title": "Default opening",
        "player_role": "Default opening role",
    }

    character = create_revision11_character(api)
    assert character["opening_id"] == "default"
    assert character["description"] == "Default opening role"
    party_response = create_revision11_party(api, character["id"])
    assert party_response.status_code == 200, party_response.text
    party = party_response.json()["party"]
    assert party["preset_id"] == "default"
    assert party["opening_id"] == "default"
    assert set(party["worldpack_materialization_hashes"]) == {
        "world_system_prompt",
        "world_authors_note",
        "opening_prompt",
        "player_role",
        "state_seed",
    }
    assert "worldpack_materialization" not in party


def test_revision11_rejects_legacy_contract_schema_on_all_entry_points(tmp_path: Path) -> None:
    pack_dir = write_revision11_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=11)
    character = create_revision11_character(api)

    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rp_contract"]["schema_version"] = "rp-core.v1"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="requires RP contract rp-core.v2"):
        api.get("/api/worldpacks")

    draft = api.post(
        "/api/player-characters/draft",
        json={"worldpack_id": "demo-world", "name": "Mira", "concept": ""},
    )
    assert draft.status_code == 400
    assert "requires RP contract rp-core.v2" in draft.text

    party = create_revision11_party(api, character["id"])
    assert party.status_code == 400
    assert "requires RP contract rp-core.v2" in party.text
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        assert connection.execute("SELECT count(*) FROM player_characters").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM parties").fetchone()[0] == 0


def test_revision11_explicit_nondefault_choices_drive_character_snapshot_and_seed(tmp_path: Path) -> None:
    write_revision11_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=11)

    character = create_revision11_character(api, opening_id="first")
    assert character["opening_id"] == "first"
    assert character["description"] == "First opening role"
    assert character["profile"]["opening_id"] == "first"
    assert character["profile"]["player_role"] == "First opening role"
    party_response = create_revision11_party(
        api,
        character["id"],
        preset_id="first",
        opening_id="first",
    )
    assert party_response.status_code == 200, party_response.text
    party = party_response.json()["party"]
    assert party["preset_id"] == "first"
    assert party["opening_id"] == "first"

    stored = api.app.state.party_store.get_party(party["id"])
    snapshot = stored.worldpack_materialization
    assert snapshot["world_system_prompt"] == "FIRST SYSTEM"
    assert snapshot["world_authors_note"] == "FIRST AUTHORS"
    assert snapshot["opening_prompt"] == "FIRST OPENING"
    assert snapshot["player_role"] == "First opening role"
    assert snapshot["state_seed"]["player"]["location"] == "seed-first-location"
    state = api.app.state.party_store.store_for_party(party["id"]).get_state()
    assert state["player"]["location"] == "seed-first-location"
    assert state["player"]["role"] == "First opening role"


def test_revision11_observed_gate_precedes_character_party_and_state_writes(tmp_path: Path) -> None:
    write_revision11_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=10)

    character = api.post(
        "/api/player-characters",
        json={"worldpack_id": "demo-world", "name": "Mira", "description": "x", "profile": {}},
    )
    assert character.status_code == 400
    party = create_revision11_party(api, "pc_does_not_exist")
    assert party.status_code == 400
    assert "not activated" in party.text

    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        assert connection.execute("SELECT count(*) FROM player_characters").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM parties").fetchone()[0] == 0
    party_state_root = tmp_path / "state" / "parties"
    assert not party_state_root.exists() or not any(party_state_root.iterdir())


@pytest.mark.parametrize("invalid_role", ["\ufeff", "R" * 4001])
def test_revision11_invalid_player_role_fails_before_draft_or_character_write(
    tmp_path: Path,
    invalid_role: str,
) -> None:
    pack_dir = write_revision11_worldpack(tmp_path)
    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["player_role"] = invalid_role
    manifest["openings"][1]["player_role"] = invalid_role
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    api = client(tmp_path, rp_contract_observed_revision=11)

    draft = api.post(
        "/api/player-characters/draft",
        json={"worldpack_id": "demo-world", "name": "Mira", "concept": ""},
    )
    assert draft.status_code == 400
    assert "invalid player_role" in draft.text
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        assert connection.execute("SELECT count(*) FROM player_characters").fetchone()[0] == 0


def test_revision11_unknown_and_path_like_ids_fail_without_party_state(tmp_path: Path) -> None:
    write_revision11_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=11)
    character = create_revision11_character(api)

    unknown = create_revision11_party(api, character["id"], preset_id="missing")
    assert unknown.status_code == 400
    unsafe = create_revision11_party(api, character["id"], preset_id="../default")
    assert unsafe.status_code == 422
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        assert connection.execute("SELECT count(*) FROM parties").fetchone()[0] == 0
    assert not any((tmp_path / "state" / "parties").iterdir())


def test_revision11_snapshot_hashes_overlay_order_and_source_edits_are_ignored(tmp_path: Path) -> None:
    pack_dir = write_revision11_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=11)
    character = create_revision11_character(
        api,
        patch=[
            {"op": "replace", "path": "/player/location", "value": "patched-location"},
            {"op": "replace", "path": "/player/role", "value": "patched-role"},
        ],
    )
    party_response = create_revision11_party(api, character["id"])
    assert party_response.status_code == 200, party_response.text
    party_id = party_response.json()["party"]["id"]
    party_store = api.app.state.party_store
    party = party_store.get_party(party_id)

    with party_store.connect() as connection:
        row = connection.execute(
            "SELECT preset_id, opening_id, worldpack_materialization_json FROM parties WHERE id = ?",
            (party_id,),
        ).fetchone()
    snapshot = json.loads(row["worldpack_materialization_json"])
    assert (row["preset_id"], row["opening_id"]) == ("default", "default")
    assert snapshot["world_system_prompt"] == "DEFAULT SYSTEM\nsecond line"
    assert snapshot["opening_prompt"] == "DEFAULT OPENING"
    assert snapshot["hashes"]["world_system_prompt"] == hashlib.sha256(
        snapshot["world_system_prompt"].encode("utf-8")
    ).hexdigest()
    assert snapshot["hashes"]["state_seed"] == hashlib.sha256(
        PartyStore.canonical_seed_json(snapshot["state_seed"]).encode("utf-8")
    ).hexdigest()

    state = json.loads(party_store.state_path_for(party_id).read_text(encoding="utf-8"))
    assert state["meta"]["campaign_id"] == party_id
    assert state["meta"]["state_version"] == 1
    assert state["player"]["name"] == "Mira"
    assert state["player"]["description"] == "Default opening role"
    assert state["player"]["location"] == "patched-location"
    assert state["player"]["role"] == "patched-role"

    runtime_before = settings_for_party(party_store.settings, party)
    start_before = party_start_prompt(party_store, party)
    (pack_dir / "presets" / "default" / "gm-system.md").write_text("EDITED SYSTEM", encoding="utf-8")
    (pack_dir / "prompts" / "opening-scene.md").write_text("EDITED OPENING", encoding="utf-8")
    refreshed = party_store.get_party(party_id)
    runtime_after = settings_for_party(party_store.settings, refreshed)
    start_after = party_start_prompt(party_store, refreshed)
    assert runtime_before.world_system_prompt == runtime_after.world_system_prompt == "DEFAULT SYSTEM\nsecond line"
    assert start_before == start_after
    assert "DEFAULT OPENING" in start_after
    assert "EDITED OPENING" not in start_after


def test_revision11_final_state_validation_runs_before_state_or_party_write(tmp_path: Path) -> None:
    write_revision11_worldpack(tmp_path, invalid_seed=True)
    api = client(tmp_path, rp_contract_observed_revision=11)
    character = create_revision11_character(api)

    response = create_revision11_party(api, character["id"])
    assert response.status_code == 400
    assert "state.factions" in response.text
    with sqlite3.connect(tmp_path / "rp_gateway.db") as connection:
        assert connection.execute("SELECT count(*) FROM parties").fetchone()[0] == 0
    assert not any((tmp_path / "state" / "parties").iterdir())


def test_revision11_repair_uses_materialized_preset_and_opening_only_for_start(tmp_path: Path) -> None:
    write_revision11_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=11)
    character = create_revision11_character(api)
    party = create_revision11_party(api, character["id"]).json()["party"]
    stored = api.app.state.party_store.get_party(party["id"])
    runtime = settings_for_party(api.app.state.settings, stored)
    narrative = NarrativeClient(runtime)
    state = api.app.state.party_store.store_for_party(party["id"]).get_state()
    outcome = party_start_outcome(party["id"], "rp", 11)

    normal_repair = narrative.repair_messages(state, outcome, "fix", "bad")
    start_repair = narrative.repair_messages(
        state,
        outcome,
        "fix",
        "bad",
        opening_prompt=stored.worldpack_materialization["opening_prompt"],
    )
    normal_blocks = [message["content"] for message in normal_repair]
    start_blocks = [message["content"] for message in start_repair]
    assert "WORLD_SYSTEM_PROMPT\nDEFAULT SYSTEM\nsecond line" in normal_blocks
    assert "WORLD_AUTHORS_NOTE\nDEFAULT AUTHORS" in normal_blocks
    assert not any(block.startswith("OPENING_PROMPT\n") for block in normal_blocks)
    assert "OPENING_PROMPT\nDEFAULT OPENING" in start_blocks


def test_revision11_branch_inherits_snapshot_and_missing_snapshot_fails_closed(tmp_path: Path) -> None:
    pack_dir = write_revision11_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=11)
    character = create_revision11_character(api)
    party = create_revision11_party(api, character["id"]).json()["party"]
    store = api.app.state.party_store.store_for_party(party["id"])
    checkpoint = store.create_memory_checkpoint("revision 11")

    (pack_dir / "presets" / "default" / "gm-system.md").write_text("EDITED", encoding="utf-8")
    branch = api.post(
        f"/api/parties/{party['id']}/branches",
        json={"checkpoint_id": checkpoint["id"], "label": "candidate", "rp_contract_revision": 11},
    )
    assert branch.status_code == 200, branch.text
    source = api.app.state.party_store.get_party(party["id"])
    inherited = settings_for_party(api.app.state.settings, source, effective_revision=11)
    assert inherited.world_system_prompt == "DEFAULT SYSTEM\nsecond line"

    with api.app.state.party_store.connect() as connection:
        connection.execute(
            "UPDATE parties SET preset_id = NULL, opening_id = NULL, worldpack_materialization_json = NULL WHERE id = ?",
            (party["id"],),
        )
    missing = api.post(
        f"/api/parties/{party['id']}/branches",
        json={"checkpoint_id": checkpoint["id"], "label": "missing", "rp_contract_revision": 11},
    )
    assert missing.status_code == 400
    assert "missing its WorldPack materialization" in missing.text


def test_revision10_payload_and_live_prompt_behavior_remain_unchanged(tmp_path: Path) -> None:
    pack_dir = write_worldpack(tmp_path, rp_revision=10)
    api = client(tmp_path, rp_contract_observed_revision=10)
    world = api.get("/api/worldpacks/demo-world").json()["worldpack"]
    assert "presets" not in world and "openings" not in world

    draft = api.post(
        "/api/player-characters/draft",
        json={"worldpack_id": "demo-world", "name": "Mira", "concept": ""},
    ).json()["draft"]
    assert "opening_id" not in draft
    character = api.post("/api/player-characters", json=draft).json()["player_character"]
    assert "opening_id" not in character
    party = create_demo_party(api)
    assert "preset_id" not in party
    assert "opening_id" not in party
    assert "worldpack_materialization_hashes" not in party
    with api.app.state.party_store.connect() as connection:
        before = connection.execute(
            "SELECT preset_id, opening_id, worldpack_materialization_json FROM parties WHERE id = ?",
            (party["id"],),
        ).fetchone()
        api.app.state.party_store.migrate_revision11_worldpack_materialization(connection)
        after = connection.execute(
            "SELECT preset_id, opening_id, worldpack_materialization_json FROM parties WHERE id = ?",
            (party["id"],),
        ).fetchone()
    assert tuple(before) == tuple(after) == (None, None, None)

    stored = api.app.state.party_store.get_party(party["id"])
    assert settings_for_party(api.app.state.settings, stored).world_system_prompt == "DEMO_WORLD_SYSTEM_RULE"
    assert "Player role: Investigator" in party_start_prompt(api.app.state.party_store, stored)
    (pack_dir / "prompts" / "gm-system.md").write_text("LIVE EDIT", encoding="utf-8")
    assert settings_for_party(api.app.state.settings, stored).world_system_prompt == "LIVE EDIT"
    rejected = api.post(
        "/api/player-characters",
        json={
            "worldpack_id": "demo-world",
            "name": "Mira",
            "description": "x",
            "profile": {},
            "opening_id": "default",
        },
    )
    assert rejected.status_code == 400

    missing_world = api.post(
        "/api/player-characters/draft",
        json={"worldpack_id": "missing-world", "name": "Mira", "concept": ""},
    )
    assert missing_world.status_code == 404
