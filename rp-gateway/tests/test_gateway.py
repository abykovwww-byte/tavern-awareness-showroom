from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome
from app.services.intent_parser import IntentParser
from app.services.narrative import NarrativeClient
from app.services.nvidia_catalog import parse_build_catalog


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
        "files": {"state_seed": "state-seed.json", "opening_scene": "prompts/opening-scene.md", "world_info": "world-info/index.md"},
    }
    seed = base_state()
    seed["meta"]["campaign_id"] = pack_id
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (pack_dir / "state-seed.json").write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    (pack_dir / "prompts").mkdir()
    (pack_dir / "prompts" / "opening-scene.md").write_text("Rain taps the glass. What do you do?", encoding="utf-8")
    (pack_dir / "world-info").mkdir()
    (pack_dir / "world-info" / "index.md").write_text("# Demo World\n", encoding="utf-8")
    return pack_dir


def create_demo_party(c: TestClient, title: str = "Demo Party", character_name: str = "Mira") -> dict[str, object]:
    model_id = c.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    character = c.post(
        "/api/player-characters",
        json={"worldpack_id": "demo-world", "name": character_name, "description": "Investigator", "profile": {}},
    ).json()["player_character"]
    return c.post(
        "/api/parties",
        json={
            "title": title,
            "worldpack_id": "demo-world",
            "player_character_id": character["id"],
            "model_profile_id": model_id,
        },
    ).json()["party"]


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


def test_model_profiles_include_rp_descriptions(tmp_path: Path):
    c = client(tmp_path)
    models = c.get("/api/model-profiles").json()["model_profiles"]
    assert len(models) >= 8
    assert models[0]["model"] == "z-ai/glm-5.2"
    assert models[0]["rp_fit"]
    assert models[0]["context_window"]
    assert "reasoning" in models[0]["tags"]


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
    assert estimate["context_limit_tokens"] == 1_000_000
    assert estimate["estimated_prompt_tokens"] > 0
    assert estimate["estimated_total_tokens"] >= estimate["estimated_prompt_tokens"]
    assert estimate["history_turns_total"] == 7
    assert estimate["direct_history_messages"] == 14
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
        json={"content": '/check persuasion target=advisor skill=1 difficulty=8 goal="borrow the map"'},
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


def test_party_memory_auto_summary_is_party_isolated(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
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
    assert first_memory["memory"]["from_turn_id"] == 1
    assert first_memory["memory"]["to_turn_id"] == 10
    assert "old clue 0" in first_memory["memory"]["summary_text"]

    second_memory = c.get(f"/api/parties/{second['id']}/memory").json()
    assert second_memory["memory"] is None
    assert second_memory["stats"]["total_turns"] == 0


def test_party_journal_auto_summary_is_party_isolated(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path)
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
    c = client(tmp_path)
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
    assert before["memory"] is None
    assert before["stats"]["eligible_old_turns"] == 1

    generated = c.post(
        f"/api/parties/{party['id']}/memory/summarize",
        json={"force": True},
        headers={"Authorization": "Bearer test"},
    )
    assert generated.status_code == 200
    assert generated.json()["generated"] is True
    assert generated.json()["memory"]["to_turn_id"] == 1

    deleted = c.delete(f"/api/parties/{party['id']}/memory/latest")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["memory"] is None


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
            "worldpack_id": pack["id"],
            "player_character_id": character["id"],
            "model_profile_id": model_id,
        },
    ).json()["party"]
    state = c.get(f"/api/parties/{party['id']}/state").json()["state"]
    assert any(item["id"] == "world_prompt" for item in state["world_constraints"])
    assert state["player"]["name"] == "Ника"
    assert "стеклянном дожде" in state["player"]["description"]

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


def test_provider_http_error_returns_502_not_500(tmp_path: Path):
    c = client(tmp_path, mode="http-503")
    response = c.post(
        "/v1/chat/completions",
        json=chat_payload("/check stealth skill=1 difficulty=10"),
        headers={"Authorization": "Bearer test", "Idempotency-Key": "provider-http-503"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Narrative provider HTTP 503"


def test_party_message_provider_http_error_returns_502(tmp_path: Path):
    write_worldpack(tmp_path)
    c = client(tmp_path, mode="http-503")
    party = create_demo_party(c)

    response = c.post(
        f"/api/parties/{party['id']}/messages",
        json={"content": "/check stealth skill=1 difficulty=10"},
        headers={"Authorization": "Bearer test"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Narrative provider HTTP 503"


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
