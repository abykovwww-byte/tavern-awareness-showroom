from __future__ import annotations

import copy
import json
import sqlite3
import shutil
from pathlib import Path

import pytest

from app.models.schemas import (
    PatchOperation,
    StatePatch,
    TrainingWorkspaceEventRequest,
    WorldPackSummary,
)
from app.services.state_store import StateStore, StateVersionConflict
from app.services.training_artifacts import TrainingArtifactService
from app.services.training_capabilities import TrainingCapabilityPolicy
from app.services.training_workspace import TrainingWorkspaceService


def awareness_pack() -> WorldPackSummary:
    root = Path(__file__).resolve().parents[2] / "worldpacks" / "awareness-one-day"
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return WorldPackSummary(
        id=manifest["id"],
        title=manifest["title"],
        slug=root.name,
        status="playable",
        manifest_path=str(manifest_path),
        state_seed_path=str(root / "state-seed.json"),
        manifest=manifest,
    )


def test_training_capabilities_are_declared_separately_from_activation(tmp_path: Path):
    pack = awareness_pack()
    support = TrainingCapabilityPolicy.support(pack)

    assert support == {
        "interactive_links_supported": True,
        "interactive_workspace_supported": True,
    }
    selected = TrainingCapabilityPolicy.validate(
        scenario_type="training",
        worldpack=pack,
        interactive_links_enabled=True,
        interactive_workspace_enabled=False,
    )
    assert selected["interactive_links_enabled"] is True
    assert selected["interactive_workspace_enabled"] is False

    with pytest.raises(ValueError, match="only for training"):
        TrainingCapabilityPolicy.validate(
            scenario_type="rp",
            worldpack=pack,
            interactive_links_enabled=True,
            interactive_workspace_enabled=False,
        )


def test_disabled_services_do_not_infer_activation_from_manifest(tmp_path: Path):
    pack = awareness_pack()
    store = StateStore(str(tmp_path / "state.db"), "party-disabled", pack.state_seed_path)

    assert TrainingArtifactService(pack, store, enabled=False).enabled is False
    assert TrainingWorkspaceService(pack, store, enabled=False).enabled is False


def test_anonymous_showroom_rejects_restricted_workspace_resources(tmp_path: Path):
    source = Path(__file__).resolve().parents[2] / "worldpacks" / "awareness-one-day"
    target = tmp_path / "worldpacks" / "restricted-training"
    shutil.copytree(source, target)
    blueprint_path = target / "artifacts" / "workspace" / "files" / "security-policy.json"
    blueprint = json.loads(blueprint_path.read_text(encoding="utf-8"))
    blueprint["resource_classification"] = "restricted_internal"
    blueprint_path.write_text(json.dumps(blueprint, ensure_ascii=False), encoding="utf-8")
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack = WorldPackSummary(
        id=manifest["id"],
        title=manifest["title"],
        slug=target.name,
        status="playable",
        manifest_path=str(manifest_path),
        state_seed_path=str(target / "state-seed.json"),
        manifest=manifest,
    )

    assert TrainingWorkspaceService.supports(pack) is True
    assert TrainingWorkspaceService.supports_anonymous_showroom(pack) is False
    with pytest.raises(ValueError, match="unavailable to anonymous Showroom"):
        TrainingCapabilityPolicy.validate(
            scenario_type="training",
            worldpack=pack,
            interactive_links_enabled=False,
            interactive_workspace_enabled=True,
        )


