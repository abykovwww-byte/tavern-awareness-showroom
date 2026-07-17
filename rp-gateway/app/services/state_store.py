"""SQLite-backed authoritative world state store."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.json_patch import apply_patch
from app.models.schemas import StatePatch


def now_ts() -> int:
    return int(time.time())


class StateStore:
    def __init__(self, sqlite_path: str, campaign_id: str, state_path: str):
        self.sqlite_path = sqlite_path
        self.campaign_id = campaign_id
        self.state_path = Path(state_path)
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()
        self.bootstrap_state()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.sqlite_path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def init_db(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS campaigns (
                    id TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS state_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    UNIQUE(campaign_id, version),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    player_message TEXT NOT NULL,
                    narrative_response TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, idempotency_key),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    turn_id INTEGER,
                    check_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    result TEXT NOT NULL,
                    roll INTEGER NOT NULL,
                    difficulty INTEGER NOT NULL,
                    final_score INTEGER NOT NULL,
                    modifiers_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, check_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS state_patches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    check_id TEXT,
                    patch_json TEXT NOT NULL,
                    applied INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    applied_at INTEGER,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    request_id TEXT,
                    event_type TEXT NOT NULL,
                    event_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO campaigns(id, created_at) VALUES(?, ?)",
                (self.campaign_id, now_ts()),
            )

    def bootstrap_state(self) -> None:
        if self.current_version() is not None:
            return
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        else:
            state = self.empty_state()
            self.write_state_file(state)
        self.insert_state_version(state, "bootstrap")

    def empty_state(self) -> dict[str, Any]:
        return {
            "meta": {
                "campaign_id": self.campaign_id,
                "schema_version": "1.0.0",
                "state_version": 1,
                "turn": 0,
                "last_updated": "1970-01-01T00:00:00Z",
            },
            "player": {
                "location": "unknown",
                "status": "active",
                "reputation": {},
                "resources": {},
                "known_abilities": [],
                "constraints": [],
                "known_world_facts": [],
            },
            "characters": {},
            "factions": {},
            "locations": {},
            "resources": {},
            "relationships": {},
            "active_threads": [],
            "completed_threads": [],
            "world_constraints": [],
            "timeline": [],
            "last_turn": {"turn": 0, "player_message": "", "narrator_response": "", "state_patch_id": ""},
            "uncertain_facts": [],
        }

    def current_version(self) -> int | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT MAX(version) AS version FROM state_versions WHERE campaign_id = ?",
                (self.campaign_id,),
            ).fetchone()
            return row["version"] if row and row["version"] is not None else None

    def get_state(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT state_json FROM state_versions
                WHERE campaign_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (self.campaign_id,),
            ).fetchone()
            if row is None:
                return self.empty_state()
            return json.loads(row["state_json"])

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT version, created_at, reason FROM state_versions
                WHERE campaign_id = ?
                ORDER BY version DESC
                LIMIT ?
                """,
                (self.campaign_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def preview_patch(self, patch: StatePatch) -> dict[str, Any]:
        state = self.get_state()
        operations = [operation.model_dump(exclude_none=True) for operation in patch.patch]
        return apply_patch(state, operations)

    def apply_state_patch(self, patch: StatePatch, reason: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT state_json, version FROM state_versions
                WHERE campaign_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (self.campaign_id,),
            ).fetchone()
            if row is None:
                state = self.empty_state()
                version = 0
            else:
                state = json.loads(row["state_json"])
                version = int(row["version"])
            if patch.check_id:
                existing = connection.execute(
                    "SELECT id FROM state_patches WHERE campaign_id = ? AND check_id = ? AND applied = 1",
                    (self.campaign_id, patch.check_id),
                ).fetchone()
                if existing:
                    raise ValueError(f"patch for check_id {patch.check_id} is already applied")

            operations = [operation.model_dump(exclude_none=True) for operation in patch.patch]
            candidate = apply_patch(state, operations)
            candidate.setdefault("meta", {})
            candidate["meta"]["state_version"] = version + 1
            candidate["meta"]["turn"] = max(int(candidate["meta"].get("turn", 0)) + 1, patch.turn)
            candidate["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            candidate.setdefault("last_turn", {})
            candidate["last_turn"]["turn"] = candidate["meta"]["turn"]
            candidate["last_turn"]["state_patch_id"] = patch.check_id or f"gateway-v{version + 1}"

            patch_json = patch.model_dump_json()
            patch_row = connection.execute(
                """
                INSERT INTO state_patches(campaign_id, check_id, patch_json, applied, created_at, applied_at)
                VALUES(?, ?, ?, 1, ?, ?)
                """,
                (self.campaign_id, patch.check_id, patch_json, now_ts(), now_ts()),
            )
            connection.execute(
                """
                INSERT INTO state_versions(campaign_id, version, state_json, created_at, reason)
                VALUES(?, ?, ?, ?, ?)
                """,
                (self.campaign_id, version + 1, json.dumps(candidate, ensure_ascii=False), now_ts(), reason),
            )
            _ = patch_row
        self.write_state_file(candidate)
        return candidate

    def insert_state_version(self, state: dict[str, Any], reason: str) -> None:
        version = int(state.get("meta", {}).get("state_version", 1))
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO state_versions(campaign_id, version, state_json, created_at, reason)
                VALUES(?, ?, ?, ?, ?)
                """,
                (self.campaign_id, version, json.dumps(state, ensure_ascii=False), now_ts(), reason),
            )

    def write_state_file(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def rollback(self, target_version: int | None = None) -> dict[str, Any]:
        with self.connect() as connection:
            if target_version is None:
                current = self.current_version()
                target_version = max((current or 1) - 1, 1)
            row = connection.execute(
                "SELECT state_json FROM state_versions WHERE campaign_id = ? AND version = ?",
                (self.campaign_id, target_version),
            ).fetchone()
            if row is None:
                raise ValueError(f"state version not found: {target_version}")
            restored = json.loads(row["state_json"])
            latest = self.current_version() or target_version
            restored["meta"]["state_version"] = latest + 1
            restored["meta"]["turn"] = int(restored["meta"].get("turn", 0)) + 1
            restored["meta"]["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            restored.setdefault("last_turn", {})
            restored["last_turn"]["turn"] = restored["meta"]["turn"]
            restored["last_turn"]["state_patch_id"] = f"rollback:v{target_version}"
            connection.execute(
                """
                INSERT INTO state_versions(campaign_id, version, state_json, created_at, reason)
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    latest + 1,
                    json.dumps(restored, ensure_ascii=False),
                    now_ts(),
                    f"rollback:v{target_version}",
                ),
            )
        self.write_state_file(restored)
        return restored

    def get_turn_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM turns WHERE campaign_id = ? AND idempotency_key = ?",
                (self.campaign_id, idempotency_key),
            ).fetchone()
            return json.loads(row["response_json"]) if row else None

    def record_turn(
        self,
        idempotency_key: str,
        request_id: str,
        player_message: str,
        narrative_response: str,
        response_json: dict[str, Any],
        state_version: int,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO turns(
                    campaign_id, idempotency_key, request_id, player_message,
                    narrative_response, response_json, state_version, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    idempotency_key,
                    request_id,
                    player_message,
                    narrative_response,
                    json.dumps(response_json, ensure_ascii=False),
                    state_version,
                    now_ts(),
                ),
            )
            return int(cursor.lastrowid)

    def record_check(self, turn_id: int | None, outcome: Any) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO checks(
                    campaign_id, turn_id, check_id, action_type, result, roll,
                    difficulty, final_score, modifiers_json, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, check_id) DO UPDATE SET
                    turn_id = COALESCE(checks.turn_id, excluded.turn_id)
                """,
                (
                    self.campaign_id,
                    turn_id,
                    outcome.check_id,
                    outcome.action_type,
                    outcome.result,
                    outcome.roll,
                    outcome.difficulty,
                    outcome.final_score,
                    json.dumps(outcome.modifiers, ensure_ascii=False),
                    now_ts(),
                ),
            )

    def audit(self, event_type: str, event: dict[str, Any], request_id: str | None = None) -> None:
        clean = dict(event)
        clean.pop("authorization", None)
        clean.pop("api_key", None)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events(campaign_id, request_id, event_type, event_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (self.campaign_id, request_id, event_type, json.dumps(clean, ensure_ascii=False), now_ts()),
            )
