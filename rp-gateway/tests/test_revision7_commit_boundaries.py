from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.main import scene_contract_for_party
from app.models.schemas import ChatCompletionRequest, ChatMessage, PatchOperation, StatePatch
from app.services.adjudicator import Adjudicator, SceneContinuityError
from app.services.narrative import NarrativeClient, response_text
from app.services.state_store import StateStore, StateVersionConflict
from test_gateway import client, create_demo_party, write_worldpack
from test_rp_continuity_revision7 import set_worldpack_revision
from test_scene_state import (
    authoritative_counts,
    provider_response,
    revision_seven_adjudicator,
    scene_bundle,
    table_count,
)


def revision_seven_worldpack(tmp_path: Path) -> Path:
    pack_dir = write_worldpack(tmp_path, supported_modes=["rp"])
    set_worldpack_revision(pack_dir, 7)

    seed_path = pack_dir / "state-seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    seed["locations"] = {
        "court": {"name": "Court", "aliases": ["the court"]},
        "throne_room": {"name": "Throne room", "aliases": ["the throne room"]},
    }
    seed["factions"] = {
        "crown": {"name": "Crown", "aliases": ["the Crown"]},
        "realm": {"name": "Realm", "aliases": ["the Realm"]},
    }
    seed["characters"]["advisor"]["name"] = "Advisor"
    seed["characters"]["advisor"]["aliases"] = ["the Advisor"]
    seed["characters"]["king"]["name"] = "King"
    seed["characters"]["king"]["aliases"] = ["the King"]
    seed_path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")

    manifest_path = pack_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["relationships"] = {
        "schema_version": "rp-relationships.v2",
        "model": "relationships/model.json",
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    relationships_dir = pack_dir / "relationships"
    relationships_dir.mkdir()
    (relationships_dir / "model.json").write_text(
        json.dumps(
            {
                "schema_version": "rp-relationships.v2",
                "characters": {
                    "advisor": {"aliases": ["Advisor", "the Advisor"]},
                    "king": {"aliases": ["King", "the King"]},
                },
                "axes": {},
                "events": {},
                "character_weights": {},
                "roles": {},
                "wounds": {},
                "clocks": {},
                "trust_mapping": {"kind": "linear", "in": [-10, 10], "out": [-40, 40]},
                "plot": {"tell_required_every_turn": True, "discovery_chance_per_turn": 0.0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return pack_dir


def opening_bundle(text: str = "The Advisor waits in the court.") -> dict[str, Any]:
    return scene_bundle(
        text=text,
        claims={"location_id": "court", "present_character_ids": ["advisor"]},
    )


def test_scene_contract_declaration_is_ignored_before_revision_seven() -> None:
    malformed_world = SimpleNamespace(
        manifest={"rp_contract": {"stable_affiliations": ["invalid"]}}
    )
    legacy_party = SimpleNamespace(
        scenario_type="rp",
        rp_contract_revision=6,
        worldpack=malformed_world,
    )
    candidate_party = SimpleNamespace(
        scenario_type="rp",
        rp_contract_revision=7,
        worldpack=malformed_world,
    )

    assert scene_contract_for_party(legacy_party) is None
    with pytest.raises(ValueError, match="stable_affiliations"):
        scene_contract_for_party(candidate_party)


def test_internal_pre_provider_error_cannot_be_misclassified_as_safe_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "internal-pre-provider-error")
    provider_calls = 0

    def fail_relationship_pressure(*args: object, **kwargs: object) -> None:
        raise RuntimeError("internal relationship pressure failure")

    async def unexpected_provider(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal provider_calls
        provider_calls += 1
        return provider_response(scene_bundle())

    monkeypatch.setattr(adjudicator, "relationship_pressure", fail_relationship_pressure)
    monkeypatch.setattr(adjudicator.narrative, "complete", unexpected_provider)
    before = authoritative_counts(store)

    with pytest.raises(RuntimeError, match="internal relationship pressure failure"):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Я отвечаю Горазду.")],
                ),
                authorization=None,
                idempotency_key="internal-pre-provider-error",
                request_id="req-internal-pre-provider-error",
            )
        )

    assert provider_calls == 0
    assert authoritative_counts(store) == before
    saved_request = store.get_turn_request("req-internal-pre-provider-error")
    assert saved_request is not None
    assert saved_request["status"] == "failed"
    with store.connect() as connection:
        fallback_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE campaign_id = ? AND request_id = ? AND event_type = 'llm_safe_fallback'",
            (store.campaign_id, "req-internal-pre-provider-error"),
        ).fetchone()[0]
    assert fallback_count == 0


def test_transport_failure_during_hard_repair_never_commits_safe_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "repair-transport-error")
    invalid = provider_response(
        scene_bundle(
            claims={"location_id": "yard", "present_character_ids": ["milorad", "ratibor"]}
        )
    )
    calls = 0

    async def invalid_then_timeout(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return invalid
        raise httpx.TimeoutException("repair provider timeout")

    monkeypatch.setattr(adjudicator.narrative, "complete", invalid_then_timeout)
    before = authoritative_counts(store)

    with pytest.raises((httpx.TimeoutException, RuntimeError), match="repair provider timeout"):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Я отвечаю Горазду.")],
                ),
                authorization=None,
                idempotency_key="repair-transport-error",
                request_id="req-repair-transport-error",
            )
        )

    assert calls == 2
    assert authoritative_counts(store) == before
    saved_request = store.get_turn_request("req-repair-transport-error")
    assert saved_request is not None
    assert saved_request["status"] == "failed"
    with store.connect() as connection:
        fallback_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE campaign_id = ? AND request_id = ? AND event_type = 'llm_safe_fallback'",
            (store.campaign_id, "req-repair-transport-error"),
        ).fetchone()[0]
    assert fallback_count == 0


