"""SQLite-backed authoritative world state store."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.json_patch import apply_patch
from app.models.schemas import StatePatch
from app.services.trace_redaction import redact_trace_value


logger = logging.getLogger(__name__)


def now_ts() -> int:
    return int(time.time())


def archive_search_terms(query: str) -> list[str]:
    """Small local lexical retriever; no player history leaves SQLite for embeddings."""
    stop_words = {
        "это", "как", "что", "где", "когда", "теперь", "потом", "тогда", "очень", "снова",
        "было", "быть", "меня", "тебя", "него", "него", "себя", "этого", "этой", "который",
        "with", "this", "that", "from", "have", "what", "where", "then", "they", "them",
    }
    terms = re.findall(r"[a-zа-яё0-9]{3,}", query.lower())
    return list(dict.fromkeys(term for term in terms if term not in stop_words))[:12]


def archive_word_tokens(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9]{3,}", text.lower())


def archive_stem(term: str) -> str:
    """Conservative local stem for inflection-aware retrieval without external services."""
    if len(term) <= 5:
        return term
    return term[: max(len(term) - 2, 4)]


def archive_ngrams(terms: list[str], size: int = 3) -> set[str]:
    grams: set[str] = set()
    for term in terms:
        if len(term) < size:
            grams.add(term)
            continue
        grams.update(term[index : index + size] for index in range(len(term) - size + 1))
    return grams


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
                CREATE TABLE IF NOT EXISTS relationship_causes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    axis TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    weight INTEGER NOT NULL,
                    turn_id INTEGER NOT NULL,
                    party_turn INTEGER NOT NULL,
                    expires_turn INTEGER,
                    evidence TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, character_id, axis, event_id, turn_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS character_badges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    badge_kind TEXT NOT NULL,
                    badge_id TEXT NOT NULL,
                    party_turn INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    payload_json TEXT,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, character_id, badge_kind, badge_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS narrative_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    axis TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    opened_turn INTEGER NOT NULL,
                    due_turn INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    resolution TEXT,
                    resolved_turn INTEGER,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS character_axis_state (
                    campaign_id TEXT NOT NULL,
                    character_id TEXT NOT NULL,
                    axis TEXT NOT NULL,
                    band TEXT NOT NULL,
                    band_since_turn INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(campaign_id, character_id, axis),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_narrative_events_active
                    ON narrative_events(campaign_id, status, due_turn);
                CREATE TABLE IF NOT EXISTS training_runtime_snapshots (
                    campaign_id TEXT PRIMARY KEY,
                    contract_hash TEXT NOT NULL,
                    contract_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
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
                    metadata_json TEXT,
                    state_version INTEGER NOT NULL,
                    party_turn INTEGER,
                    excluded_from_memory INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, idempotency_key),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE TABLE IF NOT EXISTS turn_feedback (
                    campaign_id TEXT NOT NULL,
                    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    liked INTEGER NOT NULL DEFAULT 0,
                    rating INTEGER NOT NULL DEFAULT 0 CHECK(rating IN (-1, 0, 1)),
                    source_ui TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(campaign_id, turn_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_turn_feedback_liked
                    ON turn_feedback(liked, campaign_id, turn_id);
                CREATE TABLE IF NOT EXISTS training_artifacts (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    artifact_key TEXT NOT NULL,
                    artifact_revision INTEGER NOT NULL,
                    blueprint_id TEXT NOT NULL,
                    public_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, artifact_key, artifact_revision),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_training_artifacts_campaign_turn
                    ON training_artifacts(campaign_id, turn_id DESC);
                CREATE TABLE IF NOT EXISTS training_artifact_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL REFERENCES training_artifacts(id) ON DELETE CASCADE,
                    artifact_revision INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    filled_field_ids_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    consumed_turn_id INTEGER REFERENCES turns(id) ON DELETE SET NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, event_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_training_artifact_events_pending
                    ON training_artifact_events(campaign_id, consumed_turn_id, id);
                CREATE TABLE IF NOT EXISTS training_workspace_files (
                    id TEXT PRIMARY KEY,
                    campaign_id TEXT NOT NULL,
                    file_key TEXT NOT NULL,
                    file_revision INTEGER NOT NULL,
                    blueprint_id TEXT NOT NULL,
                    folder_id TEXT NOT NULL,
                    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    available_from_turn INTEGER NOT NULL,
                    available_until_turn INTEGER,
                    public_json TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    materialized_turn INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, file_key, file_revision),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_training_workspace_files_available
                    ON training_workspace_files(campaign_id, available_from_turn, available_until_turn);
                CREATE TABLE IF NOT EXISTS training_workspace_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    file_id TEXT NOT NULL REFERENCES training_workspace_files(id) ON DELETE CASCADE,
                    file_revision INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    consumed_turn_id INTEGER REFERENCES turns(id) ON DELETE SET NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(campaign_id, event_id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_training_workspace_events_pending
                    ON training_workspace_events(campaign_id, consumed_turn_id, id);
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
                CREATE TABLE IF NOT EXISTS turn_trace_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    turn_id INTEGER,
                    party_turn INTEGER,
                    phase_key TEXT NOT NULL,
                    alignment_key TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    UNIQUE(campaign_id, request_id, phase_key),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
                    FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_turn_trace_events_request
                    ON turn_trace_events(campaign_id, request_id, id);
                CREATE INDEX IF NOT EXISTS idx_turn_trace_events_turn
                    ON turn_trace_events(campaign_id, turn_id, id);
                CREATE TABLE IF NOT EXISTS turn_state_mutations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    turn_id INTEGER,
                    party_turn INTEGER,
                    phase_key TEXT NOT NULL,
                    store_name TEXT NOT NULL,
                    entity_key TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    lane TEXT NOT NULL DEFAULT 'background' CHECK(lane IN ('main', 'background')),
                    source TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
                    FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_turn_state_mutations_request
                    ON turn_state_mutations(campaign_id, request_id, id);
                CREATE INDEX IF NOT EXISTS idx_turn_state_mutations_turn
                    ON turn_state_mutations(campaign_id, turn_id, id);
                CREATE TABLE IF NOT EXISTS turn_phase_annotations (
                    id TEXT NOT NULL,
                    campaign_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    turn_id INTEGER,
                    phase_key TEXT NOT NULL,
                    author_user_id TEXT,
                    body TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(campaign_id, id),
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
                    FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE SET NULL
                );
                CREATE INDEX IF NOT EXISTS idx_turn_phase_annotations_request
                    ON turn_phase_annotations(campaign_id, request_id, phase_key, created_at);
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
                CREATE TABLE IF NOT EXISTS memory_chapters (
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
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
                    UNIQUE(campaign_id, from_turn_id, to_turn_id)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_chapters_campaign_to
                    ON memory_chapters(campaign_id, to_turn_id DESC);
                CREATE TABLE IF NOT EXISTS rp_story_memory_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    from_turn_id INTEGER NOT NULL,
                    to_turn_id INTEGER NOT NULL,
                    state_version INTEGER NOT NULL,
                    memory_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id),
                    UNIQUE(campaign_id, revision),
                    UNIQUE(campaign_id, to_turn_id)
                );
                CREATE INDEX IF NOT EXISTS idx_rp_story_memory_campaign_to
                    ON rp_story_memory_snapshots(campaign_id, to_turn_id DESC, revision DESC);
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
                CREATE TABLE IF NOT EXISTS lore_cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    keywords_json TEXT NOT NULL,
                    always_on INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    archived INTEGER NOT NULL DEFAULT 0,
                    source_turn_ids_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_lore_cards_campaign_active
                    ON lore_cards(campaign_id, archived, enabled, updated_at DESC);
                CREATE TABLE IF NOT EXISTS memory_checkpoints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    through_turn_id INTEGER,
                    state_version INTEGER NOT NULL,
                    memory_coverage_turn_id INTEGER,
                    state_json TEXT NOT NULL,
                    lore_card_ids_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_memory_checkpoints_campaign_created
                    ON memory_checkpoints(campaign_id, created_at DESC, id DESC);
                CREATE TABLE IF NOT EXISTS service_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    next_attempt_at INTEGER NOT NULL,
                    request_id TEXT,
                    last_error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
                );
                CREATE INDEX IF NOT EXISTS idx_service_jobs_campaign_status
                    ON service_jobs(campaign_id, status, next_attempt_at, id);
                """
            )
            self.migrate_turn_columns(connection)
            self.migrate_relationship_turn_columns(connection)
            self.migrate_turn_feedback_columns(connection)
            self.migrate_turn_trace_tables(connection)
            connection.execute(
                "INSERT OR IGNORE INTO campaigns(id, created_at) VALUES(?, ?)",
                (self.campaign_id, now_ts()),
            )

    def migrate_turn_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(turns)").fetchall()}
        if "prompt_json" not in columns:
            connection.execute("ALTER TABLE turns ADD COLUMN prompt_json TEXT")
        if "metadata_json" not in columns:
            connection.execute("ALTER TABLE turns ADD COLUMN metadata_json TEXT")
        if "excluded_from_memory" not in columns:
            connection.execute(
                "ALTER TABLE turns ADD COLUMN excluded_from_memory INTEGER NOT NULL DEFAULT 0"
            )
        if "party_turn" not in columns:
            connection.execute("ALTER TABLE turns ADD COLUMN party_turn INTEGER")
        connection.execute(
            """
            UPDATE turns AS current_turn
            SET party_turn = (
                SELECT COUNT(*)
                FROM turns AS preceding_turn
                WHERE preceding_turn.campaign_id = current_turn.campaign_id
                  AND preceding_turn.id <= current_turn.id
            )
            WHERE current_turn.party_turn IS NULL
            """
        )

    def migrate_relationship_turn_columns(self, connection: sqlite3.Connection) -> None:
        cause_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(relationship_causes)").fetchall()
        }
        badge_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(character_badges)").fetchall()
        }
        if "party_turn" in cause_columns and "party_turn" in badge_columns:
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationship_causes_lookup "
                "ON relationship_causes(campaign_id, character_id, axis, party_turn)"
            )
            return

        relationship_tables = (
            "relationship_causes",
            "character_badges",
            "narrative_events",
            "character_axis_state",
        )
        populated = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in relationship_tables
        }
        if any(populated.values()):
            raise RuntimeError(
                "relationship party-turn migration requires empty relationship tables: "
                + ", ".join(f"{table}={count}" for table, count in populated.items())
            )

        connection.execute("DROP INDEX IF EXISTS idx_relationship_causes_lookup")
        connection.execute("DROP INDEX IF EXISTS idx_narrative_events_active")
        for table in relationship_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.executescript(
            """
            CREATE TABLE relationship_causes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                axis TEXT NOT NULL,
                event_id TEXT NOT NULL,
                weight INTEGER NOT NULL,
                turn_id INTEGER NOT NULL,
                party_turn INTEGER NOT NULL,
                expires_turn INTEGER,
                evidence TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE(campaign_id, character_id, axis, event_id, turn_id),
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
            );
            CREATE TABLE character_badges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                badge_kind TEXT NOT NULL,
                badge_id TEXT NOT NULL,
                party_turn INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT,
                created_at INTEGER NOT NULL,
                UNIQUE(campaign_id, character_id, badge_kind, badge_id),
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
            );
            CREATE TABLE narrative_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                axis TEXT NOT NULL,
                event_id TEXT NOT NULL,
                status TEXT NOT NULL,
                opened_turn INTEGER NOT NULL,
                due_turn INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                resolution TEXT,
                resolved_turn INTEGER,
                created_at INTEGER NOT NULL,
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
            );
            CREATE TABLE character_axis_state (
                campaign_id TEXT NOT NULL,
                character_id TEXT NOT NULL,
                axis TEXT NOT NULL,
                band TEXT NOT NULL,
                band_since_turn INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(campaign_id, character_id, axis),
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id)
            );
            CREATE INDEX idx_relationship_causes_lookup
                ON relationship_causes(campaign_id, character_id, axis, party_turn);
            CREATE INDEX idx_narrative_events_active
                ON narrative_events(campaign_id, status, due_turn);
            """
        )

    def migrate_turn_feedback_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(turn_feedback)").fetchall()}
        if "rating" not in columns:
            connection.execute(
                "ALTER TABLE turn_feedback ADD COLUMN rating INTEGER NOT NULL DEFAULT 0 "
                "CHECK(rating IN (-1, 0, 1))"
            )
            connection.execute("UPDATE turn_feedback SET rating = 1 WHERE liked = 1")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_turn_feedback_rating "
            "ON turn_feedback(rating, campaign_id, turn_id)"
        )

    def migrate_turn_trace_tables(self, connection: sqlite3.Connection) -> None:
        """Upgrade additive trace fields and the draft annotation key."""

        mutation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(turn_state_mutations)").fetchall()
        }
        if "lane" not in mutation_columns:
            connection.execute(
                "ALTER TABLE turn_state_mutations "
                "ADD COLUMN lane TEXT NOT NULL DEFAULT 'background' "
                "CHECK(lane IN ('main', 'background'))"
            )

        columns = connection.execute("PRAGMA table_info(turn_phase_annotations)").fetchall()
        primary_key = [row["name"] for row in sorted(columns, key=lambda row: int(row["pk"])) if row["pk"]]
        if primary_key == ["id"]:
            connection.execute("ALTER TABLE turn_phase_annotations RENAME TO turn_phase_annotations_legacy")
            connection.executescript(
                """
            CREATE TABLE turn_phase_annotations (
                id TEXT NOT NULL,
                campaign_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                turn_id INTEGER,
                phase_key TEXT NOT NULL,
                author_user_id TEXT,
                body TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(campaign_id, id),
                FOREIGN KEY(campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
                FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE SET NULL
            );
            INSERT INTO turn_phase_annotations(
                id, campaign_id, request_id, turn_id, phase_key,
                author_user_id, body, created_at
            )
            SELECT id, campaign_id, request_id, turn_id, phase_key,
                   author_user_id, body, created_at
            FROM turn_phase_annotations_legacy;
            DROP TABLE turn_phase_annotations_legacy;
            CREATE INDEX idx_turn_phase_annotations_request
                ON turn_phase_annotations(campaign_id, request_id, phase_key, created_at);
                """
            )

    def recover_interrupted_work(self) -> dict[str, int]:
        """Reconcile work that could only remain running after a process restart."""
        timestamp = now_ts()
        with self.connect() as connection:
            completed_requests = connection.execute(
                """
                UPDATE turn_requests
                SET status = 'completed',
                    response_json = (
                        SELECT turns.response_json FROM turns
                        WHERE turns.campaign_id = turn_requests.campaign_id
                          AND turns.idempotency_key = turn_requests.idempotency_key
                        ORDER BY turns.id DESC LIMIT 1
                    ),
                    error = NULL,
                    updated_at = ?
                WHERE campaign_id = ? AND status = 'running'
                  AND EXISTS (
                      SELECT 1 FROM turns
                      WHERE turns.campaign_id = turn_requests.campaign_id
                        AND turns.idempotency_key = turn_requests.idempotency_key
                  )
                """,
                (timestamp, self.campaign_id),
            ).rowcount
            failed_requests = connection.execute(
                """
                UPDATE turn_requests
                SET status = 'failed',
                    error = 'Gateway restarted before the request completed. Check history before retrying.',
                    updated_at = ?
                WHERE campaign_id = ? AND status = 'running'
                """,
                (timestamp, self.campaign_id),
            ).rowcount
            resumed_jobs = connection.execute(
                """
                UPDATE service_jobs
                SET status = 'pending', next_attempt_at = ?, updated_at = ?
                WHERE campaign_id = ? AND status = 'running'
                """,
                (timestamp, timestamp, self.campaign_id),
            ).rowcount
        return {
            "completed_requests": max(completed_requests, 0),
            "failed_requests": max(failed_requests, 0),
            "resumed_jobs": max(resumed_jobs, 0),
        }

    def enqueue_service_job(self, job_type: str, request_id: str | None, max_attempts: int = 5) -> dict[str, Any]:
        if job_type not in {"memory", "rp_story_memory", "relationship_extraction", "journal"}:
            raise ValueError(f"unsupported service job type: {job_type}")
        timestamp = now_ts()
        with self.connect() as connection:
            if job_type == "relationship_extraction":
                row = connection.execute(
                    """
                    SELECT * FROM service_jobs
                    WHERE campaign_id = ? AND job_type = ? AND request_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (self.campaign_id, job_type, request_id),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT * FROM service_jobs
                    WHERE campaign_id = ? AND job_type = ? AND status IN ('pending', 'running')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (self.campaign_id, job_type),
                ).fetchone()
            if row is None:
                cursor = connection.execute(
                    """
                    INSERT INTO service_jobs(
                        campaign_id, job_type, status, attempts, max_attempts,
                        next_attempt_at, request_id, last_error, created_at, updated_at
                    ) VALUES(?, ?, 'pending', 0, ?, ?, ?, NULL, ?, ?)
                    """,
                    (self.campaign_id, job_type, max(max_attempts, 1), timestamp, request_id, timestamp, timestamp),
                )
                row = connection.execute("SELECT * FROM service_jobs WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        return self.service_job_from_row(row)

    def due_service_job(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM service_jobs
                WHERE campaign_id = ? AND status = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at ASC, id ASC LIMIT 1
                """,
                (self.campaign_id, now_ts()),
            ).fetchone()
        return self.service_job_from_row(row) if row else None

    def next_service_job_delay(self) -> int | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT MIN(next_attempt_at) AS next_attempt_at FROM service_jobs
                WHERE campaign_id = ? AND status = 'pending'
                """,
                (self.campaign_id,),
            ).fetchone()
        if not row or row["next_attempt_at"] is None:
            return None
        return max(int(row["next_attempt_at"]) - now_ts(), 0)

    def mark_service_job_running(self, job_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE service_jobs SET status = 'running', attempts = attempts + 1, updated_at = ?
                WHERE id = ? AND campaign_id = ?
                """,
                (now_ts(), job_id, self.campaign_id),
            )
            row = connection.execute("SELECT * FROM service_jobs WHERE id = ?", (job_id,)).fetchone()
        return self.service_job_from_row(row)

    def complete_service_job(self, job_id: int) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE service_jobs SET status = 'succeeded', last_error = NULL, updated_at = ?
                WHERE id = ? AND campaign_id = ?
                """,
                (now_ts(), job_id, self.campaign_id),
            )

    def retry_service_job(self, job_id: int, error: str, retry_delay: int) -> None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT attempts, max_attempts FROM service_jobs WHERE id = ? AND campaign_id = ?",
                (job_id, self.campaign_id),
            ).fetchone()
            if row is None:
                return
            terminal = int(row["attempts"]) >= int(row["max_attempts"])
            timestamp = now_ts()
            connection.execute(
                """
                UPDATE service_jobs
                SET status = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
                WHERE id = ? AND campaign_id = ?
                """,
                (
                    "failed" if terminal else "pending",
                    timestamp if terminal else timestamp + max(retry_delay, 1),
                    error[:500],
                    timestamp,
                    job_id,
                    self.campaign_id,
                ),
            )

    def service_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM service_jobs WHERE campaign_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (self.campaign_id, max(limit, 1)),
            ).fetchall()
        return [self.service_job_from_row(row) for row in rows]

    def service_job_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "next_attempt_at": row["next_attempt_at"],
            "request_id": row["request_id"],
            "last_error": row["last_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_lore_card(
        self,
        title: str,
        content: str,
        keywords: list[str],
        always_on: bool,
        enabled: bool,
        source_turn_ids: list[int],
    ) -> dict[str, Any]:
        timestamp = now_ts()
        clean_keywords = list(dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip()))[:40]
        clean_turn_ids = list(dict.fromkeys(int(turn_id) for turn_id in source_turn_ids if int(turn_id) > 0))[:100]
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO lore_cards(
                    campaign_id, title, content, keywords_json, always_on, enabled,
                    archived, source_turn_ids_json, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    title.strip(),
                    content.strip(),
                    json.dumps(clean_keywords, ensure_ascii=False),
                    int(always_on),
                    int(enabled),
                    json.dumps(clean_turn_ids),
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute("SELECT * FROM lore_cards WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        return self.lore_card_from_row(row)

    def lore_cards(self, include_archived: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM lore_cards WHERE campaign_id = ?"
        params: list[Any] = [self.campaign_id]
        if not include_archived:
            query += " AND archived = 0"
        query += " ORDER BY updated_at DESC, id DESC LIMIT ?"
        params.append(max(limit, 1))
        with self.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self.lore_card_from_row(row) for row in rows]

    def update_lore_card(self, card_id: int, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {"title", "content", "always_on", "enabled", "archived"}
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed or value is None:
                continue
            assignments.append(f"{key} = ?")
            values.append(int(value) if key in {"always_on", "enabled", "archived"} else str(value).strip())
        if updates.get("keywords") is not None:
            keywords = list(dict.fromkeys(str(item).strip() for item in updates["keywords"] if str(item).strip()))[:40]
            assignments.append("keywords_json = ?")
            values.append(json.dumps(keywords, ensure_ascii=False))
        if not assignments:
            raise ValueError("no lore card fields to update")
        assignments.append("updated_at = ?")
        values.append(now_ts())
        values.extend([card_id, self.campaign_id])
        with self.connect() as connection:
            updated = connection.execute(
                f"UPDATE lore_cards SET {', '.join(assignments)} WHERE id = ? AND campaign_id = ?",
                tuple(values),
            ).rowcount
            row = connection.execute(
                "SELECT * FROM lore_cards WHERE id = ? AND campaign_id = ?",
                (card_id, self.campaign_id),
            ).fetchone()
        if updated == 0 or row is None:
            raise ValueError(f"lore card not found: {card_id}")
        return self.lore_card_from_row(row)

    def lore_cards_for_prompt(self, query: str, limit: int = 8, max_chars: int = 12_000) -> list[dict[str, Any]]:
        query_terms = archive_search_terms(query)
        query_stems = {archive_stem(term) for term in query_terms}
        ranked: list[tuple[float, dict[str, Any]]] = []
        for card in self.lore_cards(limit=500):
            if not card["enabled"] or card["archived"]:
                continue
            text = " ".join([card["title"], card["content"], *card["keywords"]]).lower()
            lexical = sum(text.count(term) for term in query_terms)
            card_stems = {archive_stem(token) for token in archive_word_tokens(text)}
            stem_hits = len(query_stems & card_stems)
            if not card["always_on"] and not lexical and not stem_hits:
                continue
            score = 1_000.0 if card["always_on"] else float((lexical * 4) + (stem_hits * 2))
            ranked.append((score, card | {"retrieval_score": score}))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"], pair[1]["id"]), reverse=True)
        selected: list[dict[str, Any]] = []
        used = 0
        for _, card in ranked[: max(limit, 1)]:
            size = len(card["title"]) + len(card["content"]) + sum(len(keyword) for keyword in card["keywords"])
            if selected and used + size > max_chars:
                continue
            selected.append(card)
            used += size
        return selected

    def lore_card_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "title": row["title"],
            "content": row["content"],
            "keywords": self.json_list(row["keywords_json"]),
            "always_on": bool(row["always_on"]),
            "enabled": bool(row["enabled"]),
            "archived": bool(row["archived"]),
            "source_turn_ids": self.json_list(row["source_turn_ids_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_memory_checkpoint(self, label: str) -> dict[str, Any]:
        latest_turn = self.latest_turn()
        coverage = self.latest_memory_coverage()
        state = self.get_state()
        lore_card_ids = [card["id"] for card in self.lore_cards() if card["enabled"]]
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memory_checkpoints(
                    campaign_id, label, through_turn_id, state_version, memory_coverage_turn_id,
                    state_json, lore_card_ids_json, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    label.strip(),
                    int(latest_turn["id"]) if latest_turn else None,
                    int(state.get("meta", {}).get("state_version", 1)),
                    int(coverage["to_turn_id"]) if coverage else None,
                    json.dumps(state, ensure_ascii=False),
                    json.dumps(lore_card_ids),
                    now_ts(),
                ),
            )
            row = connection.execute("SELECT * FROM memory_checkpoints WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        return self.memory_checkpoint_from_row(row)

    def memory_checkpoints(self, limit: int = 50, include_state: bool = False) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_checkpoints WHERE campaign_id = ?
                ORDER BY created_at DESC, id DESC LIMIT ?
                """,
                (self.campaign_id, max(limit, 1)),
            ).fetchall()
        return [self.memory_checkpoint_from_row(row, include_state=include_state) for row in rows]

    def get_memory_checkpoint(self, checkpoint_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_checkpoints WHERE id = ? AND campaign_id = ?",
                (int(checkpoint_id), self.campaign_id),
            ).fetchone()
        if row is None:
            raise ValueError(f"checkpoint not found: {checkpoint_id}")
        return self.memory_checkpoint_from_row(row)

    def memory_checkpoint_from_row(self, row: sqlite3.Row, include_state: bool = True) -> dict[str, Any]:
        checkpoint = {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "label": row["label"],
            "through_turn_id": row["through_turn_id"],
            "state_version": row["state_version"],
            "memory_coverage_turn_id": row["memory_coverage_turn_id"],
            "lore_card_ids": self.json_list(row["lore_card_ids_json"]),
            "created_at": row["created_at"],
        }
        if include_state:
            checkpoint["state"] = json.loads(row["state_json"])
        return checkpoint

    def fork_from_checkpoint(
        self,
        *,
        checkpoint_id: int,
        target_campaign_id: str,
        target_state_path: str,
    ) -> dict[str, Any]:
        """Create an isolated campaign branch from a party checkpoint."""
        checkpoint = self.get_memory_checkpoint(checkpoint_id)
        state = json.loads(json.dumps(checkpoint["state"], ensure_ascii=False))
        state.setdefault("meta", {})["campaign_id"] = target_campaign_id
        state["meta"]["branch_parent_campaign_id"] = self.campaign_id
        state["meta"]["branch_checkpoint_id"] = int(checkpoint_id)
        through_turn_id = checkpoint.get("through_turn_id")
        target_path = Path(target_state_path)
        turn_map: dict[int, int] = {}
        copied_turns = 0

        try:
            with self.connect() as connection:
                exists = connection.execute("SELECT 1 FROM campaigns WHERE id = ?", (target_campaign_id,)).fetchone()
                if exists:
                    raise ValueError(f"branch campaign already exists: {target_campaign_id}")
                connection.execute("INSERT INTO campaigns(id, created_at) VALUES(?, ?)", (target_campaign_id, now_ts()))
                runtime_snapshot = connection.execute(
                    "SELECT contract_hash, contract_json, created_at FROM training_runtime_snapshots WHERE campaign_id = ?",
                    (self.campaign_id,),
                ).fetchone()
                if runtime_snapshot is not None:
                    connection.execute(
                        """
                        INSERT INTO training_runtime_snapshots(campaign_id, contract_hash, contract_json, created_at)
                        VALUES(?, ?, ?, ?)
                        """,
                        (
                            target_campaign_id,
                            runtime_snapshot["contract_hash"],
                            runtime_snapshot["contract_json"],
                            runtime_snapshot["created_at"],
                        ),
                    )
                connection.execute(
                    """
                    INSERT INTO state_versions(campaign_id, version, state_json, created_at, reason)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (
                        target_campaign_id,
                        int(checkpoint["state_version"]),
                        json.dumps(state, ensure_ascii=False),
                        now_ts(),
                        f"branch_from:{self.campaign_id}:checkpoint:{checkpoint_id}",
                    ),
                )

                if through_turn_id is not None:
                    source_turns = connection.execute(
                        """
                        SELECT * FROM turns
                        WHERE campaign_id = ? AND id <= ?
                        ORDER BY id ASC
                        """,
                        (self.campaign_id, int(through_turn_id)),
                    ).fetchall()
                    for row in source_turns:
                        cursor = connection.execute(
                            """
                            INSERT INTO turns(
                                campaign_id, idempotency_key, request_id, player_message,
                                narrative_response, response_json, prompt_json, metadata_json,
                                state_version, party_turn, created_at
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                target_campaign_id,
                                row["idempotency_key"],
                                row["request_id"],
                                row["player_message"],
                                row["narrative_response"],
                                row["response_json"],
                                row["prompt_json"],
                                row["metadata_json"],
                                row["state_version"],
                                row["party_turn"],
                                row["created_at"],
                            ),
                        )
                        turn_map[int(row["id"])] = int(cursor.lastrowid)
                    copied_turns = len(source_turns)

                    checks = connection.execute(
                        """
                        SELECT * FROM checks
                        WHERE campaign_id = ? AND (turn_id IS NULL OR turn_id <= ?)
                        ORDER BY id ASC
                        """,
                        (self.campaign_id, int(through_turn_id)),
                    ).fetchall()
                    for row in checks:
                        mapped_turn_id = turn_map.get(int(row["turn_id"])) if row["turn_id"] is not None else None
                        connection.execute(
                            """
                            INSERT INTO checks(
                                campaign_id, turn_id, check_id, action_type, result, roll,
                                difficulty, final_score, modifiers_json, created_at
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                target_campaign_id,
                                mapped_turn_id,
                                row["check_id"],
                                row["action_type"],
                                row["result"],
                                row["roll"],
                                row["difficulty"],
                                row["final_score"],
                                row["modifiers_json"],
                                row["created_at"],
                            ),
                        )

                    for table in ("memory_summaries", "memory_chapters"):
                        rows = connection.execute(
                            f"SELECT * FROM {table} WHERE campaign_id = ? AND to_turn_id <= ? ORDER BY id ASC",
                            (self.campaign_id, int(through_turn_id)),
                        ).fetchall()
                        for row in rows:
                            mapped_from = turn_map.get(int(row["from_turn_id"]))
                            mapped_to = turn_map.get(int(row["to_turn_id"]))
                            if mapped_from is None or mapped_to is None:
                                continue
                            connection.execute(
                                f"""
                                INSERT INTO {table}(
                                    campaign_id, from_turn_id, to_turn_id, state_version,
                                    summary_text, key_facts_json, open_threads_json,
                                    relationship_changes_json, player_promises_json,
                                    npc_obligations_json, created_at, model
                                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    target_campaign_id,
                                    mapped_from,
                                    mapped_to,
                                    row["state_version"],
                                    row["summary_text"],
                                    row["key_facts_json"],
                                    row["open_threads_json"],
                                    row["relationship_changes_json"],
                                    row["player_promises_json"],
                                    row["npc_obligations_json"],
                                    row["created_at"],
                                    row["model"],
                                ),
                            )

                    story_row = connection.execute(
                        """
                        SELECT * FROM rp_story_memory_snapshots
                        WHERE campaign_id = ? AND to_turn_id <= ?
                        ORDER BY to_turn_id DESC, revision DESC LIMIT 1
                        """,
                        (self.campaign_id, int(through_turn_id)),
                    ).fetchone()
                    if story_row is not None:
                        mapped_from = turn_map.get(int(story_row["from_turn_id"]))
                        mapped_to = turn_map.get(int(story_row["to_turn_id"]))
                        if mapped_from is not None and mapped_to is not None:
                            connection.execute(
                                """
                                INSERT INTO rp_story_memory_snapshots(
                                    campaign_id, revision, from_turn_id, to_turn_id,
                                    state_version, memory_json, created_at, model
                                ) VALUES(?, 1, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    target_campaign_id,
                                    mapped_from,
                                    mapped_to,
                                    story_row["state_version"],
                                    story_row["memory_json"],
                                    story_row["created_at"],
                                    story_row["model"],
                                ),
                            )

                    journals = connection.execute(
                        """
                        SELECT * FROM journal_entries
                        WHERE campaign_id = ? AND to_turn_id <= ? ORDER BY id ASC
                        """,
                        (self.campaign_id, int(through_turn_id)),
                    ).fetchall()
                    for row in journals:
                        mapped_from = turn_map.get(int(row["from_turn_id"]))
                        mapped_to = turn_map.get(int(row["to_turn_id"]))
                        if mapped_from is None or mapped_to is None:
                            continue
                        connection.execute(
                            """
                            INSERT INTO journal_entries(
                                campaign_id, from_turn_id, to_turn_id, state_version, title,
                                recap_text, important_changes_json, created_at, model
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                target_campaign_id,
                                mapped_from,
                                mapped_to,
                                row["state_version"],
                                row["title"],
                                row["recap_text"],
                                row["important_changes_json"],
                                row["created_at"],
                                row["model"],
                            ),
                        )

                lore_card_ids = [int(value) for value in checkpoint.get("lore_card_ids", [])]
                for lore_card_id in lore_card_ids:
                    row = connection.execute(
                        "SELECT * FROM lore_cards WHERE id = ? AND campaign_id = ?",
                        (lore_card_id, self.campaign_id),
                    ).fetchone()
                    if row is None:
                        continue
                    source_turn_ids = [turn_map.get(int(value)) for value in self.json_list(row["source_turn_ids_json"])]
                    source_turn_ids = [value for value in source_turn_ids if value is not None]
                    connection.execute(
                        """
                        INSERT INTO lore_cards(
                            campaign_id, title, content, keywords_json, always_on, enabled,
                            archived, source_turn_ids_json, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            target_campaign_id,
                            row["title"],
                            row["content"],
                            row["keywords_json"],
                            row["always_on"],
                            row["enabled"],
                            row["archived"],
                            json.dumps(source_turn_ids),
                            row["created_at"],
                            row["updated_at"],
                        ),
                    )

                branch_through_turn_id = turn_map.get(int(through_turn_id)) if through_turn_id is not None else None
                memory_coverage = checkpoint.get("memory_coverage_turn_id")
                branch_memory_coverage = turn_map.get(int(memory_coverage)) if memory_coverage is not None else None
                connection.execute(
                    """
                    INSERT INTO memory_checkpoints(
                        campaign_id, label, through_turn_id, state_version, memory_coverage_turn_id,
                        state_json, lore_card_ids_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_campaign_id,
                        f"Branch base: {checkpoint['label']}",
                        branch_through_turn_id,
                        int(checkpoint["state_version"]),
                        branch_memory_coverage,
                        json.dumps(state, ensure_ascii=False),
                        "[]",
                        now_ts(),
                    ),
                )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = target_path.with_suffix(target_path.suffix + ".tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp, target_path)
        except Exception:
            with self.connect() as connection:
                for table in (
                    "turns", "turn_requests", "checks", "state_patches", "state_versions",
                    "audit_events", "memory_summaries", "memory_chapters", "rp_story_memory_snapshots", "journal_entries",
                    "lore_cards", "memory_checkpoints", "service_jobs", "training_runtime_snapshots",
                ):
                    connection.execute(f"DELETE FROM {table} WHERE campaign_id = ?", (target_campaign_id,))
                connection.execute("DELETE FROM campaigns WHERE id = ?", (target_campaign_id,))
            raise

        return {
            "source_campaign_id": self.campaign_id,
            "target_campaign_id": target_campaign_id,
            "checkpoint_id": int(checkpoint_id),
            "copied_turns": copied_turns,
            "state_version": int(checkpoint["state_version"]),
        }

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

    def training_runtime_snapshot(self, candidate: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Return the immutable party/branch training contract, creating it once when needed."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT contract_json FROM training_runtime_snapshots WHERE campaign_id = ?",
                (self.campaign_id,),
            ).fetchone()
            if row is not None:
                return json.loads(row["contract_json"])
            if candidate is None:
                return None
            contract_hash = str(candidate.get("contract_hash") or "")
            if not contract_hash:
                raise ValueError("training runtime snapshot requires contract_hash")
            connection.execute(
                """
                INSERT OR IGNORE INTO training_runtime_snapshots(
                    campaign_id, contract_hash, contract_json, created_at
                )
                VALUES(?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    contract_hash,
                    json.dumps(candidate, ensure_ascii=False),
                    now_ts(),
                ),
            )
            row = connection.execute(
                "SELECT contract_json FROM training_runtime_snapshots WHERE campaign_id = ?",
                (self.campaign_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("failed to create training runtime snapshot")
        return json.loads(row["contract_json"])

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
                SELECT t.id, t.request_id, t.player_message, t.narrative_response,
                       t.state_version, t.created_at,
                       COALESCE(f.rating, 0) AS player_rating_value,
                       COALESCE(f.liked, 0) AS player_liked
                FROM turns t
                LEFT JOIN turn_feedback f
                  ON f.campaign_id = t.campaign_id AND f.turn_id = t.id
                WHERE t.campaign_id = ?
                ORDER BY t.id DESC
                LIMIT ?
                """,
                (self.campaign_id, limit),
            ).fetchall()
        turns = [dict(row) for row in reversed(rows)]
        for turn in turns:
            rating_value = int(turn.pop("player_rating_value"))
            turn["player_rating"] = {1: "positive", -1: "negative"}.get(rating_value, "none")
            turn["player_liked"] = bool(turn["player_liked"])
            turn["player_disliked"] = rating_value == -1
            turn["artifacts"] = self.training_artifacts_for_turn(int(turn["id"]))
            turn["interaction_status"] = self.training_artifact_event_status_for_turn(int(turn["id"]))
        return turns

    def set_turn_feedback(self, turn_id: int, *, rating: str, source_ui: str) -> dict[str, Any]:
        if source_ui not in {"light-gui", "showroom"}:
            raise ValueError("unsupported turn feedback source")
        rating_values = {"none": 0, "positive": 1, "negative": -1}
        if rating not in rating_values:
            raise ValueError("unsupported turn feedback rating")
        rating_value = rating_values[rating]
        timestamp = now_ts()
        with self.connect() as connection:
            turn = connection.execute(
                "SELECT id, player_message FROM turns WHERE id = ? AND campaign_id = ?",
                (int(turn_id), self.campaign_id),
            ).fetchone()
            if turn is None:
                raise ValueError(f"turn not found: {turn_id}")
            if str(turn["player_message"]).startswith("[AUTO_START]"):
                raise ValueError("opening scene has no player and assistant pair to rate")
            connection.execute(
                """
                INSERT INTO turn_feedback(
                    campaign_id, turn_id, liked, rating, source_ui, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, turn_id) DO UPDATE SET
                    liked = excluded.liked,
                    rating = excluded.rating,
                    source_ui = excluded.source_ui,
                    updated_at = excluded.updated_at
                """,
                (
                    self.campaign_id,
                    int(turn_id),
                    int(rating_value == 1),
                    rating_value,
                    source_ui,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_events(campaign_id, request_id, event_type, event_json, created_at)
                VALUES(?, NULL, 'turn_feedback_updated', ?, ?)
                """,
                (
                    self.campaign_id,
                    json.dumps(
                        {
                            "turn_id": int(turn_id),
                            "rating": rating,
                            "liked": rating_value == 1,
                            "disliked": rating_value == -1,
                            "source_ui": source_ui,
                        },
                        ensure_ascii=False,
                    ),
                    timestamp,
                ),
            )
            row = connection.execute(
                """
                SELECT campaign_id, turn_id, liked, rating, source_ui, created_at, updated_at
                FROM turn_feedback WHERE campaign_id = ? AND turn_id = ?
                """,
                (self.campaign_id, int(turn_id)),
            ).fetchone()
        feedback = dict(row)
        rating_value = int(feedback.pop("rating"))
        feedback["rating"] = {1: "positive", -1: "negative"}.get(rating_value, "none")
        feedback["liked"] = bool(feedback["liked"])
        feedback["disliked"] = rating_value == -1
        return feedback

    def has_running_turn_request(self) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM turn_requests WHERE campaign_id = ? AND status = 'running' LIMIT 1",
                (self.campaign_id,),
            ).fetchone()
        return row is not None

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
            SELECT id, request_id, player_message, narrative_response, state_version, party_turn, created_at
            FROM turns
            WHERE campaign_id = ? AND id > ? AND excluded_from_memory = 0
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

    def search_archived_turns(self, query: str, through_turn_id: int, limit: int = 3) -> list[dict[str, Any]]:
        """Retrieve only already-compressed turns; the raw tail remains sequential in the prompt."""
        return [
            {
                key: value
                for key, value in match.items()
                if key not in {
                    "retrieval_score",
                    "lexical_score",
                    "stem_hits",
                    "fuzzy_score",
                    "matched_terms",
                    "match_mode",
                }
            }
            for match in self.explain_archived_retrieval(query, through_turn_id, limit)
        ]

    def explain_archived_retrieval(self, query: str, through_turn_id: int, limit: int = 3) -> list[dict[str, Any]]:
        """Return local lexical retrieval evidence for the prompt inspector without changing prompt authority."""
        terms = archive_search_terms(query)
        if not terms or through_turn_id <= 0 or limit <= 0:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, player_message, narrative_response, state_version
                FROM turns
                WHERE campaign_id = ? AND id <= ?
                ORDER BY id DESC
                """,
                (self.campaign_id, through_turn_id),
            ).fetchall()
        query_stems = {archive_stem(term) for term in terms}
        query_ngrams = archive_ngrams(terms)
        ranked: list[tuple[float, dict[str, Any]]] = []
        newest_turn_id = int(rows[0]["id"]) if rows else through_turn_id
        for row in rows:
            item = dict(row)
            text = f"{item['player_message']}\n{item['narrative_response']}".lower()
            text_tokens = archive_word_tokens(text)
            text_stems = {archive_stem(token) for token in text_tokens}
            matched_terms = [term for term in terms if term in text]
            lexical_score = sum(text.count(term) for term in matched_terms)
            stem_hits = len(query_stems & text_stems)
            text_ngrams = archive_ngrams(text_tokens)
            fuzzy_score = len(query_ngrams & text_ngrams) / max(len(query_ngrams), 1)
            if not lexical_score and not stem_hits and fuzzy_score < 0.45:
                continue
            recency_bonus = 0.25 / (1 + max(newest_turn_id - int(item["id"]), 0))
            score = round((lexical_score * 4) + (stem_hits * 2) + (fuzzy_score * 3) + recency_bonus, 3)
            if score:
                item["retrieval_score"] = score
                item["lexical_score"] = lexical_score
                item["stem_hits"] = stem_hits
                item["fuzzy_score"] = round(fuzzy_score, 3)
                item["matched_terms"] = matched_terms
                item["match_mode"] = "exact+fuzzy" if lexical_score else "fuzzy"
                ranked.append((score, item))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["id"]), reverse=True)
        return [item for _, item in ranked[:limit]]

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

    def memory_chapters(self, limit: int = 10_000) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM memory_chapters
                WHERE campaign_id = ?
                ORDER BY to_turn_id ASC, id ASC
                LIMIT ?
                """,
                (self.campaign_id, limit),
            ).fetchall()
        return [self.memory_summary_from_row(row) | {"memory_type": "chapter"} for row in rows]

    def latest_rp_story_memory(self) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM rp_story_memory_snapshots
                WHERE campaign_id = ?
                ORDER BY to_turn_id DESC, revision DESC LIMIT 1
                """,
                (self.campaign_id,),
            ).fetchone()
        return self.rp_story_memory_from_row(row) if row else None

    def rp_story_memories(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM rp_story_memory_snapshots
                WHERE campaign_id = ?
                ORDER BY revision DESC LIMIT ?
                """,
                (self.campaign_id, max(limit, 1)),
            ).fetchall()
        return [self.rp_story_memory_from_row(row) for row in rows]

    def record_rp_story_memory(
        self,
        *,
        from_turn_id: int,
        to_turn_id: int,
        state_version: int,
        memory: dict[str, Any],
        model: str,
    ) -> dict[str, Any]:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM rp_story_memory_snapshots WHERE campaign_id = ? AND to_turn_id = ?",
                (self.campaign_id, int(to_turn_id)),
            ).fetchone()
            if existing is not None:
                return self.rp_story_memory_from_row(existing)
            row = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) AS revision FROM rp_story_memory_snapshots WHERE campaign_id = ?",
                (self.campaign_id,),
            ).fetchone()
            revision = int(row["revision"] or 0) + 1
            cursor = connection.execute(
                """
                INSERT INTO rp_story_memory_snapshots(
                    campaign_id, revision, from_turn_id, to_turn_id,
                    state_version, memory_json, created_at, model
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    revision,
                    int(from_turn_id),
                    int(to_turn_id),
                    int(state_version),
                    json.dumps(memory, ensure_ascii=False),
                    now_ts(),
                    model,
                ),
            )
            created = connection.execute(
                "SELECT * FROM rp_story_memory_snapshots WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
        return self.rp_story_memory_from_row(created)

    def rp_story_memory_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            memory = json.loads(row["memory_json"])
        except (TypeError, json.JSONDecodeError):
            memory = {}
        return {
            "id": row["id"],
            "campaign_id": row["campaign_id"],
            "revision": row["revision"],
            "from_turn_id": row["from_turn_id"],
            "to_turn_id": row["to_turn_id"],
            "state_version": row["state_version"],
            "memory": memory if isinstance(memory, dict) else {},
            "created_at": row["created_at"],
            "model": row["model"],
        }

    def latest_memory_coverage(self) -> dict[str, Any] | None:
        legacy = self.latest_memory_summary()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_chapters
                WHERE campaign_id = ?
                ORDER BY to_turn_id DESC, id DESC
                LIMIT 1
                """,
                (self.campaign_id,),
            ).fetchone()
        chapter = self.memory_summary_from_row(row) | {"memory_type": "chapter"} if row else None
        if chapter and (not legacy or int(chapter["to_turn_id"]) >= int(legacy["to_turn_id"])):
            return chapter
        return legacy

    def memory_for_prompt(self, max_chars: int) -> list[dict[str, Any]]:
        """Return the newest detailed chapters that fit, retaining legacy memory during migration."""
        legacy = self.latest_memory_summary()
        entries: list[dict[str, Any]] = []
        if legacy:
            entries.append(legacy | {"memory_type": "legacy_cumulative"})
        entries.extend(self.memory_chapters())
        kept: list[dict[str, Any]] = []
        used = 0
        for entry in reversed(entries):
            serialized_size = len(json.dumps(entry, ensure_ascii=False))
            if kept and used + serialized_size > max_chars:
                continue
            if not kept and serialized_size > max_chars:
                entry = dict(entry)
                entry["summary_text"] = entry["summary_text"][: max(max_chars - 2000, 0)]
                serialized_size = len(json.dumps(entry, ensure_ascii=False))
            kept.append(entry)
            used += serialized_size
        return list(reversed(kept))

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

    def record_memory_chapter(
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
                INSERT INTO memory_chapters(
                    campaign_id, from_turn_id, to_turn_id, state_version,
                    summary_text, key_facts_json, open_threads_json,
                    relationship_changes_json, player_promises_json,
                    npc_obligations_json, created_at, model
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id, from_turn_id, to_turn_id, state_version, summary_text,
                    json.dumps(key_facts, ensure_ascii=False), json.dumps(open_threads, ensure_ascii=False),
                    json.dumps(relationship_changes, ensure_ascii=False), json.dumps(player_promises, ensure_ascii=False),
                    json.dumps(npc_obligations, ensure_ascii=False), now_ts(), model,
                ),
            )
            row = connection.execute("SELECT * FROM memory_chapters WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
        return self.memory_summary_from_row(row) | {"memory_type": "chapter"}

    def delete_latest_memory_summary(self) -> dict[str, Any] | None:
        latest = self.latest_memory_summary()
        if latest is None:
            return None
        with self.connect() as connection:
            connection.execute("DELETE FROM memory_summaries WHERE id = ?", (latest["id"],))
        return latest

    def delete_latest_memory_coverage(self) -> dict[str, Any] | None:
        latest = self.latest_memory_coverage()
        if latest is None:
            return None
        table = "memory_chapters" if latest.get("memory_type") == "chapter" else "memory_summaries"
        with self.connect() as connection:
            connection.execute(f"DELETE FROM {table} WHERE id = ?", (latest["id"],))
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
            connection.execute(
                """
                UPDATE turns SET excluded_from_memory = 1
                WHERE campaign_id = ? AND state_version > ?
                """,
                (self.campaign_id, target_version),
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
                SELECT id, request_id, player_message, narrative_response,
                       state_version, party_turn, created_at
                FROM turns
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.campaign_id, request_id),
            ).fetchone()
        return dict(row) if row else None

    def begin_turn_request(self, idempotency_key: str, request_id: str) -> dict[str, Any]:
        idempotency_key = str(idempotency_key).strip()
        request_id = str(request_id).strip()
        if not idempotency_key:
            raise ValueError("idempotency_key must not be blank")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,240}", request_id):
            raise ValueError(
                "request_id must contain only letters, digits, '.', '_', ':' or '-' and be at most 240 characters"
            )
        timestamp = now_ts()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
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
            existing_request = connection.execute(
                """
                SELECT * FROM turn_requests
                WHERE campaign_id = ? AND idempotency_key = ?
                ORDER BY id DESC LIMIT 1
                """,
                (self.campaign_id, idempotency_key),
            ).fetchone()
            if existing_request is not None:
                current = self.turn_request_from_row(existing_request)
                if current["status"] == "failed":
                    connection.execute(
                        """
                        UPDATE turn_requests
                        SET status = 'running', response_json = NULL, error = NULL, updated_at = ?
                        WHERE id = ?
                        """,
                        (timestamp, int(existing_request["id"])),
                    )
                    return {
                        "acquired": True,
                        "status": "running",
                        "request_id": str(existing_request["request_id"]),
                        "idempotency_key": idempotency_key,
                        "retried": True,
                    }
                current["acquired"] = False
                return current
            request_conflict = connection.execute(
                """
                SELECT idempotency_key FROM turn_requests
                WHERE campaign_id = ? AND request_id = ?
                UNION ALL
                SELECT idempotency_key FROM turns
                WHERE campaign_id = ? AND request_id = ?
                LIMIT 1
                """,
                (self.campaign_id, request_id, self.campaign_id, request_id),
            ).fetchone()
            if request_conflict is not None:
                raise ValueError("request_id already belongs to a different idempotency_key")
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

    def record_trace_event(
        self,
        *,
        request_id: str,
        phase_key: str,
        alignment_key: str,
        lane: str,
        event_type: str,
        status: str,
        payload: dict[str, Any],
        party_turn: int | None = None,
        turn_id: int | None = None,
    ) -> dict[str, Any]:
        """Persist one request-scoped diagnostic fact without affecting gameplay."""

        request_id = str(request_id).strip()
        phase_key = str(phase_key).strip()
        alignment_key = str(alignment_key).strip()
        if not request_id or len(request_id) > 240:
            raise ValueError("invalid trace request_id")
        if not phase_key or len(phase_key) > 240:
            raise ValueError("invalid trace phase_key")
        if not alignment_key or len(alignment_key) > 160:
            raise ValueError("invalid trace alignment_key")
        if lane not in {"main", "background"}:
            raise ValueError("invalid trace lane")
        if status not in {"running", "completed", "failed", "skipped"}:
            raise ValueError("invalid trace status")
        timestamp = now_ts()
        completed_at = timestamp if status != "running" else None
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO turn_trace_events(
                    campaign_id, request_id, turn_id, party_turn, phase_key,
                    alignment_key, lane, event_type, status, payload_json,
                    created_at, completed_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, request_id, phase_key) DO UPDATE SET
                    turn_id = COALESCE(excluded.turn_id, turn_trace_events.turn_id),
                    party_turn = COALESCE(excluded.party_turn, turn_trace_events.party_turn),
                    alignment_key = excluded.alignment_key,
                    lane = excluded.lane,
                    event_type = excluded.event_type,
                    status = excluded.status,
                    payload_json = excluded.payload_json,
                    completed_at = excluded.completed_at
                """,
                (
                    self.campaign_id,
                    request_id,
                    turn_id,
                    party_turn,
                    phase_key,
                    alignment_key,
                    lane,
                    event_type,
                    status,
                    json.dumps(redact_trace_value(payload), ensure_ascii=False),
                    timestamp,
                    completed_at,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM turn_trace_events
                WHERE campaign_id = ? AND request_id = ? AND phase_key = ?
                """,
                (self.campaign_id, request_id, phase_key),
            ).fetchone()
        return dict(row) if row else {}

    def record_narrative_attempt(self, event: dict[str, Any]) -> dict[str, Any]:
        """Callback used by NarrativeClient for the exact provider attempt."""

        request_id = str(event.get("request_id") or "").strip()
        if not request_id:
            return {}
        status = str(event.get("status") or "failed")
        with self.connect() as connection:
            count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM turn_trace_events
                    WHERE campaign_id = ? AND request_id = ?
                      AND event_type = 'narrator_attempt'
                    """,
                    (self.campaign_id, request_id),
                ).fetchone()["count"]
            )
        return self.record_trace_event(
            request_id=request_id,
            phase_key=f"narrator:attempt:{count + 1}",
            alignment_key="narrator_attempt",
            lane="main",
            event_type="narrator_attempt",
            status=status if status in {"running", "completed", "failed", "skipped"} else "failed",
            payload={key: value for key, value in event.items() if key != "request_id"},
        )

    def trace_projection_snapshot(self) -> dict[str, dict[str, dict[str, Any]]]:
        """Snapshot only mutable projections whose prior values are otherwise lost."""

        definitions = {
            "character_badges": ("id",),
            "narrative_events": ("id",),
            "character_axis_state": ("character_id", "axis"),
        }
        snapshot: dict[str, dict[str, dict[str, Any]]] = {}
        with self.connect() as connection:
            available = {
                str(row["name"])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            for table, key_columns in definitions.items():
                if table not in available:
                    continue
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE campaign_id = ? ORDER BY rowid ASC",
                    (self.campaign_id,),
                ).fetchall()
                snapshot[table] = {
                    ":".join(str(row[column]) for column in key_columns): dict(row)
                    for row in rows
                }
        return snapshot

    def capture_projection_changes(
        self,
        request_id: str,
        before: dict[str, dict[str, dict[str, Any]]],
        *,
        source: str,
        reason: str,
        lane: str = "background",
    ) -> int:
        """Record exact before/after transitions for in-place projections."""

        if lane not in {"main", "background"}:
            raise ValueError("invalid projection mutation lane")
        after = self.trace_projection_snapshot()
        turn = self.get_turn_by_request_id(request_id)
        turn_id = int(turn["id"]) if turn else None
        party_turn = int(turn["party_turn"]) if turn and turn.get("party_turn") is not None else None
        changes: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []
        for store_name in sorted(set(before) | set(after)):
            before_rows = before.get(store_name, {})
            after_rows = after.get(store_name, {})
            for entity_key in sorted(set(before_rows) | set(after_rows)):
                old = before_rows.get(entity_key)
                new = after_rows.get(entity_key)
                if old != new:
                    changes.append((store_name, entity_key, old, new))
        if not changes:
            return 0
        timestamp = now_ts()
        with self.connect() as connection:
            start = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM turn_state_mutations
                    WHERE campaign_id = ? AND request_id = ?
                    """,
                    (self.campaign_id, request_id),
                ).fetchone()["count"]
            )
            for offset, (store_name, entity_key, old, new) in enumerate(changes, start=1):
                connection.execute(
                    """
                    INSERT INTO turn_state_mutations(
                        campaign_id, request_id, turn_id, party_turn, phase_key,
                        store_name, entity_key, before_json, after_json,
                        lane, source, reason, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.campaign_id,
                        request_id,
                        turn_id,
                        party_turn,
                        f"projection:{start + offset}",
                        store_name,
                        entity_key,
                        json.dumps(old, ensure_ascii=False) if old is not None else None,
                        json.dumps(new, ensure_ascii=False) if new is not None else None,
                        lane,
                        source[:80],
                        reason[:240],
                        timestamp,
                    ),
                )
        return len(changes)

    def add_trace_annotation(
        self,
        *,
        annotation_id: str,
        request_id: str,
        phase_key: str,
        author_user_id: str | None,
        body: str,
    ) -> dict[str, Any]:
        annotation_id = str(annotation_id).strip()
        request_id = str(request_id).strip()
        phase_key = str(phase_key).strip()
        body = str(body).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,160}", annotation_id):
            raise ValueError("invalid annotation_id")
        if not request_id or len(request_id) > 240:
            raise ValueError("invalid request_id")
        if not phase_key or len(phase_key) > 240:
            raise ValueError("invalid phase_key")
        if not body or len(body) > 4000:
            raise ValueError("annotation body must contain 1..4000 characters")
        turn = self.get_turn_by_request_id(request_id)
        turn_id = int(turn["id"]) if turn else None
        timestamp = now_ts()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT * FROM turn_phase_annotations WHERE campaign_id = ? AND id = ?",
                (self.campaign_id, annotation_id),
            ).fetchone()
            if existing:
                current = dict(existing)
                if (
                    current["campaign_id"] != self.campaign_id
                    or current["request_id"] != request_id
                    or current["phase_key"] != phase_key
                    or current["body"] != body
                ):
                    raise ValueError("annotation_id already belongs to different content")
                current["duplicate"] = True
                return current
            connection.execute(
                """
                INSERT INTO turn_phase_annotations(
                    id, campaign_id, request_id, turn_id, phase_key,
                    author_user_id, body, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    annotation_id,
                    self.campaign_id,
                    request_id,
                    turn_id,
                    phase_key,
                    author_user_id,
                    body,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_events(campaign_id, request_id, event_type, event_json, created_at)
                VALUES(?, ?, 'turn_trace_annotation_added', ?, ?)
                """,
                (
                    self.campaign_id,
                    request_id,
                    json.dumps(
                        {
                            "annotation_id": annotation_id,
                            "turn_id": turn_id,
                            "phase_key": phase_key,
                            "author_user_id": author_user_id,
                        },
                        ensure_ascii=False,
                    ),
                    timestamp,
                ),
            )
        return {
            "id": annotation_id,
            "campaign_id": self.campaign_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "phase_key": phase_key,
            "author_user_id": author_user_id,
            "body": body,
            "created_at": timestamp,
            "duplicate": False,
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
        metadata: dict[str, Any] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        consumed_artifact_event_ids: list[int] | None = None,
        workspace_files: list[dict[str, Any]] | None = None,
        consumed_workspace_event_ids: list[int] | None = None,
        party_turn: int | None = None,
    ) -> int:
        if party_turn is None:
            party_turn = int(self.get_state().get("meta", {}).get("turn", 0))
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO turns(
                    campaign_id, idempotency_key, request_id, player_message,
                    narrative_response, response_json, prompt_json, metadata_json,
                    state_version, party_turn, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    idempotency_key,
                    request_id,
                    player_message,
                    narrative_response,
                    json.dumps(response_json, ensure_ascii=False),
                    json.dumps(prompt_messages, ensure_ascii=False) if prompt_messages is not None else None,
                    json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
                    state_version,
                    party_turn,
                    now_ts(),
                ),
            )
            turn_id = int(cursor.lastrowid)
            for artifact in artifacts or []:
                public = artifact.get("public") if isinstance(artifact, dict) else None
                policy = artifact.get("policy") if isinstance(artifact, dict) else None
                if not isinstance(public, dict) or not isinstance(policy, dict):
                    raise ValueError("invalid training artifact persistence record")
                connection.execute(
                    """
                    INSERT INTO training_artifacts(
                        id, campaign_id, turn_id, artifact_key, artifact_revision,
                        blueprint_id, public_json, policy_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        public["artifact_id"],
                        self.campaign_id,
                        turn_id,
                        public["artifact_key"],
                        int(public["artifact_revision"]),
                        public["blueprint_id"],
                        json.dumps(public, ensure_ascii=False),
                        json.dumps(policy, ensure_ascii=False),
                        now_ts(),
                    ),
                )
            for workspace_file in workspace_files or []:
                public = workspace_file.get("public") if isinstance(workspace_file, dict) else None
                policy = workspace_file.get("policy") if isinstance(workspace_file, dict) else None
                if not isinstance(public, dict) or not isinstance(policy, dict):
                    raise ValueError("invalid training workspace persistence record")
                connection.execute(
                    """
                    INSERT INTO training_workspace_files(
                        id, campaign_id, file_key, file_revision, blueprint_id, folder_id,
                        turn_id, available_from_turn, available_until_turn, public_json,
                        policy_json, materialized_turn, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(campaign_id, file_key, file_revision) DO NOTHING
                    """,
                    (
                        public["file_id"],
                        self.campaign_id,
                        public["file_key"],
                        int(public["file_revision"]),
                        public["blueprint_id"],
                        public["folder_id"],
                        turn_id,
                        int(public["available_from_turn"]),
                        public.get("available_until_turn"),
                        json.dumps(public, ensure_ascii=False),
                        json.dumps(policy, ensure_ascii=False),
                        int(public["materialized_turn"]),
                        now_ts(),
                    ),
                )
            event_ids = [int(value) for value in consumed_artifact_event_ids or []]
            if event_ids:
                placeholders = ",".join("?" for _ in event_ids)
                connection.execute(
                    f"""
                    UPDATE training_artifact_events
                    SET consumed_turn_id = ?
                    WHERE campaign_id = ? AND consumed_turn_id IS NULL AND id IN ({placeholders})
                    """,
                    (turn_id, self.campaign_id, *event_ids),
                )
            workspace_event_ids = [int(value) for value in consumed_workspace_event_ids or []]
            if workspace_event_ids:
                placeholders = ",".join("?" for _ in workspace_event_ids)
                connection.execute(
                    f"""
                    UPDATE training_workspace_events
                    SET consumed_turn_id = ?
                    WHERE campaign_id = ? AND consumed_turn_id IS NULL AND id IN ({placeholders})
                    """,
                    (turn_id, self.campaign_id, *workspace_event_ids),
                )
        self.link_turn_diagnostics(turn_id, request_id, party_turn)
        return turn_id

    def link_turn_diagnostics(self, turn_id: int, request_id: str, party_turn: int) -> None:
        """Best-effort correlation after the authoritative turn transaction commits."""

        try:
            with self.connect() as connection:
                available = {
                    str(row["name"])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                if "turn_trace_events" in available:
                    connection.execute(
                        """
                        UPDATE turn_trace_events SET turn_id = ?, party_turn = ?
                        WHERE campaign_id = ? AND request_id = ? AND turn_id IS NULL
                        """,
                        (turn_id, party_turn, self.campaign_id, request_id),
                    )
                if "turn_state_mutations" in available:
                    connection.execute(
                        """
                        UPDATE turn_state_mutations SET turn_id = ?, party_turn = ?
                        WHERE campaign_id = ? AND request_id = ? AND turn_id IS NULL
                        """,
                        (turn_id, party_turn, self.campaign_id, request_id),
                    )
                if "turn_phase_annotations" in available:
                    connection.execute(
                        """
                        UPDATE turn_phase_annotations SET turn_id = ?
                        WHERE campaign_id = ? AND request_id = ? AND turn_id IS NULL
                        """,
                        (turn_id, self.campaign_id, request_id),
                    )
                if "service_call_log" in available:
                    service_columns = {
                        str(row["name"])
                        for row in connection.execute("PRAGMA table_info(service_call_log)")
                    }
                    if {"request_id", "turn_id", "party_turn"}.issubset(service_columns):
                        connection.execute(
                            """
                            UPDATE service_call_log
                            SET turn_id = COALESCE(turn_id, ?), party_turn = COALESCE(party_turn, ?)
                            WHERE party_id = ? AND request_id = ?
                            """,
                            (turn_id, party_turn, self.campaign_id, request_id),
                        )
        except Exception as exc:  # noqa: BLE001 - diagnostics cannot roll back a turn
            logger.warning(
                "turn_trace_link_failed request_id=%s turn_id=%s error=%s",
                request_id,
                turn_id,
                f"{type(exc).__name__}: {exc}",
            )

    def training_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, turn_id, public_json, policy_json
                FROM training_artifacts
                WHERE id = ? AND campaign_id = ?
                """,
                (artifact_id, self.campaign_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "turn_id": int(row["turn_id"]),
            "public": json.loads(row["public_json"]),
            "policy": json.loads(row["policy_json"]),
        }

    def training_artifacts_for_turn(self, turn_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT public_json FROM training_artifacts
                WHERE campaign_id = ? AND turn_id = ?
                ORDER BY artifact_key ASC
                """,
                (self.campaign_id, int(turn_id)),
            ).fetchall()
        return [json.loads(row["public_json"]) for row in rows]

    def training_artifact_event_status_for_turn(self, turn_id: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.event_type, e.created_at, e.consumed_turn_id, e.artifact_id
                FROM training_artifact_events e
                JOIN training_artifacts a ON a.id = e.artifact_id
                WHERE e.campaign_id = ? AND a.turn_id = ?
                ORDER BY e.id ASC
                """,
                (self.campaign_id, int(turn_id)),
            ).fetchall()
        return [
            {
                "event_sequence": int(row["id"]),
                "event_type": row["event_type"],
                "artifact_id": row["artifact_id"],
                "consumed": row["consumed_turn_id"] is not None,
                "created_at": int(row["created_at"]),
            }
            for row in rows
        ]

    def record_training_artifact_event(
        self,
        *,
        event_id: str,
        artifact_id: str,
        artifact_revision: int,
        event_type: str,
        filled_field_ids: list[str],
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = now_ts()
        payload_fields = json.dumps(list(filled_field_ids), ensure_ascii=False)
        payload_evidence = json.dumps(evidence, ensure_ascii=False)
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, artifact_id, artifact_revision, event_type, filled_field_ids_json
                FROM training_artifact_events
                WHERE campaign_id = ? AND event_id = ?
                """,
                (self.campaign_id, event_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["artifact_id"] != artifact_id
                    or int(existing["artifact_revision"]) != int(artifact_revision)
                    or existing["event_type"] != event_type
                    or existing["filled_field_ids_json"] != payload_fields
                ):
                    raise ValueError("artifact event id was already used with a different payload")
                return {"accepted": True, "event_sequence": int(existing["id"]), "duplicate": True}
            cursor = connection.execute(
                """
                INSERT INTO training_artifact_events(
                    campaign_id, event_id, artifact_id, artifact_revision, event_type,
                    filled_field_ids_json, evidence_json, consumed_turn_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    self.campaign_id,
                    event_id,
                    artifact_id,
                    int(artifact_revision),
                    event_type,
                    payload_fields,
                    payload_evidence,
                    timestamp,
                ),
            )
        return {"accepted": True, "event_sequence": int(cursor.lastrowid), "duplicate": False}

    def unconsumed_training_artifact_evidence(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.event_id, e.artifact_id, e.event_type, e.evidence_json,
                       a.artifact_key, a.blueprint_id
                FROM training_artifact_events e
                JOIN training_artifacts a ON a.id = e.artifact_id
                WHERE e.campaign_id = ? AND e.consumed_turn_id IS NULL
                ORDER BY e.id ASC
                """,
                (self.campaign_id,),
            ).fetchall()
            consumed_rules = {
                str(json.loads(row["evidence_json"] or "{}").get("score_rule_id") or "")
                for row in connection.execute(
                    """
                    SELECT evidence_json FROM training_artifact_events
                    WHERE campaign_id = ? AND consumed_turn_id IS NOT NULL
                    """,
                    (self.campaign_id,),
                ).fetchall()
            }
        evidence_items: list[dict[str, Any]] = []
        seen_pending_rules: set[str] = set()
        for row in rows:
            evidence = json.loads(row["evidence_json"] or "{}")
            rule_id = str(evidence.get("score_rule_id") or "")
            score_once = bool(evidence.get("score_once", True))
            eligible = not score_once or not rule_id or (rule_id not in consumed_rules and rule_id not in seen_pending_rules)
            if rule_id:
                seen_pending_rules.add(rule_id)
            evidence_items.append(
                {
                    "event_sequence": int(row["id"]),
                    "event_id": row["event_id"],
                    "artifact_id": row["artifact_id"],
                    "artifact_key": row["artifact_key"],
                    "blueprint_id": row["blueprint_id"],
                    "event_type": row["event_type"],
                    "evidence": str(evidence.get("evidence") or ""),
                    "score_rule_id": rule_id,
                    "score_once": score_once,
                    "score_eligible": eligible,
                    "decision_result": str(evidence.get("decision_result") or "neutral"),
                }
            )
        return evidence_items

    def training_workspace_file(self, file_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, public_json, policy_json
                FROM training_workspace_files
                WHERE id = ? AND campaign_id = ?
                """,
                (file_id, self.campaign_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "public": json.loads(row["public_json"]),
            "policy": json.loads(row["policy_json"]),
        }

    def training_workspace_snapshot(self, current_turn: int) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT public_json
                FROM training_workspace_files
                WHERE campaign_id = ?
                  AND available_from_turn <= ?
                  AND (available_until_turn IS NULL OR available_until_turn >= ?)
                ORDER BY folder_id, file_key, file_revision DESC
                """,
                (self.campaign_id, int(current_turn), int(current_turn)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            public = json.loads(row["public_json"])
            file_key = str(public.get("file_key") or "")
            if file_key and file_key not in seen:
                seen.add(file_key)
                result.append(public)
        return result

    def record_training_workspace_event(
        self,
        *,
        event_id: str,
        file_id: str,
        file_revision: int,
        event_type: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = now_ts()
        payload_evidence = json.dumps(evidence, ensure_ascii=False)
        with self.connect() as connection:
            existing = connection.execute(
                """
                SELECT id, file_id, file_revision, event_type
                FROM training_workspace_events
                WHERE campaign_id = ? AND event_id = ?
                """,
                (self.campaign_id, event_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["file_id"] != file_id
                    or int(existing["file_revision"]) != int(file_revision)
                    or existing["event_type"] != event_type
                ):
                    raise ValueError("workspace event id was already used with a different payload")
                return {"accepted": True, "event_sequence": int(existing["id"]), "duplicate": True}
            cursor = connection.execute(
                """
                INSERT INTO training_workspace_events(
                    campaign_id, event_id, file_id, file_revision, event_type,
                    evidence_json, consumed_turn_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    self.campaign_id,
                    event_id,
                    file_id,
                    int(file_revision),
                    event_type,
                    payload_evidence,
                    timestamp,
                ),
            )
        return {"accepted": True, "event_sequence": int(cursor.lastrowid), "duplicate": False}

    def unconsumed_training_workspace_evidence(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT e.id, e.event_id, e.file_id, e.event_type, e.evidence_json,
                       f.file_key, f.blueprint_id
                FROM training_workspace_events e
                JOIN training_workspace_files f ON f.id = e.file_id
                WHERE e.campaign_id = ? AND e.consumed_turn_id IS NULL
                ORDER BY e.id ASC
                """,
                (self.campaign_id,),
            ).fetchall()
            consumed_rules = {
                str(json.loads(row["evidence_json"] or "{}").get("score_rule_id") or "")
                for row in connection.execute(
                    """
                    SELECT evidence_json FROM training_workspace_events
                    WHERE campaign_id = ? AND consumed_turn_id IS NOT NULL
                    """,
                    (self.campaign_id,),
                ).fetchall()
            }
        result: list[dict[str, Any]] = []
        pending_rules: set[str] = set()
        for row in rows:
            evidence = json.loads(row["evidence_json"] or "{}")
            rule_id = str(evidence.get("score_rule_id") or "")
            score_once = bool(evidence.get("score_once", True))
            eligible = not score_once or not rule_id or (rule_id not in consumed_rules and rule_id not in pending_rules)
            if rule_id:
                pending_rules.add(rule_id)
            result.append(
                {
                    "event_sequence": int(row["id"]),
                    "event_id": row["event_id"],
                    "artifact_id": row["file_id"],
                    "artifact_key": row["file_key"],
                    "blueprint_id": row["blueprint_id"],
                    "event_type": row["event_type"],
                    "evidence": str(evidence.get("evidence") or ""),
                    "score_rule_id": rule_id,
                    "score_once": score_once,
                    "score_eligible": eligible,
                    "decision_result": str(evidence.get("decision_result") or "neutral"),
                }
            )
        return result

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
