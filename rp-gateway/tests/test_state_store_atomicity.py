from __future__ import annotations

import asyncio
import json
import sqlite3
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.models.schemas import ChatCompletionRequest, ChatMessage, PatchOperation, StatePatch
from app.services.scene_state import initial_scene_state
from app.services.state_store import StateStore
from test_scene_state import (
    authoritative_counts,
    provider_response,
    revision_seven_adjudicator,
    scene_bundle,
    scene_state,
)


def atomic_store(tmp_path: Path, campaign_id: str = "atomic-commit") -> StateStore:
    store = StateStore(
        str(tmp_path / "atomic.db"),
        campaign_id,
        str(tmp_path / f"{campaign_id}.json"),
    )
    store.begin_turn_request("atomic-turn", "req-atomic-turn")
    return store


def atomic_patch(store: StateStore) -> StatePatch:
    party_turn = int(store.get_state().get("meta", {}).get("turn", 0)) + 1
    return StatePatch(
        turn=party_turn,
        check_id="atomic-check",
        patch=[
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={"turn": party_turn, "event": "atomic event", "confirmed": True},
                reason="exercise atomic turn persistence",
                turn=party_turn,
            )
        ],
    )


def commit_atomic_turn(store: StateStore) -> tuple[dict[str, Any], int]:
    return store.commit_turn(
        atomic_patch(store),
        reason="test:atomic-turn",
        idempotency_key="atomic-turn",
        request_id="req-atomic-turn",
        player_message="Continue.",
        narrative_response="Narration.",
        response_json={"choices": [{"message": {"role": "assistant", "content": "Narration."}}]},
        metadata={"private": {"evidence": "bounded evidence"}},
    )


@pytest.mark.parametrize(
    ("trigger_name", "trigger_sql", "expected_error"),
    [
        pytest.param(
            "fail_state_version_insert",
            """
            CREATE TRIGGER fail_state_version_insert
            BEFORE INSERT ON state_versions
            BEGIN
                SELECT RAISE(FAIL, 'forced state version failure');
            END
            """,
            "forced state version failure",
            id="state-version-write",
        ),
        pytest.param(
            "fail_turn_private_metadata_insert",
            """
            CREATE TRIGGER fail_turn_private_metadata_insert
            BEFORE INSERT ON turns
            BEGIN
                SELECT RAISE(FAIL, 'forced turn metadata failure');
            END
            """,
            "forced turn metadata failure",
            id="turn-private-metadata-write",
        ),
        pytest.param(
            "fail_request_completion",
            """
            CREATE TRIGGER fail_request_completion
            BEFORE UPDATE OF status ON turn_requests
            WHEN NEW.status = 'completed'
            BEGIN
                SELECT RAISE(FAIL, 'forced request completion failure');
            END
            """,
            "forced request completion failure",
            id="request-completion-write",
        ),
    ],
)
def test_authoritative_write_failure_rolls_back_state_turn_and_request_completion(
    tmp_path: Path,
    trigger_name: str,
    trigger_sql: str,
    expected_error: str,
) -> None:
    store = atomic_store(tmp_path, trigger_name)
    before_state = store.get_state()
    before_counts = authoritative_counts(store)
    with store.connect() as connection:
        connection.executescript(trigger_sql)

    with pytest.raises(sqlite3.IntegrityError, match=expected_error):
        commit_atomic_turn(store)

    assert authoritative_counts(store) == before_counts
    assert store.get_state() == before_state
    request = store.get_turn_request("req-atomic-turn")
    assert request is not None
    assert request["status"] == "running"
    assert request["response"] is None