def test_state_change_after_initial_bundle_skips_repair_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "repair-state-conflict")
    calls = 0
    invalid = provider_response(
        scene_bundle(
            claims={"location_id": "yard", "present_character_ids": ["milorad", "ratibor"]}
        )
    )

    async def mutate_state_then_return_invalid(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise AssertionError("a stale base must not reach the repair provider")
        store.apply_state_patch(
            StatePatch(
                turn=15,
                check_id="concurrent-change-before-repair",
                patch=[
                    PatchOperation(
                        op="replace",
                        path="/player/status",
                        value="waiting",
                        reason="simulate a concurrent authoritative update",
                        turn=15,
                    )
                ],
            ),
            reason="test:concurrent-change-before-repair",
        )
        return invalid

    monkeypatch.setattr(adjudicator.narrative, "complete", mutate_state_then_return_invalid)
    before = authoritative_counts(store)

    with pytest.raises(StateVersionConflict):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Я отвечаю Горазду.")],
                ),
                authorization=None,
                idempotency_key="repair-state-conflict",
                request_id="req-repair-state-conflict",
            )
        )

    assert calls == 1
    assert table_count(store, "turns") == before["turns"]
    assert table_count(store, "state_versions") == before["state_versions"] + 1
    assert table_count(store, "state_patches") == before["state_patches"] + 1
    saved_request = store.get_turn_request("req-repair-state-conflict")
    assert saved_request is not None
    assert saved_request["status"] == "failed"


def test_invalid_narrative_after_valid_bundle_never_commits_safe_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "bundle-validation-error")
    calls = 0

    async def valid_bundle(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return provider_response(scene_bundle())

    def reject_narrative(*args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            valid=False,
            violations=["narrative continuity violation"],
            repair_instruction="Исправь нарушение непрерывности.",
        )

    monkeypatch.setattr(adjudicator.narrative, "complete", valid_bundle)
    monkeypatch.setattr(adjudicator.validator, "validate", reject_narrative)
    before = authoritative_counts(store)

    with pytest.raises(RuntimeError, match="failed narrative validation after bundle"):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Я отвечаю Горазду.")],
                ),
                authorization=None,
                idempotency_key="bundle-validation-error",
                request_id="req-bundle-validation-error",
            )
        )

    assert calls == 2
    assert authoritative_counts(store) == before
    assert store.get_state()["meta"]["turn"] == 14
    saved_request = store.get_turn_request("req-bundle-validation-error")
    assert saved_request is not None
    assert saved_request["status"] == "failed"
    with store.connect() as connection:
        fallback_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE campaign_id = ? AND request_id = ? AND event_type = 'llm_safe_fallback'",
            (store.campaign_id, "req-bundle-validation-error"),
        ).fetchone()[0]
    assert fallback_count == 0


