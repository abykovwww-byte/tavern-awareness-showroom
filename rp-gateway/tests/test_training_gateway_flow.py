from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.services.narrative import NarrativeClient
from app.services.training_runtime import TrainingRuntimeService


def training_client(tmp_path: Path, slug: str = "awareness") -> TestClient:
    source = Path(__file__).resolve().parents[2] / "worldpacks" / slug
    pack = tmp_path / "worldpacks" / slug
    shutil.copytree(source, pack)
    settings = Settings(
        app_env="test",
        campaign_id=slug,
        scenario_type="training",
        database_url=f"sqlite:///{tmp_path / 'awareness_gateway.db'}",
        world_state_path=str(pack / "state-seed.json"),
        party_state_root=str(tmp_path / "state" / "parties"),
        showroom_cover_dir=str(tmp_path / "showroom-covers"),
        worldpacks_path=str(tmp_path / "worldpacks"),
        showroom_catalog_path="",
        llm_api_base="mock://training-provider",
        llm_api_key="test-key",
        openrouter_api_base="mock://training-provider",
        openrouter_api_key="test-key",
        service_openrouter_api_key="test-key",
        openrouter_model_catalog_live=False,
        local_llm_enabled=False,
        post_turn_helpers_inline=True,
        auth_enabled=True,
        bootstrap_admin_username="admin",
        bootstrap_admin_password="admin-secret",
    )
    return TestClient(create_app(settings))


def latest_turn_metadata(client: TestClient, party_id: str) -> dict[str, object]:
    store = client.app.state.party_store.store_for_party(party_id)
    with sqlite3.connect(store.sqlite_path) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM turns WHERE campaign_id = ? ORDER BY id DESC LIMIT 1",
            (store.campaign_id,),
        ).fetchone()
    assert row is not None
    return json.loads(row[0])


def structured_provider_content(
    runtime: TrainingRuntimeService,
    state: dict,
    interaction: dict | None,
) -> dict:
    contract = runtime.prompt_contract(state, interaction)
    assert contract is not None and contract["kind"] == "turn"
    visible = runtime.fallback_text(state, interaction)
    header = str(contract["header"]).strip()
    question = str(contract.get("question") or "").strip()
    visible = visible.removeprefix(header).lstrip()
    if question and visible.endswith(question):
        visible = visible[: -len(question)].rstrip()
    marker_match = re.search(r"(?m)^(ПИСЬМО|СООБЩЕНИЕ)\s*$", visible)
    assert marker_match is not None
    blocks = {
        "ПИСЬМО": iter(runtime.structured_surface_blocks(visible, "ПИСЬМО")),
        "СООБЩЕНИЕ": iter(runtime.structured_surface_blocks(visible, "СООБЩЕНИЕ")),
    }
    structured: dict[str, dict[str, str]] = {}
    for surface_index, surface in enumerate(contract["surfaces"], start=1):
        marker = "ПИСЬМО" if surface["type"] == "email" else "СООБЩЕНИЕ"
        fields = [str(field) for field in surface["required_fields"]]
        for instance_index in range(1, int(surface.get("count", 1)) + 1):
            lines = next(blocks[marker]).strip().splitlines()[1:]
            positions: list[int] = []
            search_from = 0
            for field in fields:
                position = next(
                    index
                    for index in range(search_from, len(lines))
                    if lines[index].startswith(field)
                )
                positions.append(position)
                search_from = position + 1
            values: dict[str, str] = {}
            for field_index, field in enumerate(fields):
                start = positions[field_index]
                end = positions[field_index + 1] if field_index + 1 < len(positions) else len(lines)
                inline = lines[start][len(field) :].strip()
                value_lines = ([inline] if inline else []) + lines[start + 1 : end]
                values[field] = "\n".join(value_lines).strip()
            structured[f"surface_{surface_index}_{instance_index}"] = values
    site = interaction.get("site") if interaction else None
    artifacts = []
    if site:
        artifacts.append(
            {
                "artifact_key": site["artifact_key"],
                "blueprint_id": site["blueprint_id"],
                "slots": {slot_id: "Учебная проверка" for slot_id in site["slots"]},
            }
        )
    return {
        "schema_version": "rp-gateway.narrative-bundle.v3",
        "narrative_text": visible[: marker_match.start()].strip(),
        "visible_surfaces": structured,
        "artifacts": artifacts,
        "workspace_files": [],
    }


