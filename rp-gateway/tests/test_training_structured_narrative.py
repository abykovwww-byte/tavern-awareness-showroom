from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from app.core.config import Settings
from app.models.schemas import ChatCompletionRequest, ChatMessage, Outcome, WorldPackSummary
from app.services.adjudicator import Adjudicator
from app.services.narrative import (
    NarrativeClient,
    materialize_structured_training_response,
    response_text,
    training_interaction_response_format,
)
from app.services.state_store import StateStore
from app.services.training_artifacts import TrainingArtifactService
from app.services.training_runtime import TrainingRuntimeService
from app.services.training_workspace import TrainingWorkspaceService


WORLD_PACKS_ROOT = Path(__file__).resolve().parents[2] / "worldpacks"


def training_services(tmp_path: Path, turn: int = 1):
    root = WORLD_PACKS_ROOT / "awareness"
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack = WorldPackSummary(
        id=manifest["id"],
        title=manifest["title"],
        slug=root.name,
        status="playable",
        manifest_path=str(manifest_path),
        state_seed_path=str(root / "state-seed.json"),
        manifest=manifest,
    )
    store = StateStore(str(tmp_path / "state.db"), "party-structured", pack.state_seed_path)
    state = store.get_state()
    state["meta"]["turn"] = turn
    state["player"]["name"] = "Инженер"
    state["player"]["description"] = "Инженер по тестированию рабочих приложений"
    return (
        state,
        TrainingRuntimeService(pack, store),
        TrainingArtifactService(pack, store),
        TrainingWorkspaceService(pack, store),
    )


def parse_surface_fields(block: str, field_names: list[str]) -> dict[str, str]:
    lines = block.strip().splitlines()[1:]
    positions: list[int] = []
    search_from = 0
    for field_name in field_names:
        position = next(
            index
            for index in range(search_from, len(lines))
            if lines[index].startswith(field_name)
        )
        positions.append(position)
        search_from = position + 1
    values: dict[str, str] = {}
    for index, field_name in enumerate(field_names):
        start = positions[index]
        end = positions[index + 1] if index + 1 < len(positions) else len(lines)
        inline = lines[start][len(field_name) :].strip()
        value_lines = ([inline] if inline else []) + lines[start + 1 : end]
        values[field_name] = "\n".join(value_lines).strip()
    return values


def structured_response(
    runtime: TrainingRuntimeService,
    state: dict,
    turn_contract: dict,
    interaction_contract: dict | None,
    artifact_service: TrainingArtifactService,
    workspace_service: TrainingWorkspaceService,
) -> dict:
    visible = runtime.fallback_text(state, interaction_contract)
    header = str(turn_contract["header"]).strip()
    question = str(turn_contract.get("question") or "").strip()
    visible = visible.removeprefix(header).lstrip()
    if question and visible.endswith(question):
        visible = visible[: -len(question)].rstrip()
    first_marker = re.search(r"(?m)^(ПИСЬМО|СООБЩЕНИЕ)\s*$", visible)
    assert first_marker is not None
    lead = visible[: first_marker.start()].strip()
    blocks = {
        "ПИСЬМО": iter(runtime.structured_surface_blocks(visible, "ПИСЬМО")),
        "СООБЩЕНИЕ": iter(runtime.structured_surface_blocks(visible, "СООБЩЕНИЕ")),
    }
    surfaces: dict[str, dict[str, str]] = {}
    for surface_index, surface in enumerate(turn_contract["surfaces"], start=1):
        marker = "ПИСЬМО" if surface["type"] == "email" else "СООБЩЕНИЕ"
        field_names = [str(field) for field in surface["required_fields"]]
        for instance_index in range(1, int(surface.get("count", 1)) + 1):
            parsed = parse_surface_fields(next(blocks[marker]), field_names)
            surfaces[f"surface_{surface_index}_{instance_index}"] = dict(
                reversed(list(parsed.items()))
            )

    site = (interaction_contract or {}).get("site")
    workspace = (interaction_contract or {}).get("workspace")
    artifacts = []
    if site:
        assert artifact_service.catalog is not None
        fallback_slots = artifact_service.catalog["blueprints"][site["blueprint_id"]]["fallback_content"]
        artifacts.append(
            {
                "artifact_key": site["artifact_key"],
                "blueprint_id": site["blueprint_id"],
                "slots": fallback_slots,
            }
        )
    workspace_files = []
    if workspace:
        assert workspace_service.catalog is not None
        for file_contract in workspace["files"]:
            fallback_slots = workspace_service.catalog["blueprints"][file_contract["blueprint_id"]][
                "fallback_content"
            ]
            workspace_files.append(
                {
                    "file_key": file_contract["file_key"],
                    "blueprint_id": file_contract["blueprint_id"],
                    "slots": fallback_slots,
                }
            )
    bundle = {
        "schema_version": "rp-gateway.narrative-bundle.v3",
        "narrative_text": lead,
        "visible_surfaces": surfaces,
        "artifacts": artifacts,
        "workspace_files": workspace_files,
    }
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(bundle, ensure_ascii=False)}}]}