def test_deferred_constraint_commit_failure_rolls_back_every_authoritative_row(
    tmp_path: Path,
) -> None:
    store = atomic_store(tmp_path, "transaction-commit-failure")
    before_state = store.get_state()
    before_counts = authoritative_counts(store)
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE forced_commit_parent (
                id INTEGER PRIMARY KEY
            );
            CREATE TABLE forced_commit_child (
                id INTEGER PRIMARY KEY,
                missing_parent_id INTEGER NOT NULL,
                FOREIGN KEY(missing_parent_id) REFERENCES forced_commit_parent(id)
                    DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_only_at_transaction_commit
            AFTER INSERT ON turns
            BEGIN
                INSERT INTO forced_commit_child(id, missing_parent_id) VALUES (NEW.id, -1);
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        commit_atomic_turn(store)

    assert authoritative_counts(store) == before_counts
    assert store.get_state() == before_state
    request = store.get_turn_request("req-atomic-turn")
    assert request is not None
    assert request["status"] == "running"


def test_scene_drop_audit_write_is_in_same_transaction_as_state_and_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "atomic-scene-audit")
    raw = provider_response(
        scene_bundle(
            delta=[
                {
                    "type": "move_player",
                    "location_id": "market",
                    "evidence": "Я иду на рынок",
                }
            ]
        )
    )

    async def provider(*args: object, **kwargs: object) -> dict[str, Any]:
        return raw

    monkeypatch.setattr(adjudicator.narrative, "complete", provider)
    before = authoritative_counts(store)
    before_state = store.get_state()
    with store.connect() as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_scene_drop_audit
            BEFORE INSERT ON audit_events
            WHEN NEW.event_type = 'scene_delta_operations_dropped'
            BEGIN
                SELECT RAISE(FAIL, 'forced scene audit failure');
            END;
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced scene audit failure"):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Я иду на рынок.")],
                ),
                authorization=None,
                idempotency_key="atomic-scene-audit",
                request_id="req-atomic-scene-audit",
            )
        )

    assert authoritative_counts(store) == before
    assert store.get_state() == before_state
    saved_request = store.get_turn_request("req-atomic-scene-audit")
    assert saved_request is not None
    assert saved_request["status"] == "failed"


def test_postcommit_current_json_failure_keeps_one_sqlite_turn_and_recovers_mirror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = atomic_store(tmp_path, "postcommit-mirror-failure")
    state_path = Path(store.state_path)
    before_counts = authoritative_counts(store)
    original_write_state_file = store.write_state_file
    writes = 0

    def fail_once(state: dict[str, Any]) -> None:
        nonlocal writes
        writes += 1
        raise OSError("forced current.json replace failure")

    monkeypatch.setattr(store, "write_state_file", fail_once)

    committed_state, committed_turn_id = commit_atomic_turn(store)

    assert writes == 1
    assert committed_turn_id > 0
    assert {
        table: authoritative_counts(store)[table] - before_counts[table]
        for table in ("state_versions", "state_patches", "turns")
    } == {"state_versions": 1, "state_patches": 1, "turns": 1}
    assert store.get_turn_request("req-atomic-turn")["status"] == "completed"
    with store.connect() as connection:
        failures = connection.execute(
            "SELECT event_json FROM audit_events "
            "WHERE campaign_id = ? AND event_type = 'state_mirror_write_failed'",
            (store.campaign_id,),
        ).fetchall()
    assert len(failures) == 1

    monkeypatch.setattr(store, "write_state_file", original_write_state_file)
    reopened = StateStore(store.sqlite_path, store.campaign_id, str(state_path))
    assert reopened.get_state() == committed_state
    assert json.loads(state_path.read_text(encoding="utf-8")) == committed_state
    assert authoritative_counts(reopened) == authoritative_counts(store)


def test_opening_store_with_healthy_mirror_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = atomic_store(tmp_path, "healthy-mirror-read")
    writes = 0
    original_write_state_file = StateStore.write_state_file

    def track_write(self: StateStore, state: dict[str, Any]) -> None:
        nonlocal writes
        writes += 1
        original_write_state_file(self, state)

    monkeypatch.setattr(StateStore, "write_state_file", track_write)
    reopened = StateStore(store.sqlite_path, store.campaign_id, str(store.state_path))

    assert writes == 0
    assert reopened.get_state() == store.get_state()


def test_idempotent_retry_after_atomic_commit_does_not_create_second_scene_or_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "atomic-idempotent-retry")
    provider_calls = 0

    async def provider(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal provider_calls
        provider_calls += 1
        return provider_response(scene_bundle())

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "complete", provider)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    request = ChatCompletionRequest(
        model="mock-narrator",
        messages=[ChatMessage(role="user", content="Я отвечаю Горазду.")],
    )

    first = asyncio.run(
        adjudicator.handle_chat(
            request,
            authorization=None,
            idempotency_key="atomic-idempotent-retry",
            request_id="req-atomic-idempotent-retry",
        )
    )
    after_first = authoritative_counts(store)
    second = asyncio.run(
        adjudicator.handle_chat(
            request,
            authorization=None,
            idempotency_key="atomic-idempotent-retry",
            request_id="req-atomic-idempotent-retry",
        )
    )

    assert second == first
    assert provider_calls == 1
    assert authoritative_counts(store) == after_first
    assert table_count_for_campaign(store, "turns") == 1