def test_showroom_opening_semantic_repair_reuses_raw_v3_values_only_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = training_client(tmp_path, "awareness-one-day")
    login = admin.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-secret"},
    )
    assert login.status_code == 200, login.text
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    created = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Opening semantic repair",
            "status": "published",
            "scenario_type": "training",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "awareness-one-day",
            "interactive_links_enabled": False,
            "interactive_workspace_enabled": False,
        },
    )
    assert created.status_code == 200, created.text
    scenario_id = created.json()["scenario"]["id"]

    public = TestClient(admin.app)
    run_response = public.post(
        f"/api/showroom/scenarios/{scenario_id}/runs",
        json={
            "character_name": "Коллега QA",
            "character_prompt": "Археолог по керамическим артефактам",
            "employee_position": "Специалист",
        },
    )
    assert run_response.status_code == 200, run_response.text
    run_id = run_response.json()["run"]["id"]
    with admin.app.state.showroom_store.connect() as connection:
        party_id = connection.execute(
            "SELECT party_id FROM showroom_runs WHERE id = ?",
            (run_id,),
        ).fetchone()["party_id"]
    party = admin.app.state.party_store.get_party(party_id)
    state_store = admin.app.state.party_store.store_for_party(party_id)
    runtime = TrainingRuntimeService(party.worldpack, state_store)
    opening_state = state_store.get_state()
    opening_state["meta"]["turn"] = 1
    opening_contract = runtime.prompt_contract(opening_state)
    assert opening_contract is not None and opening_contract["kind"] == "turn"
    captured_failed_bundle: dict[str, object] = {}
    provider_calls = 0

    async def provider_complete(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal provider_calls, captured_failed_bundle
        provider_calls += 1
        state = args[2]
        assert isinstance(state, dict)
        interaction = kwargs.get("artifact_contract")
        assert interaction is None or isinstance(interaction, dict)
        repair_instruction = args[5] if len(args) > 5 else kwargs.get("repair_instruction")
        bundle = structured_provider_content(runtime, state, interaction)
        if not repair_instruction:
            body = bundle["visible_surfaces"]["surface_1_1"]["Тело:"]
            description = str(state["player"]["description"])
            assert description in body
            bundle["visible_surfaces"]["surface_1_1"]["Тело:"] = body.replace(
                description,
                "текущая рабочая задача",
            )
        else:
            failed_response_text = kwargs.get("failed_response_text")
            assert isinstance(failed_response_text, str)
            captured_failed_bundle = json.loads(failed_response_text)
        content = json.dumps(bundle, ensure_ascii=False)
        return {
            "id": f"opening-semantic-repair-{provider_calls}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-training-model",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
        }

    monkeypatch.setattr(NarrativeClient, "complete", provider_complete)
    started = public.post(
        f"/api/showroom/runs/{run_id}/start",
        json={"idempotency_key": "opening-semantic-repair-start"},
    )
    assert started.status_code == 200, started.text
    public_text = started.json()["choices"][0]["message"]["content"]
    assert public_text.startswith(opening_contract["header"])
    assert public_text.count(opening_contract["header"]) == 1
    assert provider_calls == 2
    assert captured_failed_bundle["schema_version"] == "rp-gateway.narrative-bundle.v3"
    assert opening_contract["header"] not in str(captured_failed_bundle["narrative_text"])
    assert opening_contract["question"] not in str(captured_failed_bundle["narrative_text"])
    assert "ПИСЬМО" not in str(captured_failed_bundle["narrative_text"])
    fields = captured_failed_bundle["visible_surfaces"]["surface_1_1"]
    expected_fields = opening_contract["surfaces"][0]["required_fields"]
    assert list(fields) == expected_fields
    for field, value in fields.items():
        assert not str(value).lstrip().startswith(str(field))
    metadata = latest_turn_metadata(admin, party_id)
    assert metadata["repaired"] is True
    assert metadata["fallback"] is False
    assert metadata["validator_valid"] is True


def test_public_showroom_provider_turn_persists_training_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = training_client(tmp_path)
    login = admin.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin-secret"},
    )
    assert login.status_code == 200, login.text
    model_id = admin.get("/api/model-profiles").json()["model_profiles"][0]["id"]
    created = admin.post(
        "/api/admin/showroom/scenarios",
        json={
            "title": "Training provider flow",
            "status": "published",
            "scenario_type": "training",
            "model_profile_id": model_id,
            "world_source": "preset",
            "worldpack_id": "awareness",
            "interactive_links_enabled": True,
            "interactive_workspace_enabled": False,
        },
    )
    assert created.status_code == 200, created.text
    scenario_id = created.json()["scenario"]["id"]

    public = TestClient(admin.app)
    run_response = public.post(
        f"/api/showroom/scenarios/{scenario_id}/runs",
        json={
            "character_name": "Provider QA",
            "character_prompt": "Инженер QA",
            "employee_position": "Инженер QA",
        },
    )
    assert run_response.status_code == 200, run_response.text
    run_id = run_response.json()["run"]["id"]
    with admin.app.state.showroom_store.connect() as connection:
        party_id = connection.execute(
            "SELECT party_id FROM showroom_runs WHERE id = ?",
            (run_id,),
        ).fetchone()["party_id"]
    party = admin.app.state.party_store.get_party(party_id)
    state_store = admin.app.state.party_store.store_for_party(party_id)
    runtime = TrainingRuntimeService(party.worldpack, state_store)
    provider_turns: list[int] = []
    turn_two_failed_bundle: dict[str, object] = {}

    async def provider_complete(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal turn_two_failed_bundle
        state = args[2]
        assert isinstance(state, dict)
        turn = int(state["meta"]["turn"])
        provider_turns.append(turn)
        interaction = kwargs.get("artifact_contract")
        assert interaction is None or isinstance(interaction, dict)
        repair_instruction = args[5] if len(args) > 5 else kwargs.get("repair_instruction")
        bundle = structured_provider_content(runtime, state, interaction)
        site = interaction.get("site") if interaction else None
        if turn == 2 and not repair_instruction:
            for fields in bundle["visible_surfaces"].values():
                if "От:" in fields:
                    fields["От:"] = "Неизвестный отправитель"
        elif turn == 2:
            failed_response_text = kwargs.get("failed_response_text")
            assert isinstance(failed_response_text, str)
            turn_two_failed_bundle = json.loads(failed_response_text)
        elif turn == 3 and not repair_instruction:
            assert site is not None
            bundle["visible_surfaces"]["surface_2_1"]["Ссылки:"] = "нет"
        elif turn == 3:
            for fields in bundle["visible_surfaces"].values():
                for field, value in fields.items():
                    fields[field] = value.replace("\n", "\u2028")
        content = json.dumps(bundle, ensure_ascii=False)
        return {
            "id": f"training-provider-{turn}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-training-model",
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
            ],
        }

    monkeypatch.setattr(NarrativeClient, "complete", provider_complete)
    started = public.post(
        f"/api/showroom/runs/{run_id}/start",
        json={"idempotency_key": "training-flow-start"},
    )
    assert started.status_code == 200, started.text
    assert started.json()["choices"][0]["message"]["content"].startswith("Ход 1.")
    assert started.json()["choices"][0]["message"]["artifacts"]

    answered = public.post(
        f"/api/showroom/runs/{run_id}/messages",
        json={
            "content": "Не открываю подозрительный файл, сообщаю в SOC и продолжаю штатную рабочую задачу.",
            "idempotency_key": "training-flow-turn-1",
        },
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["choices"][0]["message"]["content"].startswith("Ход 2.")
    turn_two_state = state_store.get_state()
    turn_two_contract = runtime.prompt_contract(turn_two_state)
    assert turn_two_contract is not None and turn_two_contract["kind"] == "turn"
    assert turn_two_failed_bundle["schema_version"] == "rp-gateway.narrative-bundle.v3"
    turn_two_narrative = str(turn_two_failed_bundle["narrative_text"])
    assert turn_two_contract["header"] not in turn_two_narrative
    assert turn_two_contract["question"] not in turn_two_narrative
    assert "ПИСЬМО" not in turn_two_narrative
    assert "СООБЩЕНИЕ" not in turn_two_narrative
    turn_two_surfaces = turn_two_failed_bundle["visible_surfaces"]
    assert isinstance(turn_two_surfaces, dict)
    for surface_index, surface in enumerate(turn_two_contract["surfaces"], start=1):
        for instance_index in range(1, int(surface.get("count", 1)) + 1):
            fields = turn_two_surfaces[f"surface_{surface_index}_{instance_index}"]
            assert list(fields) == surface["required_fields"]
            for field, value in fields.items():
                assert not str(value).lstrip().startswith(str(field))
    turn_two_metadata = latest_turn_metadata(admin, party_id)
    assert turn_two_metadata["repaired"] is True
    assert turn_two_metadata["fallback"] is False
    assert turn_two_metadata["validator_valid"] is True
    repaired_answer = public.post(
        f"/api/showroom/runs/{run_id}/messages",
        json={
            "content": "Проверяю источник обращения и не передаю внутренние материалы внешнему адресату.",
            "idempotency_key": "training-flow-turn-2",
        },
    )
    assert repaired_answer.status_code == 200, repaired_answer.text
    assert repaired_answer.json()["choices"][0]["message"]["content"].startswith("Ход 3.")
    assert "\u2028" not in repaired_answer.json()["choices"][0]["message"]["content"]
    assert repaired_answer.json()["choices"][0]["message"]["artifacts"]
    assert provider_turns == [1, 2, 2, 3, 3]

    resumed = public.get(f"/api/showroom/runs/{run_id}")
    history = public.get(f"/api/showroom/runs/{run_id}/history")
    assert resumed.status_code == 200, resumed.text
    assert history.status_code == 200, history.text
    assert resumed.json()["run"]["party_status"] == "active"
    assert len(history.json()["turns"]) == 3
    state = state_store.get_state()
    assert state["meta"]["turn"] == 3
    assert state["player"]["resources"]["safe-escalations"] >= 1
    metadata = latest_turn_metadata(admin, party_id)
    assert metadata["fallback"] is False
    assert metadata["validator_valid"] is True
    assert metadata["repaired"] is True

    unauthorized_runs = public.get("/api/admin/showroom/runs")
    assert unauthorized_runs.status_code == 401, unauthorized_runs.text
    admin_runs = admin.get("/api/admin/showroom/runs")
    assert admin_runs.status_code == 200, admin_runs.text
    discovered = next(item for item in admin_runs.json()["runs"] if item["run_id"] == run_id)
    assert discovered["party_id"] == party_id
    assert discovered["dataset_review_status"] == "review"

    approved_party = admin.patch(
        f"/api/admin/datasets/parties/{party_id}",
        json={"review_status": "approved", "tags": ["showroom-training"]},
    )
    assert approved_party.status_code == 200, approved_party.text
    dataset_turns = admin.get(f"/api/admin/datasets/parties/{party_id}/turns")
    assert dataset_turns.status_code == 200, dataset_turns.text
    first_turn = dataset_turns.json()["turns"][0]
    approved_turn = admin.put(
        f"/api/admin/datasets/parties/{party_id}/turns/{first_turn['turn_id']}",
        json={"review_status": "approved", "tags": ["provider-flow"], "notes": "reviewed"},
    )
    assert approved_turn.status_code == 200, approved_turn.text
    exported = admin.get("/api/admin/datasets/export.jsonl?scenario_type=training&include_branches=false")
    assert exported.status_code == 200, exported.text
    assert exported.headers["X-Dataset-Approved-Turns"] == "1"
    exported_rows = [json.loads(line) for line in exported.text.splitlines() if line.strip()]
    assert len(exported_rows) == 1
    assert exported_rows[0]["metadata"]["party_id"] == party_id

    autotest_models = admin.get("/api/admin/autotests/models")
    assert autotest_models.status_code == 200, autotest_models.text
    assert autotest_models.json()["model_profiles"]
    autotest = admin.post(
        "/api/admin/autotests",
        json={
            "source_party_id": party_id,
            "player_prompt": "Проверяй каждое сообщение и эскалируй риск.",
            "turn_count": 1,
            "player_model_profile_id": autotest_models.json()["model_profiles"][0]["id"],
        },
    )
    assert autotest.status_code == 200, autotest.text
    assert autotest.json()["run"]["source_party_id"] == party_id
    assert autotest.json()["run"]["owner_user_id"] == login.json()["user"]["id"]
    assert autotest.json()["branch"]["owner_user_id"] == "__showroom__"
    autotest_id = autotest.json()["run"]["id"]
    autotest_run = autotest.json()["run"]
    for _ in range(100):
        listed = admin.get(f"/api/admin/autotests?source_party_id={party_id}")
        assert listed.status_code == 200, listed.text
        autotest_run = next(item for item in listed.json()["runs"] if item["id"] == autotest_id)
        if autotest_run["status"] in {"completed", "failed", "stopped"}:
            break
        time.sleep(0.01)
    assert autotest_run["status"] == "completed", autotest_run
    assert autotest_run["completed_turns"] == 1

    with admin.app.state.showroom_store.connect() as connection:
        connection.execute("DELETE FROM showroom_runs WHERE id = ?", (run_id,))
    orphan_export = admin.get("/api/admin/datasets/export.jsonl?scenario_type=training&include_branches=false")
    assert orphan_export.status_code == 200, orphan_export.text
    assert orphan_export.headers["X-Dataset-Approved-Turns"] == "0"
    assert orphan_export.text == ""

    paths = {route.path for route in admin.app.router.routes}
    assert not any(path == "/api/parties" or path.startswith("/api/parties/") for path in paths)
    assert "/api/showroom/runs/{run_id}/messages" in paths