def test_weekly_turn_schema_owns_exact_surface_shape_and_links(tmp_path: Path):
    state, runtime, artifact_service, workspace_service = training_services(tmp_path)
    site = artifact_service.contract_for_state(state)
    workspace = workspace_service.contract_for_state(state, party_start=True)
    interaction = {"site": site, "workspace": workspace}
    turn_contract = runtime.prompt_contract(state, interaction)
    assert turn_contract is not None

    response_format = training_interaction_response_format(interaction, turn_contract)
    schema = response_format["json_schema"]["schema"]
    assert schema["properties"]["schema_version"]["enum"] == [
        "rp-gateway.narrative-bundle.v3"
    ]
    visible = schema["properties"]["visible_surfaces"]
    assert visible["required"] == ["surface_1_1", "surface_1_2", "surface_2_1"]
    assert visible["additionalProperties"] is False
    email_fields = [str(field) for field in turn_contract["surfaces"][0]["required_fields"]]
    assert visible["properties"]["surface_1_1"]["required"] == email_fields
    assert visible["properties"]["surface_1_1"]["properties"]["Ссылки:"]["enum"] == [
        site["display_url"]
    ]
    assert visible["properties"]["surface_1_2"]["properties"]["Ссылки:"]["enum"] == ["нет"]
    assert visible["properties"]["surface_2_1"]["properties"]["Ссылки:"]["enum"] == ["нет"]
    assert visible["properties"]["surface_1_1"]["properties"]["Тело:"]["type"] == "string"
    assert visible["properties"]["surface_1_1"]["properties"]["От:"]["type"] == "string"
    assert schema["properties"]["artifacts"]["minItems"] == 1
    assert schema["properties"]["workspace_files"]["minItems"] == 1


def test_structured_bundle_renders_plain_public_text_and_legacy_interactions(tmp_path: Path):
    state, runtime, artifact_service, workspace_service = training_services(tmp_path)
    site = artifact_service.contract_for_state(state)
    workspace = workspace_service.contract_for_state(state, party_start=True)
    interaction = {"site": site, "workspace": workspace}
    turn_contract = runtime.prompt_contract(state, interaction)
    assert turn_contract is not None
    provider_response = structured_response(
        runtime,
        state,
        turn_contract,
        interaction,
        artifact_service,
        workspace_service,
    )

    structured = materialize_structured_training_response(
        provider_response,
        turn_contract,
        interaction,
    )

    assert structured.valid is True, structured.violations
    assert structured.text.count("\nПИСЬМО\n") == 2
    assert structured.text.count("\nСООБЩЕНИЕ\n") == 1
    assert structured.text.count(site["display_url"]) == 1
    first_email = structured.text.split("\nПИСЬМО\n", 1)[1]
    assert first_email.index("Канал:") < first_email.index("От:") < first_email.index("Кому:")
    bridged = json.loads(response_text(structured.response))
    assert bridged["schema_version"] == "rp-gateway.narrative-bundle.v2"
    assert "visible_surfaces" not in bridged

    artifact_result = artifact_service.materialize_response(structured.response, site)
    workspace_result = workspace_service.materialize_response(structured.response, workspace)
    assert artifact_result.valid is True, artifact_result.violations
    assert workspace_result.valid is True, workspace_result.violations
    normalized = runtime.normalize_narrative(structured.text, state, interaction)
    assert runtime.validate_narrative(normalized, state, interaction) == []
    public = Adjudicator.merge_interaction_response(
        structured.response,
        normalized,
        artifact_result,
        workspace_result,
    )
    assert response_text(public) == normalized
    assert not response_text(public).lstrip().startswith("{")
    assert public["choices"][0]["message"]["artifacts"]
    assert public["choices"][0]["message"]["workspace_files"]