@pytest.mark.parametrize("failure_point", ["turn_complete_audit", "post_turn_helper"])
def test_revision_seven_postcommit_failure_never_relabels_committed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, f"postcommit-{failure_point}")

    async def provider(*args: object, **kwargs: object) -> dict[str, Any]:
        return provider_response(scene_bundle())

    monkeypatch.setattr(adjudicator.narrative, "complete", provider)
    if failure_point == "turn_complete_audit":
        original_audit = store.audit

        def fail_turn_complete(
            event_type: str,
            event: dict[str, Any],
            request_id: str | None = None,
        ) -> None:
            if event_type == "turn_complete":
                raise RuntimeError("postcommit audit unavailable")
            original_audit(event_type, event, request_id)

        monkeypatch.setattr(store, "audit", fail_turn_complete)
    else:
        async def fail_post_turn_helper(*args: object, **kwargs: object) -> None:
            raise RuntimeError("postcommit helper unavailable")

        monkeypatch.setattr(adjudicator, "after_turn_recorded", fail_post_turn_helper)

    before = authoritative_counts(store)
    result = asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Я отвечаю Горазду.")],
            ),
            authorization=None,
            idempotency_key=f"postcommit-{failure_point}",
            request_id=f"req-postcommit-{failure_point}",
        )
    )

    assert response_text(result) == "Горазд остаётся во дворе."
    after = authoritative_counts(store)
    assert after == {
        "state_versions": before["state_versions"] + 1,
        "state_patches": before["state_patches"] + 1,
        "turns": before["turns"] + 1,
    }
    saved_request = store.get_turn_request(f"req-postcommit-{failure_point}")
    assert saved_request is not None
    assert saved_request["status"] == "completed"


@pytest.mark.parametrize("anchored", [True, False], ids=["applied", "dropped"])
def test_scene_adjudication_value_and_evidence_survive_trace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    anchored: bool,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, f"trace-failure-{anchored}")
    claims = (
        {"location_id": "market", "present_character_ids": ["milorad"]}
        if anchored
        else {"location_id": "yard", "present_character_ids": ["gorazd"]}
    )
    raw = provider_response(
        scene_bundle(
            text="Ты направляешься к рынку." if anchored else "Горазд остаётся во дворе.",
            claims=claims,
            delta=[
                {
                    "type": "move_player",
                    "location_id": "market",
                    "evidence": "Я иду на рынок",
                }
            ],
        )
    )

    async def provider(*args: object, **kwargs: object) -> dict[str, Any]:
        return raw

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    def fail_trace(*args: object, **kwargs: object) -> None:
        raise RuntimeError("trace storage unavailable")

    monkeypatch.setattr(adjudicator.narrative, "complete", provider)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    monkeypatch.setattr(store, "record_trace_event", fail_trace)

    result = asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Я иду на рынок.")],
            ),
            authorization=None,
            idempotency_key=f"trace-failure-{anchored}",
            request_id=f"req-trace-failure-{anchored}",
        )
    )

    assert response_text(result)
    with store.connect() as connection:
        row = connection.execute(
            "SELECT metadata_json FROM turns WHERE campaign_id = ?",
            (store.campaign_id,),
        ).fetchone()
    assert row is not None
    metadata = json.loads(row["metadata_json"])
    expected = {
        "type": "move_player",
        "location_id": "market",
        "evidence": "Я иду на рынок",
    }
    if anchored:
        assert metadata["applied_scene_delta"] == [expected]
        assert metadata["dropped_scene_delta"] == []
    else:
        assert metadata["applied_scene_delta"] == []
        assert len(metadata["dropped_scene_delta"]) == 1
        dropped = metadata["dropped_scene_delta"][0]
        assert {key: dropped[key] for key in expected} == expected
        assert isinstance(dropped["reason"], str) and dropped["reason"].startswith("unanchored")


