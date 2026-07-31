from __future__ import annotations

import json
import sqlite3
import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.schemas import Intent, TrainingArtifactEventRequest, WorldPackSummary
from app.services.rule_engine import AWARENESS_ONE_DAY_ID, RuleEngine
from app.services.state_store import StateStore
from app.services.training_artifacts import TrainingArtifactService


WORLD_ROOT = Path(__file__).resolve().parents[2] / "worldpacks" / AWARENESS_ONE_DAY_ID


def artifact_service(tmp_path: Path) -> tuple[StateStore, TrainingArtifactService, dict]:
    manifest_path = WORLD_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state = json.loads((WORLD_ROOT / "state-seed.json").read_text(encoding="utf-8"))
    state["meta"]["turn"] = 4
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    store = StateStore(str(tmp_path / "state.db"), "party-training-test", str(state_path))
    worldpack = WorldPackSummary(
        id=AWARENESS_ONE_DAY_ID,
        title=str(manifest["title"]),
        slug=AWARENESS_ONE_DAY_ID,
        status="playable",
        manifest_path=str(manifest_path),
        state_seed_path=str(WORLD_ROOT / "state-seed.json"),
        manifest=manifest,
    )
    return store, TrainingArtifactService(worldpack, store), state


def response_with_bundle(contract: dict, slots: dict[str, str] | None = None) -> dict:
    filled_slots = slots or {slot_id: f"Учебный текст {slot_id}" for slot_id in contract["slots"]}
    bundle = {
        "schema_version": "rp-gateway.narrative-bundle.v1",
        "narrative_text": f"Пришло письмо со ссылкой {contract['display_url']}",
        "artifacts": [
            {
                "artifact_key": contract["artifact_key"],
                "blueprint_id": contract["blueprint_id"],
                "slots": filled_slots,
            }
        ],
    }
    return {"choices": [{"message": {"role": "assistant", "content": json.dumps(bundle, ensure_ascii=False)}}]}


def patch_values(patch) -> dict:
    return {operation.path: operation.value for operation in patch.patch}


def test_materializes_only_allowlisted_public_artifact(tmp_path: Path):
    store, service, state = artifact_service(tmp_path)
    contract = service.contract_for_state(state)

    result = service.materialize_response(response_with_bundle(contract), contract)

    assert result.valid, result.violations
    assert result.text == f"Пришло письмо со ссылкой {contract['display_url']}"
    assert len(result.public_artifacts) == 1
    assert result.public_artifacts[0]["renderer"] == "credential-form"
    assert result.public_artifacts[0]["field_ids"] == ["login", "password"]
    assert "policy" not in result.public_artifacts[0]
    assert "credential_field_ids" not in json.dumps(result.response)


def test_rejects_markup_or_wrong_artifact_contract(tmp_path: Path):
    _, service, state = artifact_service(tmp_path)
    contract = service.contract_for_state(state)
    response = response_with_bundle(contract)
    bundle = json.loads(response["choices"][0]["message"]["content"])
    bundle["artifacts"][0]["blueprint_id"] = "password-reset"
    bundle["artifacts"][0]["slots"]["page_title"] = "<script>alert(1)</script>"
    response["choices"][0]["message"]["content"] = json.dumps(bundle, ensure_ascii=False)

    result = service.materialize_response(response, contract)

    assert not result.valid
    assert any("blueprint_id" in item for item in result.violations)
    assert any("markup" in item for item in result.violations)


def test_event_is_idempotent_private_and_consumed_by_scoring(tmp_path: Path):
    store, service, state = artifact_service(tmp_path)
    contract = service.contract_for_state(state)
    materialized = service.materialize_response(response_with_bundle(contract), contract)
    turn_id = store.record_turn(
        "artifact-turn-4",
        "request-turn-4",
        "Открываю письмо.",
        materialized.text,
        materialized.response,
        int(store.current_version() or 1),
        artifacts=materialized.persistence_records,
    )
    artifact = materialized.public_artifacts[0]
    link_request = TrainingArtifactEventRequest(
        event_id="evt-link-0001",
        artifact_id=artifact["artifact_id"],
        artifact_revision=artifact["artifact_revision"],
        event_type="link_opened",
    )
    form_request = TrainingArtifactEventRequest(
        event_id="evt-form-0001",
        artifact_id=artifact["artifact_id"],
        artifact_revision=artifact["artifact_revision"],
        event_type="form_submitted",
        filled_field_ids=["login", "password"],
    )

    link_result = service.record_event(link_request)
    assert service.record_event(link_request).duplicate is True
    form_result = service.record_event(form_request)
    evidence = service.pending_evidence()

    assert link_result.duplicate is False
    assert form_result.duplicate is False
    assert [item.event_type for item in evidence] == ["link_opened", "credentials_submitted"]
    with sqlite3.connect(store.sqlite_path) as connection:
        raw_rows = connection.execute(
            "SELECT filled_field_ids_json FROM training_artifact_events ORDER BY id"
        ).fetchall()
    assert raw_rows == [("[]",), ('["login", "password"]',)]

    _, score_patch = RuleEngine().resolve(
        state,
        Intent(desired_outcome="Продолжаю рабочий день."),
        "score-ui-events",
        campaign_id=AWARENESS_ONE_DAY_ID,
        scenario_type="training",
        interaction_evidence=evidence,
    )
    values = patch_values(score_patch)
    assert values["/player/resources/links-opened"] == 1
    assert values["/player/resources/unsafe-actions"] == 1
    assert values["/player/resources/suspicious-artifacts-opened"] == 1
    assert values["/player/resources/credential-exposure"] == 1

    store.record_turn(
        "artifact-turn-5",
        "request-turn-5",
        "Продолжаю рабочий день.",
        "Следующий эпизод.",
        {"choices": [{"message": {"role": "assistant", "content": "Следующий эпизод."}}]},
        int(store.current_version() or 1),
        consumed_artifact_event_ids=[item.event_sequence for item in evidence],
    )
    assert service.pending_evidence() == []
    statuses = store.training_artifact_event_status_for_turn(turn_id)
    assert all(item["consumed"] for item in statuses)


def test_event_schema_forbids_transmitting_field_values():
    with pytest.raises(ValidationError):
        TrainingArtifactEventRequest.model_validate(
            {
                "event_id": "evt-form-0002",
                "artifact_id": "artifact_test",
                "artifact_revision": 1,
                "event_type": "form_submitted",
                "filled_field_ids": ["password"],
                "password": "must-never-cross-the-wire",
            }
        )


def test_worldpack_validator_rejects_public_policy_leak_and_incomplete_event_policy():
    blueprint = json.loads((WORLD_ROOT / "artifacts" / "sites" / "corporate-sso.json").read_text(encoding="utf-8"))
    leaked = copy.deepcopy(blueprint)
    leaked["score_rule_id"] = "must-stay-server-side"
    with pytest.raises(ValueError, match="policy leaked"):
        TrainingArtifactService._validate_blueprint(leaked)

    with pytest.raises(ValueError, match="exactly cover"):
        TrainingArtifactService._validate_policy(
            {"corporate-sso": {}},
            {
                "corporate-sso": blueprint,
                "password-reset": json.loads(
                    (WORLD_ROOT / "artifacts" / "sites" / "password-reset.json").read_text(encoding="utf-8")
                ),
            },
            {4: {"blueprint_id": "corporate-sso"}},
        )