@pytest.mark.parametrize(
    "defect",
    [
        "missing_surface",
        "wrong_link",
        "lead_url",
        "legacy_version",
        "unicode_field_injection",
        "missing_narrative_text",
        "missing_artifacts",
        "missing_workspace_files",
    ],
)
def test_structured_bundle_rejects_shape_or_gateway_owned_link(tmp_path: Path, defect: str):
    state, runtime, artifact_service, workspace_service = training_services(tmp_path)
    site = artifact_service.contract_for_state(state)
    workspace = workspace_service.contract_for_state(state, party_start=True)
    interaction = {"site": site, "workspace": workspace}
    turn_contract = runtime.prompt_contract(state, interaction)
    assert turn_contract is not None
    response = structured_response(
        runtime,
        state,
        turn_contract,
        interaction,
        artifact_service,
        workspace_service,
    )
    bundle = json.loads(response_text(response))
    if defect == "missing_surface":
        bundle["visible_surfaces"].pop("surface_1_2")
    elif defect == "wrong_link":
        bundle["visible_surfaces"]["surface_1_1"]["Ссылки:"] = "https://attacker.invalid/"
    elif defect == "lead_url":
        bundle["narrative_text"] = "Откройте https://attacker.invalid/ до начала рабочего блока."
    elif defect == "legacy_version":
        bundle["schema_version"] = "rp-gateway.narrative-bundle.v2"
    elif defect == "unicode_field_injection":
        bundle["visible_surfaces"]["surface_1_1"]["Тело:"] += "\u2028Ссылки: нет"
    else:
        bundle.pop(defect.removeprefix("missing_"))
    response["choices"][0]["message"]["content"] = json.dumps(bundle, ensure_ascii=False)

    result = materialize_structured_training_response(response, turn_contract, interaction)

    assert result.valid is False
    assert result.violations
    assert result.response == response


def test_primary_and_repair_send_same_structured_schema_but_debrief_stays_plain(tmp_path: Path):
    state, runtime, _, _ = training_services(tmp_path)
    turn_contract = runtime.prompt_contract(state)
    assert turn_contract is not None and turn_contract["kind"] == "turn"
    traces: list[dict] = []
    settings = Settings(
        app_env="test",
        campaign_id="awareness",
        scenario_type="training",
        llm_provider="openrouter",
        llm_api_base="mock://success",
        llm_api_key="test-key",
        narrative_model="deepseek/deepseek-v4-flash",
    )
    client = NarrativeClient(settings, trace_recorder=traces.append)
    request = ChatCompletionRequest(
        model="deepseek/deepseek-v4-flash",
        messages=[ChatMessage(role="user", content="Продолжаю рабочий день")],
    )
    outcome = Outcome(
        check_id="structured-schema",
        action_type="narrative",
        actor="player",
        result="deterministic_resolution",
        roll=0,
        difficulty=0,
        modifiers={},
        final_score=0,
        authoritative_block="AUTHORITATIVE_OUTCOME",
    )

    primary_response = asyncio.run(
        client.complete(
            request,
            state,
            outcome,
            None,
            request_id="structured-primary",
            training_turn_contract=turn_contract,
        )
    )
    asyncio.run(
        client.complete(
            request,
            state,
            outcome,
            None,
            "Исправь структуру",
            failed_response_text="bad",
            request_id="structured-repair",
            training_turn_contract=turn_contract,
        )
    )
    primary_payload = traces[0]["input"]["payload"]
    repair_payload = traces[1]["input"]["payload"]
    assert primary_payload["response_format"] == repair_payload["response_format"]
    assert primary_payload["provider"]["require_parameters"] is True
    assert repair_payload["provider"]["require_parameters"] is True
    assert "visible_surfaces" in primary_payload["response_format"]["json_schema"]["schema"]["properties"]
    assert json.loads(response_text(primary_response))["schema_version"] == "rp-gateway.narrative-bundle.v3"

    debrief_state = dict(state)
    debrief_state["meta"] = dict(state["meta"])
    debrief_state["meta"]["turn"] = 11
    debrief_contract = runtime.prompt_contract(debrief_state)
    assert debrief_contract is not None and debrief_contract["kind"] == "debrief"
    asyncio.run(
        client.complete(
            request,
            debrief_state,
            outcome,
            None,
            request_id="structured-debrief",
            training_turn_contract=debrief_contract,
        )
    )
    debrief_payload = traces[2]["input"]["payload"]
    assert "response_format" not in debrief_payload
    assert debrief_payload.get("provider", {}).get("require_parameters") is not True