def test_opening_uses_role_guard_repairs_once_and_commits_one_atomic_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision_seven_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=7, post_turn_helpers_inline=False)
    party = create_demo_party(api)
    calls = 0

    async def conflict_then_valid(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return provider_response(
                opening_bundle("The Advisor now belongs to the Realm and no longer serves the Crown.")
            )
        return provider_response(opening_bundle())

    monkeypatch.setattr(NarrativeClient, "complete", conflict_then_valid)

    started = api.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "opening-role-repair"},
        headers={"X-Request-ID": "req-opening-role-repair"},
    )

    assert started.status_code == 200, started.text
    assert calls == 2
    store = api.app.state.party_store.store_for_party(str(party["id"]))
    assert len(store.turn_history()) == 1
    current = store.get_state()
    assert current["meta"]["turn"] == 1
    assert current["scene_state"] == {
        "schema_version": "rp-gateway.scene-state.v1",
        "location_id": "court",
        "present_character_ids": ["advisor"],
        "stable_affiliations": {"advisor": "crown", "king": "realm"},
        "as_of_state_version": current["meta"]["state_version"],
        "as_of_party_turn": 1,
        "stale": False,
        "stale_reason": None,
    }
    with store.connect() as connection:
        row = connection.execute(
            "SELECT metadata_json FROM turns WHERE campaign_id = ?",
            (store.campaign_id,),
        ).fetchone()
    assert row is not None
    metadata = json.loads(row["metadata_json"])
    assert metadata["repaired"] is True
    assert metadata["scene_claims"] == {
        "location_id": "court",
        "present_character_ids": ["advisor"],
    }
    assert metadata["prompt_assembly"]["schema_version"] == "rp-gateway.prompt-assembly.v1"


def test_opening_postcommit_trace_failure_keeps_completed_atomic_opening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision_seven_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=7, post_turn_helpers_inline=False)
    party = create_demo_party(api)

    async def valid_opening(*args: object, **kwargs: object) -> dict[str, Any]:
        return provider_response(opening_bundle())

    original_record_trace = StateStore.record_trace_event

    def fail_commit_trace(self: StateStore, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("phase_key") == "turn_commit":
            raise RuntimeError("opening trace unavailable after commit")
        return original_record_trace(self, **kwargs)

    monkeypatch.setattr(NarrativeClient, "complete", valid_opening)
    monkeypatch.setattr(StateStore, "record_trace_event", fail_commit_trace)

    started = api.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "opening-postcommit-trace"},
        headers={"X-Request-ID": "req-opening-postcommit-trace"},
    )

    assert started.status_code == 200, started.text
    store = api.app.state.party_store.store_for_party(str(party["id"]))
    assert len(store.turn_history()) == 1
    assert store.get_state()["meta"]["turn"] == 1
    saved_request = store.get_turn_request("req-opening-postcommit-trace")
    assert saved_request is not None
    assert saved_request["status"] == "completed"


