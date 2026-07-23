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
                    prompt_json TEXT,
                    state_version INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, idempotency_key),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS turn_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_json TEXT,
                    error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, idempotency_key),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_turn_requests_campaign_request
                    ON turn_requests(campaign_id, request_id);
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
                CREATE TABLE IF NOT EXISTS memory_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    from_turn_id INTEGER NOT NULL,
                    to_turn_id INTEGER NOT NULL,
                    state_version INTEGER NOT NULL,
                    summary_text TEXT NOT NULL,
                    key_facts_json TEXT NOT NULL,
                    open_threads_json TEXT NOT NULL,
                    relationship_changes_json TEXT NOT NULL,
                    player_promises_json TEXT NOT NULL,
                    npc_obligations_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_summaries_campaign_to
                    ON memory_summaries(campaign_id, to_turn_id DESC);
                CREATE TABLE IF NOT EXISTS journal_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    from_turn_id INTEGER NOT NULL,
                    to_turn_id INTEGER NOT NULL,
                    state_version INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    recap_text TEXT NOT NULL,
                    important_changes_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_journal_entries_campaign_to
                    ON journal_entries(campaign_id, to_turn_id DESC);
                """
            )
            self.migrate_turn_columns(connection)
            connection.execute(
                "INSERT OR IGNORE INTO campaigns(id, created_at) VALUES(?, ?)",
                (self.campaign_id, now_ts()),
            )

    def migrate_turn_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(turns)").fetchall()}
        if "prompt_json" not in columns:
            connection.execute("ALTER TABLE turns ADD COLUMN prompt_json TEXT")

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

    def turn_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, request_id, player_message, narrative_response, state_version, created_at
                FROM turns
                WHERE campaign_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.campaign_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def latest_turn(self, include_prompt: bool = False, include_response: bool = False) -> dict[str, Any] | None:
        prompt_column = ", prompt_json" if include_prompt else ""
        response_column = ", response_json" if include_response else ""
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT id, request_id, player_message, narrative_response, state_version, created_at{prompt_column}{response_column}
                FROM turns
                WHERE campaign_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.campaign_id,),
            ).fetchone()
        return dict(row) if row else None

    def turns_before(self, turn_id: int, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, request_id, player_message, narrative_response, state_version, created_at
                FROM turns
                WHERE campaign_id = ? AND id < ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.campaign_id, turn_id, limit),
            ).fetchall()
        return [dict(row) for row in reversed(rows)]

    def turns_for_memory(
        self,
        after_turn_id: int = 0,
        to_turn_id: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT id, request_id, player_message, narrative_response, state_version, created_at
            FROM turns
            WHERE campaign_id = ? AND id > ?
        """
        params: list[Any] = [self.campaign_id, after_turn_id]
        if to_turn_id is not None:
            query += " AND id <= ?"
            params.append(to_turn_id)
        query += " ORDER BY id ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        with self.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def latest_memory_summary(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM memory_summaries
                WHERE campaign_id = ?
                ORDER BY to_turn_id DESC, id DESC
                LIMIT 1
                """,
                (self.campaign_id,),
            ).fetchone()
        return self.memory_summary_from_row(row) if row else None

    def memory_summaries(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM memory_summaries
                WHERE campaign_id = ?
                ORDER BY to_turn_id DESC, id DESC
                LIMIT ?
                """,
                (self.campaign_id, limit),
            ).fetchall()
        return [self.memory_summary_from_row(row) for row in rows]

    def record_memory_summary(
        self,
        from_turn_id: int,
        to_turn_id: int,
        state_version: int,
        summary_text: str,
        key_facts: list[Any],
        open_threads: list[Any],
        relationship_changes: list[Any],
        player_promises: list[Any],
        npc_obligations: list[Any],
        model: str,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memory_summaries(
                    campaign_id, from_turn_id, to_turn_id, state_version,
                    summary_text, key_facts_json, open_threads_json,
                    relationship_changes_json, player_promises_json,
                    npc_obligations_json, created_at, model
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    from_turn_id,
                    to_turn_id,
                    state_version,
                    summary_text,
                    json.dumps(key_facts, ensure_ascii=False),
                    json.dumps(open_threads, ensure_ascii=False),
                    json.dumps(relationship_changes, ensure_ascii=False),
                    json.dumps(player_promises, ensure_ascii=False),
                    json.dumps(npc_obligations, ensure_ascii=False),
                    now_ts(),
                    model,
                ),
            )
            row = connection.execute(
                "SELECT * FROM memory_summaries WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return self.memory_summary_from_row(row)

    def delete_latest_memory_summary(self) -> dict[str, Any] | None:
        latest = self.latest_memory_summary()
        if latest is None:
            return None
        with self.connect() as connection:
            connection.execute("DELETE FROM memory_summaries WHERE id = ?", (latest["id"],))
        return latest

    def memory_summary_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "from_turn_id": row["from_turn_id"],
            "to_turn_id": row["to_turn_id"],
            "state_version": row["state_version"],
            "summary_text": row["summary_text"],
            "key_facts": self.json_list(row["key_facts_json"]),
            "open_threads": self.json_list(row["open_threads_json"]),
            "relationship_changes": self.json_list(row["relationship_changes_json"]),
            "player_promises": self.json_list(row["player_promises_json"]),
            "npc_obligations": self.json_list(row["npc_obligations_json"]),
            "created_at": row["created_at"],
            "model": row["model"],
        }

    def json_list(self, value: str) -> list[Any]:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def latest_journal_entry(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM journal_entries
                WHERE campaign_id = ?
                ORDER BY to_turn_id DESC, id DESC
                LIMIT 1
                """,
                (self.campaign_id,),
            ).fetchone()
        return self.journal_entry_from_row(row) if row else None

    def journal_entries(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM journal_entries
                WHERE campaign_id = ?
                ORDER BY to_turn_id DESC, id DESC
                LIMIT ?
                """,
                (self.campaign_id, limit),
            ).fetchall()
        return [self.journal_entry_from_row(row) for row in rows]

    def record_journal_entry(
        self,
        from_turn_id: int,
        to_turn_id: int,
        state_version: int,
        title: str,
        recap_text: str,
        important_changes: list[Any],
        model: str,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO journal_entries(
                    campaign_id, from_turn_id, to_turn_id, state_version,
                    title, recap_text, important_changes_json, created_at, model
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    from_turn_id,
                    to_turn_id,
                    state_version,
                    title,
                    recap_text,
                    json.dumps(important_changes, ensure_ascii=False),
                    now_ts(),
                    model,
                ),
            )
            row = connection.execute(
                "SELECT * FROM journal_entries WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return self.journal_entry_from_row(row)

    def delete_latest_journal_entry(self) -> dict[str, Any] | None:
        latest = self.latest_journal_entry()
        if latest is None:
            return None
        with self.connect() as connection:
            connection.execute("DELETE FROM journal_entries WHERE id = ?", (latest["id"],))
        return latest

    def journal_entry_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "from_turn_id": row["from_turn_id"],
            "to_turn_id": row["to_turn_id"],
            "state_version": row["state_version"],
            "title": row["title"],
            "recap_text": row["recap_text"],
            "important_changes": self.json_list(row["important_changes_json"]),
            "created_at": row["created_at"],
            "model": row["model"],
        }

    def preview_patch(self, patch: StatePatch) -> dict[str, Any]:
        state = self.get_state()
        operations = [operation.model_dump(exclude_none=True) for operation in patch.patch]
        return apply_patch(state, operations)

    def create_patch_proposal(self, patch: StatePatch) -> str:
        if not patch.check_id:
            raise ValueError("patch.check_id is required for proposals")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO state_patches(campaign_id, check_id, patch_json, applied, created_at, applied_at)
                VALUES(?, ?, ?, 0, ?, NULL)
                """,
                (self.campaign_id, patch.check_id, patch.model_dump_json(), now_ts()),
            )
        return patch.check_id

    def pending_patches(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, check_id, patch_json, created_at FROM state_patches
                WHERE campaign_id = ? AND applied = 0
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.campaign_id, limit),
            ).fetchall()
        proposals: list[dict[str, Any]] = []
        for row in rows:
            patch = json.loads(row["patch_json"])
            proposals.append(
                {
                    "id": row["id"],
                    "proposal_id": row["check_id"],
                    "turn": patch.get("turn"),
                    "source": patch.get("source"),
                    "created_at": row["created_at"],
                    "operations": len(patch.get("patch", [])),
                }
            )
        return proposals

    def get_pending_patch(self, proposal_id: str = "latest") -> StatePatch:
        with self.connect() as connection:
            if proposal_id == "latest":
                row = connection.execute(
                    """
                    SELECT patch_json FROM state_patches
                    WHERE campaign_id = ? AND applied = 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self.campaign_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT patch_json FROM state_patches
                    WHERE campaign_id = ? AND check_id = ? AND applied = 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self.campaign_id, proposal_id),
                ).fetchone()
        if row is None:
            raise ValueError(f"pending patch not found: {proposal_id}")
        return StatePatch.model_validate(json.loads(row["patch_json"]))

    def discard_pending_patch(self, proposal_id: str = "latest") -> str:
        with self.connect() as connection:
            if proposal_id == "latest":
                row = connection.execute(
                    """
                    SELECT id, check_id FROM state_patches
                    WHERE campaign_id = ? AND applied = 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self.campaign_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT id, check_id FROM state_patches
                    WHERE campaign_id = ? AND check_id = ? AND applied = 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self.campaign_id, proposal_id),
                ).fetchone()
            if row is None:
                raise ValueError(f"pending patch not found: {proposal_id}")
            connection.execute(
                """
                UPDATE state_patches
                SET applied = -1, applied_at = ?
                WHERE id = ?
                """,
                (now_ts(), row["id"]),
            )
            return str(row["check_id"])

    def apply_pending_patch(self, proposal_id: str = "latest", reason: str = "world_instruction_apply") -> dict[str, Any]:
        with self.connect() as connection:
            if proposal_id == "latest":
                row = connection.execute(
                    """
                    SELECT id, check_id, patch_json FROM state_patches
                    WHERE campaign_id = ? AND applied = 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self.campaign_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT id, check_id, patch_json FROM state_patches
                    WHERE campaign_id = ? AND check_id = ? AND applied = 0
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self.campaign_id, proposal_id),
                ).fetchone()
            if row is None:
                raise ValueError(f"pending patch not found: {proposal_id}")

            patch = StatePatch.model_validate(json.loads(row["patch_json"]))
            current = connection.execute(
                """
                SELECT state_json, version FROM state_versions
                WHERE campaign_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (self.campaign_id,),
            ).fetchone()
            if current is None:
                state = self.empty_state()
                version = 0
            else:
                state = json.loads(current["state_json"])
                version = int(current["version"])

            if patch.check_id:
                existing = connection.execute(
                    """
                    SELECT id FROM state_patches
                    WHERE campaign_id = ? AND check_id = ? AND applied = 1 AND id != ?
                    """,
                    (self.campaign_id, patch.check_id, row["id"]),
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

            connection.execute(
                """
                UPDATE state_patches
                SET applied = 1, applied_at = ?
                WHERE id = ?
                """,
                (now_ts(), row["id"]),
            )
            connection.execute(
                """
                INSERT INTO state_versions(campaign_id, version, state_json, created_at, reason)
                VALUES(?, ?, ?, ?, ?)
                """,
                (self.campaign_id, version + 1, json.dumps(candidate, ensure_ascii=False), now_ts(), reason),
            )
        self.write_state_file(candidate)
        return candidate

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

    def get_turn_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, request_id, player_message, narrative_response, state_version, created_at
                FROM turns
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.campaign_id, request_id),
            ).fetchone()
        return dict(row) if row else None

    def begin_turn_request(self, idempotency_key: str, request_id: str) -> dict[str, Any]:
        timestamp = now_ts()
        with self.connect() as connection:
            existing_turn = connection.execute(
                "SELECT response_json FROM turns WHERE campaign_id = ? AND idempotency_key = ?",
                (self.campaign_id, idempotency_key),
            ).fetchone()
            if existing_turn:
                return {
                    "acquired": False,
                    "status": "completed",
                    "response": json.loads(existing_turn["response_json"]),
                }
            try:
                connection.execute(
                    """
                    INSERT INTO turn_requests(
                        campaign_id, idempotency_key, request_id, status,
                        response_json, error, created_at, updated_at
                    )
                    VALUES(?, ?, ?, 'running', NULL, NULL, ?, ?)
                    """,
                    (self.campaign_id, idempotency_key, request_id, timestamp, timestamp),
                )
                return {
                    "acquired": True,
                    "status": "running",
                    "request_id": request_id,
                    "idempotency_key": idempotency_key,
                }
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """
                    SELECT * FROM turn_requests
                    WHERE campaign_id = ? AND idempotency_key = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (self.campaign_id, idempotency_key),
                ).fetchone()
        status = self.turn_request_from_row(row) if row else {"status": "unknown"}
        status["acquired"] = False
        return status

    def complete_turn_request(self, idempotency_key: str, response_json: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE turn_requests
                SET status = 'completed', response_json = ?, error = NULL, updated_at = ?
                WHERE campaign_id = ? AND idempotency_key = ?
                """,
                (json.dumps(response_json, ensure_ascii=False), now_ts(), self.campaign_id, idempotency_key),
            )

    def fail_turn_request(self, idempotency_key: str, error: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE turn_requests
                SET status = 'failed', error = ?, updated_at = ?
                WHERE campaign_id = ? AND idempotency_key = ?
                """,
                (error[:500], now_ts(), self.campaign_id, idempotency_key),
            )

    def get_turn_request(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM turn_requests
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.campaign_id, request_id),
            ).fetchone()
        return self.turn_request_from_row(row) if row else None

    def turn_request_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        response = json.loads(row["response_json"]) if row["response_json"] else None
        return {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "idempotency_key": row["idempotency_key"],
            "request_id": row["request_id"],
            "status": row["status"],
            "response": response,
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def record_turn(
        self,
        idempotency_key: str,
        request_id: str,
        player_message: str,
        narrative_response: str,
        response_json: dict[str, Any],
        state_version: int,
        prompt_messages: list[dict[str, str]] | None = None,
    ) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO turns(
                    campaign_id, idempotency_key, request_id, player_message,
                    narrative_response, response_json, prompt_json, state_version, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    idempotency_key,
                    request_id,
                    player_message,
                    narrative_response,
                    json.dumps(response_json, ensure_ascii=False),
                    json.dumps(prompt_messages, ensure_ascii=False) if prompt_messages is not None else None,
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