def test_workspace_materializes_files_and_records_scored_evidence(tmp_path: Path):
    pack = awareness_pack()
    state_path = tmp_path / "state.json"
    shutil.copy2(pack.state_seed_path, state_path)
    store = StateStore(str(tmp_path / "state.db"), "party-workspace", str(state_path))
    service = TrainingWorkspaceService(pack, store)
    state = store.get_state()
    state.setdefault("meta", {})["turn"] = 1
    contract = service.contract_for_state(state, party_start=True)

    assert contract is not None
    assert [item["file_key"] for item in contract["files"]] == ["security-policy"]
    response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "schema_version": "rp-gateway.narrative-bundle.v2",
                            "narrative_text": "Рабочий день начался.",
                            "artifacts": [],
                            "workspace_files": [
                                {
                                    "file_key": "security-policy",
                                    "blueprint_id": "security-policy",
                                    "slots": {"summary": "Краткая памятка по безопасной работе."},
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            }
        ]
    }
    materialized = service.materialize_response(response, contract)
    assert materialized.valid is True
    fenced_response = copy.deepcopy(response)
    bundle_text = fenced_response["choices"][0]["message"]["content"]
    fenced_response["choices"][0]["message"]["content"] = f"```json\n{bundle_text}\n```"
    assert service.materialize_response(fenced_response, contract).valid is True
    store.record_turn(
        "start",
        "request-start",
        "[AUTO_START]",
        materialized.text,
        response,
        store.current_version() or 1,
        workspace_files=materialized.persistence_records,
    )
    snapshot = service.snapshot(state)
    assert snapshot["files"][0]["file_key"] == "security-policy"
    assert snapshot["files"][0]["resource_sha256"]

    dynamic_state = store.get_state()
    dynamic_state.setdefault("meta", {})["turn"] = 4
    dynamic_contract = service.contract_for_state(dynamic_state)
    dynamic = service.fallback_materialization(dynamic_contract, "Ход 4")
    store.record_turn(
        "turn-4",
        "request-turn-4",
        "continue",
        dynamic.text,
        response,
        store.current_version() or 1,
        workspace_files=dynamic.persistence_records,
    )
    risky = next(item for item in service.snapshot(dynamic_state)["files"] if item["file_key"] == "turn-4-access-update")
    event = service.record_event(
        TrainingWorkspaceEventRequest(
            event_id="workspace-event-open-1",
            file_id=risky["file_id"],
            file_revision=risky["file_revision"],
            event_type="file_opened",
        ),
        dynamic_state,
    )
    assert event.duplicate is False
    evidence = service.pending_evidence()
    assert evidence[0].decision_result == "fail"
    assert evidence[0].score_rule_id == "workspace-access-update-open"

    new_file = copy.deepcopy(dynamic.persistence_records[0])
    new_file["public"]["file_id"] = "workspace_atomic_new"
    new_file["public"]["file_key"] = "atomic-workspace-copy"
    expected_version = int(store.current_version() or 0)
    with sqlite3.connect(store.sqlite_path) as connection:
        turn_before = int(connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
    patch = StatePatch(
        turn=int(store.get_state().get("meta", {}).get("turn", 0)) + 1,
        check_id="workspace-atomic-turn",
        patch=[
            PatchOperation(
                op="add",
                path="/meta/atomic-workspace-proof",
                value=True,
                reason="verify workspace effects share the turn transaction",
                turn=int(store.get_state().get("meta", {}).get("turn", 0)) + 1,
            )
        ],
    )
    with pytest.raises(StateVersionConflict, match="workspace events changed before turn commit"):
        store.commit_turn_bundle(
            patch,
            reason="turn:workspace-atomic-failure",
            expected_state_version=expected_version,
            idempotency_key="workspace-atomic-turn",
            request_id="workspace-atomic-request",
            player_message="Открываю рабочий файл.",
            narrative_response="Файл открыт.",
            response_json=response,
            workspace_files=[new_file],
            consumed_workspace_event_ids=[event.event_sequence, 999_999_999],
        )

    assert store.current_version() == expected_version
    assert [item.event_sequence for item in service.pending_evidence()] == [event.event_sequence]
    with sqlite3.connect(store.sqlite_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == turn_before
        assert connection.execute(
            "SELECT COUNT(*) FROM training_workspace_files WHERE id = 'workspace_atomic_new'"
        ).fetchone() == (0,)

    _, committed_turn_id = store.commit_turn_bundle(
        patch,
        reason="turn:workspace-atomic-retry",
        expected_state_version=expected_version,
        idempotency_key="workspace-atomic-turn",
        request_id="workspace-atomic-request",
        player_message="Открываю рабочий файл.",
        narrative_response="Файл открыт.",
        response_json=response,
        workspace_files=[new_file],
        consumed_workspace_event_ids=[event.event_sequence],
    )
    assert service.pending_evidence() == []
    with sqlite3.connect(store.sqlite_path) as connection:
        assert connection.execute(
            "SELECT turn_id FROM training_workspace_files WHERE id = 'workspace_atomic_new'"
        ).fetchone() == (committed_turn_id,)
        assert connection.execute(
            "SELECT consumed_turn_id FROM training_workspace_events WHERE id = ?",
            (event.event_sequence,),
        ).fetchone() == (committed_turn_id,)