def test_second_opening_scene_mismatch_has_no_partial_opening_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    revision_seven_worldpack(tmp_path)
    api = client(tmp_path, rp_contract_observed_revision=7, post_turn_helpers_inline=False)
    party = create_demo_party(api)
    calls = 0
    invalid = provider_response(
        scene_bundle(
            text="The King and Advisor appear together.",
            claims={"location_id": "court", "present_character_ids": ["advisor", "king"]},
        )
    )

    async def repeated_mismatch(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return invalid

    monkeypatch.setattr(NarrativeClient, "complete", repeated_mismatch)
    store = api.app.state.party_store.store_for_party(str(party["id"]))
    before = authoritative_counts(store)
    before_state = store.get_state()

    started = api.post(
        f"/api/parties/{party['id']}/start",
        json={"idempotency_key": "opening-hard-mismatch"},
        headers={"X-Request-ID": "req-opening-hard-mismatch"},
    )

    assert started.status_code == 502, started.text
    assert calls == 2
    assert authoritative_counts(store) == before
    assert store.get_state() == before_state
    assert store.turn_history() == []
    saved_request = store.get_turn_request("req-opening-hard-mismatch")
    assert saved_request is not None
    assert saved_request["status"] == "failed"


def test_rollback_checkpoint_fork_preserves_excluded_from_memory_flag(tmp_path: Path) -> None:
    sqlite_path = tmp_path / "rollback-fork.db"
    source = StateStore(str(sqlite_path), "rollback-source", str(tmp_path / "source.json"))

    for party_turn in (1, 2):
        idempotency_key = f"rollback-turn-{party_turn}"
        request_id = f"req-rollback-turn-{party_turn}"
        source.begin_turn_request(idempotency_key, request_id)
        source.commit_turn(
            StatePatch(
                turn=party_turn,
                check_id=f"rollback-check-{party_turn}",
                patch=[
                    PatchOperation(
                        op="add",
                        path="/timeline/-",
                        value={"turn": party_turn, "event": f"event {party_turn}", "confirmed": True},
                        reason="prepare rollback fork exclusion",
                        turn=party_turn,
                    )
                ],
            ),
            reason=f"test:rollback-turn-{party_turn}",
            idempotency_key=idempotency_key,
            request_id=request_id,
            player_message=f"player {party_turn}",
            narrative_response=f"narrator {party_turn}",
            response_json={"choices": []},
        )

    source.rollback(target_version=2)
    checkpoint = source.create_memory_checkpoint("after rollback")
    source.fork_from_checkpoint(
        checkpoint_id=checkpoint["id"],
        target_campaign_id="rollback-branch",
        target_state_path=str(tmp_path / "branch.json"),
    )
    branch = StateStore(str(sqlite_path), "rollback-branch", str(tmp_path / "branch.json"))

    with source.connect() as connection:
        source_flags = [
            int(row["excluded_from_memory"])
            for row in connection.execute(
                "SELECT excluded_from_memory FROM turns WHERE campaign_id = ? ORDER BY id",
                (source.campaign_id,),
            ).fetchall()
        ]
        branch_flags = [
            int(row["excluded_from_memory"])
            for row in connection.execute(
                "SELECT excluded_from_memory FROM turns WHERE campaign_id = ? ORDER BY id",
                (branch.campaign_id,),
            ).fetchall()
        ]
    assert source_flags == [0, 1]
    assert branch_flags == [0, 1]
    assert [turn["player_message"] for turn in branch.turns_for_memory()] == ["player 1"]


def test_concurrent_revision_seven_turns_cannot_commit_stale_scene_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, store = revision_seven_adjudicator(tmp_path, "concurrent-scene")
    second = Adjudicator(first.settings, store, relationship_model=first.relationship_model)
    provider_calls = 0
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    release_provider = asyncio.Event()

    async def synchronized_provider(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal provider_calls
        provider_calls += 1
        if provider_calls == 1:
            first_entered.set()
        else:
            second_entered.set()
        await release_provider.wait()
        return provider_response(scene_bundle())

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    for adjudicator in (first, second):
        monkeypatch.setattr(adjudicator.narrative, "complete", synchronized_provider)
        monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)

    before = authoritative_counts(store)

    async def exercise_collision() -> list[object]:
        first_task = asyncio.create_task(
            first.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Первое действие.")],
                ),
                authorization=None,
                idempotency_key="concurrent-turn-one",
                request_id="req-concurrent-turn-one",
            )
        )
        await first_entered.wait()
        second_task = asyncio.create_task(
            second.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Второе действие.")],
                ),
                authorization=None,
                idempotency_key="concurrent-turn-two",
                request_id="req-concurrent-turn-two",
            )
        )
        await asyncio.wait_for(second_entered.wait(), timeout=10)
        release_provider.set()
        return list(await asyncio.gather(first_task, second_task, return_exceptions=True))

    results = asyncio.run(exercise_collision())
    successes = [item for item in results if isinstance(item, dict)]
    failures = [item for item in results if isinstance(item, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], StateVersionConflict)
    assert {
        table: table_count(store, table) - before[table]
        for table in ("state_versions", "state_patches", "turns")
    } == {"state_versions": 1, "state_patches": 1, "turns": 1}
    current = store.get_state()
    assert current["meta"]["turn"] == 15
    assert current["scene_state"]["as_of_party_turn"] == 15