def test_external_parent_world_change_marks_existing_scene_stale_in_same_version(
    tmp_path: Path,
) -> None:
    _, store = revision_seven_adjudicator(tmp_path, "world-parent-change")
    before = store.get_state()
    player = deepcopy(before["player"])
    player["location"] = "market"
    updated = store.apply_state_patch(
        StatePatch(
            turn=15,
            check_id="world-parent-change",
            patch=[
                PatchOperation(
                    op="replace",
                    path="/player",
                    value=player,
                    reason="world command replaces the player projection",
                    turn=15,
                )
            ],
        ),
        reason="api_patch_apply",
    )

    assert updated["player"]["location"] == "market"
    assert updated["scene_state"]["stale"] is True
    assert updated["scene_state"]["stale_reason"] == "world_change"
    assert updated["scene_state"]["stable_affiliations"] == before["scene_state"][
        "stable_affiliations"
    ]
    assert updated["meta"]["state_version"] == before["meta"]["state_version"] + 1


@pytest.mark.parametrize(
    "removed_path",
    [
        pytest.param("/locations/yard", id="current-location"),
        pytest.param("/characters/gorazd", id="present-character"),
    ],
)
def test_world_removal_preserves_last_reliable_scene_boundary(
    tmp_path: Path,
    removed_path: str,
) -> None:
    _, store = revision_seven_adjudicator(
        tmp_path,
        f"world-removal-{removed_path.rsplit('/', 1)[-1]}",
    )
    before = store.get_state()
    updated = store.apply_state_patch(
        StatePatch(
            turn=15,
            check_id=f"world-removal:{removed_path}",
            patch=[
                PatchOperation(
                    op="remove",
                    path=removed_path,
                    reason="world command invalidates a referenced scene entity",
                    turn=15,
                )
            ],
        ),
        reason="api_patch_apply",
    )

    expected_scene = deepcopy(before["scene_state"])
    expected_scene["stale"] = True
    expected_scene["stale_reason"] = "world_change"
    assert updated["scene_state"] == expected_scene
    assert initial_scene_state(updated) == expected_scene


def test_external_scene_patch_is_forbidden_but_legacy_state_remains_unchanged(
    tmp_path: Path,
) -> None:
    legacy = StateStore(
        str(tmp_path / "legacy-world.db"),
        "legacy-world",
        str(tmp_path / "legacy-world.json"),
    )
    before = legacy.get_state()
    player = deepcopy(before["player"])
    player["location"] = "somewhere-else"
    updated = legacy.apply_state_patch(
        StatePatch(
            turn=1,
            check_id="legacy-world-player",
            patch=[
                PatchOperation(
                    op="replace",
                    path="/player",
                    value=player,
                    reason="legacy world update",
                    turn=1,
                )
            ],
        ),
        reason="api_patch_apply",
    )
    assert "scene_state" not in updated

    _, revision_seven = revision_seven_adjudicator(tmp_path, "forbid-scene-patch")
    with pytest.raises(ValueError, match="cannot write scene_state"):
        revision_seven.apply_state_patch(
            StatePatch(
                turn=15,
                check_id="direct-scene-write",
                patch=[
                    PatchOperation(
                        op="replace",
                        path="/scene_state/location_id",
                        value="market",
                        reason="external callers cannot author scene state",
                        turn=15,
                    )
                ],
            ),
            reason="api_patch_apply",
        )


def test_revision_seven_rollback_to_legacy_state_bootstraps_stale_scene_only(
    tmp_path: Path,
) -> None:
    store = StateStore(
        str(tmp_path / "legacy-target.db"),
        "legacy-target",
        str(tmp_path / "legacy-target.json"),
    )
    legacy_state = store.get_state()
    assert "scene_state" not in legacy_state
    revision_seven_state = scene_state()
    revision_seven_state["meta"]["campaign_id"] = store.campaign_id
    revision_seven_state["meta"]["state_version"] = 2
    store.insert_state_version(revision_seven_state, reason="test:revision-seven-scene")
    assert "scene_state" in store.get_state()

    restored = store.rollback(target_version=1, scene_state_enabled=True)

    assert restored["scene_state"]["stale"] is True
    assert restored["scene_state"]["stale_reason"] == "legacy_bootstrap"
    assert restored["scene_state"]["as_of_party_turn"] == legacy_state["meta"]["turn"]

    legacy_only = StateStore(
        str(tmp_path / "legacy-only.db"),
        "legacy-only",
        str(tmp_path / "legacy-only.json"),
    )
    legacy_second = deepcopy(legacy_only.get_state())
    legacy_second["meta"]["state_version"] = 2
    legacy_only.insert_state_version(legacy_second, reason="legacy:second-version")
    legacy_restored = legacy_only.rollback(target_version=1)
    assert "scene_state" not in legacy_restored


def table_count_for_campaign(store: StateStore, table: str) -> int:
    with store.connect() as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE campaign_id = ?",  # noqa: S608 - fixed test table
                (store.campaign_id,),
            ).fetchone()[0]
        )
