"""Party-scoped persistence for derived RP relationship pressure."""

from __future__ import annotations

import json
from typing import Any

from app.services.state_store import StateStore, now_ts


class RelationshipStore:
    """Store relationship causes and their deterministic derived state."""

    def __init__(self, state_store: StateStore, model: dict[str, Any]):
        self.state_store = state_store
        self.model = model
        self.campaign_id = state_store.campaign_id

    def add_cause(
        self,
        *,
        character_id: str,
        axis: str,
        event_id: str,
        weight: int,
        turn_id: int,
        party_turn: int,
        expires_turn: int | None,
        evidence: str,
        source: str,
    ) -> bool:
        with self.state_store.connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO relationship_causes(
                    campaign_id, character_id, axis, event_id, weight, turn_id,
                    party_turn, expires_turn, evidence, source, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.campaign_id,
                    character_id,
                    axis,
                    event_id,
                    weight,
                    turn_id,
                    party_turn,
                    expires_turn,
                    evidence,
                    source,
                    now_ts(),
                ),
            )
        return cursor.rowcount == 1

    def value(self, character_id: str, axis: str, at_party_turn: int) -> int:
        with self.state_store.connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(cause.weight), 0) AS value
                FROM relationship_causes AS cause
                WHERE cause.campaign_id = ?
                  AND cause.character_id = ?
                  AND cause.axis = ?
                  AND cause.party_turn <= ?
                  AND (cause.expires_turn IS NULL OR cause.expires_turn > ?)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM turns AS turn
                      WHERE turn.campaign_id = cause.campaign_id
                        AND turn.id = cause.turn_id
                        AND turn.excluded_from_memory = 1
                  )
                """,
                (self.campaign_id, character_id, axis, at_party_turn, at_party_turn),
            ).fetchone()
        return max(-100, min(100, int(row["value"])))

    def cause_rows(self, character_id: str, axis: str, at_party_turn: int) -> list[dict[str, Any]]:
        with self.state_store.connect() as connection:
            rows = connection.execute(
                """
                SELECT cause.*
                FROM relationship_causes AS cause
                WHERE cause.campaign_id = ?
                  AND cause.character_id = ?
                  AND cause.axis = ?
                  AND cause.party_turn <= ?
                  AND (cause.expires_turn IS NULL OR cause.expires_turn > ?)
                  AND NOT EXISTS (
                      SELECT 1
                      FROM turns AS turn
                      WHERE turn.campaign_id = cause.campaign_id
                        AND turn.id = cause.turn_id
                        AND turn.excluded_from_memory = 1
                  )
                ORDER BY cause.party_turn ASC, cause.id ASC
                """,
                (self.campaign_id, character_id, axis, at_party_turn, at_party_turn),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_badge(
        self,
        *,
        character_id: str,
        badge_kind: str,
        badge_id: str,
        party_turn: int,
        payload: dict[str, Any] | None = None,
    ) -> bool:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else None
        with self.state_store.connect() as connection:
            existing = connection.execute(
                """
                SELECT active, payload_json
                FROM character_badges
                WHERE campaign_id = ? AND character_id = ? AND badge_kind = ? AND badge_id = ?
                """,
                (self.campaign_id, character_id, badge_kind, badge_id),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO character_badges(
                        campaign_id, character_id, badge_kind, badge_id, party_turn,
                        active, payload_json, created_at
                    ) VALUES(?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        self.campaign_id,
                        character_id,
                        badge_kind,
                        badge_id,
                        party_turn,
                        payload_json,
                        now_ts(),
                    ),
                )
                return True
            if int(existing["active"]) == 1 and existing["payload_json"] == payload_json:
                return False
            connection.execute(
                """
                UPDATE character_badges
                SET active = 1, party_turn = ?, payload_json = ?
                WHERE campaign_id = ? AND character_id = ? AND badge_kind = ? AND badge_id = ?
                """,
                (party_turn, payload_json, self.campaign_id, character_id, badge_kind, badge_id),
            )
        return True

    def active_badges(
        self,
        character_id: str | None = None,
        badge_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM character_badges WHERE campaign_id = ? AND active = 1"
        params: list[Any] = [self.campaign_id]
        if character_id is not None:
            query += " AND character_id = ?"
            params.append(character_id)
        if badge_kind is not None:
            query += " AND badge_kind = ?"
            params.append(badge_kind)
        query += " ORDER BY party_turn ASC, id ASC"
        with self.state_store.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._badge_row(row) for row in rows]

    def deactivate_badge(self, *, character_id: str, badge_kind: str, badge_id: str) -> bool:
        with self.state_store.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE character_badges
                SET active = 0
                WHERE campaign_id = ? AND character_id = ? AND badge_kind = ?
                  AND badge_id = ? AND active = 1
                """,
                (self.campaign_id, character_id, badge_kind, badge_id),
            )
        return cursor.rowcount == 1

    def backfill_active_event_deadlines(self, clocks: dict[str, Any]) -> int:
        """Repair deadlines for active legacy events exactly once."""
        with self.state_store.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, event_id, opened_turn
                FROM narrative_events
                WHERE campaign_id = ? AND status = 'active' AND due_turn IS NULL
                ORDER BY opened_turn ASC, id ASC
                """,
                (self.campaign_id,),
            ).fetchall()
            updated = 0
            for row in rows:
                event_id = str(row["event_id"])
                clock = clocks.get(event_id)
                if isinstance(clock, bool) or not isinstance(clock, int) or clock <= 0:
                    raise ValueError(f"relationship clock is missing or invalid: {event_id}")
                due_turn = int(row["opened_turn"]) + clock
                cursor = connection.execute(
                    """
                    UPDATE narrative_events
                    SET due_turn = ?
                    WHERE id = ? AND campaign_id = ? AND status = 'active' AND due_turn IS NULL
                    """,
                    (due_turn, int(row["id"]), self.campaign_id),
                )
                updated += cursor.rowcount
        return updated

    def active_events(self, at_party_turn: int) -> list[dict[str, Any]]:
        with self.state_store.connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM narrative_events
                WHERE campaign_id = ? AND status = 'active' AND opened_turn <= ?
                ORDER BY opened_turn ASC, id ASC
                """,
                (self.campaign_id, at_party_turn),
            ).fetchall()
        return [self._event_row(row) for row in rows]

    def event_rows(
        self,
        character_id: str | None = None,
        event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM narrative_events WHERE campaign_id = ?"
        params: list[Any] = [self.campaign_id]
        if character_id is not None:
            query += " AND character_id = ?"
            params.append(character_id)
        if event_id is not None:
            query += " AND event_id = ?"
            params.append(event_id)
        query += " ORDER BY opened_turn ASC, id ASC"
        with self.state_store.connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._event_row(row) for row in rows]

    def open_event(
        self,
        *,
        character_id: str,
        axis: str,
        event_id: str,
        opened_turn: int,
        due_turn: int,
        payload: dict[str, Any] | None = None,
    ) -> int | None:
        if isinstance(due_turn, bool) or not isinstance(due_turn, int) or due_turn <= opened_turn:
            raise ValueError("relationship event requires a finite due_turn after opened_turn")
        with self.state_store.connect() as connection:
            existing = connection.execute(
                """
                SELECT id
                FROM narrative_events
                WHERE campaign_id = ? AND character_id = ? AND axis = ?
                  AND event_id = ? AND status = 'active'
                ORDER BY id DESC LIMIT 1
                """,
                (self.campaign_id, character_id, axis, event_id),
            ).fetchone()
            if existing is not None:
                return None
            cursor = connection.execute(
                """
                INSERT INTO narrative_events(
                    campaign_id, character_id, axis, event_id, status, opened_turn,
                    due_turn, payload_json, resolution, resolved_turn, created_at
                ) VALUES(?, ?, ?, ?, 'active', ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    self.campaign_id,
                    character_id,
                    axis,
                    event_id,
                    opened_turn,
                    due_turn,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    now_ts(),
                ),
            )
        return int(cursor.lastrowid)

    def resolve_event(
        self,
        event_row_id: int,
        *,
        status: str,
        resolution: str | None,
        resolved_turn: int,
        resolved_turn_id: int | None = None,
    ) -> bool:
        with self.state_store.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE narrative_events
                SET status = ?, resolution = ?, resolved_turn = ?, resolved_turn_id = ?
                WHERE id = ? AND campaign_id = ? AND status = 'active'
                  AND (
                      ? IS NULL
                      OR EXISTS (
                          SELECT 1 FROM turns
                          WHERE id = ? AND campaign_id = ? AND excluded_from_memory = 0
                      )
                  )
                """,
                (
                    status,
                    resolution,
                    resolved_turn,
                    resolved_turn_id,
                    event_row_id,
                    self.campaign_id,
                    resolved_turn_id,
                    resolved_turn_id,
                    self.campaign_id,
                ),
            )
        return cursor.rowcount == 1

    def axis_state(self, character_id: str, axis: str) -> dict[str, Any] | None:
        with self.state_store.connect() as connection:
            row = connection.execute(
                """
                SELECT campaign_id, character_id, axis, band, band_since_turn, updated_at
                FROM character_axis_state
                WHERE campaign_id = ? AND character_id = ? AND axis = ?
                """,
                (self.campaign_id, character_id, axis),
            ).fetchone()
        return dict(row) if row is not None else None

    def set_axis_state(
        self,
        *,
        character_id: str,
        axis: str,
        band: str,
        band_since_turn: int,
    ) -> bool:
        with self.state_store.connect() as connection:
            existing = connection.execute(
                """
                SELECT band, band_since_turn
                FROM character_axis_state
                WHERE campaign_id = ? AND character_id = ? AND axis = ?
                """,
                (self.campaign_id, character_id, axis),
            ).fetchone()
            if (
                existing is not None
                and existing["band"] == band
                and int(existing["band_since_turn"]) == band_since_turn
            ):
                return False
            connection.execute(
                """
                INSERT INTO character_axis_state(
                    campaign_id, character_id, axis, band, band_since_turn, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, character_id, axis) DO UPDATE SET
                    band = excluded.band,
                    band_since_turn = excluded.band_since_turn,
                    updated_at = excluded.updated_at
                """,
                (self.campaign_id, character_id, axis, band, band_since_turn, now_ts()),
            )
        return True

    def pressure_rows(self, at_party_turn: int) -> list[dict[str, Any]]:
        with self.state_store.connect() as connection:
            rows = connection.execute(
                """
                SELECT campaign_id, character_id, axis, band, band_since_turn, updated_at
                FROM character_axis_state
                WHERE campaign_id = ? AND band_since_turn <= ?
                ORDER BY character_id ASC, axis ASC
                """,
                (self.campaign_id, at_party_turn),
            ).fetchall()
        events_by_character: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for event in self.active_events(at_party_turn):
            key = (str(event["character_id"]), str(event["axis"]))
            events_by_character.setdefault(key, []).append(event)
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            key = (str(row["character_id"]), str(row["axis"]))
            item["events"] = events_by_character.get(key, [])
            item["causes"] = self.cause_rows(key[0], key[1], at_party_turn)
            result.append(item)
        return result

    @staticmethod
    def _badge_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["active"] = bool(result["active"])
        result["payload"] = json.loads(result.pop("payload_json")) if result["payload_json"] else None
        return result

    @staticmethod
    def _event_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result
