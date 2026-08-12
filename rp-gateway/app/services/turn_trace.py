"""Read-time assembly of request-scoped turn diagnostics for Light GUI."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.services.state_store import StateStore


TRACE_SCHEMA_VERSION = "rp-gateway.turn-trace.v1"


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return default if default is not None else value


def _phase_status(value: str) -> str:
    if value in {"completed", "failed", "running", "skipped"}:
        return value
    if value in {"pending", "retry", "retrying", "queued"}:
        return "running"
    return "failed" if value in {"error", "dead"} else "completed"


def json_changes(before: Any, after: Any, path: str = "$") -> list[dict[str, Any]]:
    """Return an exact, deterministic leaf-oriented JSON before/after diff."""

    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child_path = f"{path}.{key}"
            if key not in before:
                changes.append({"path": child_path, "operation": "add", "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child_path, "operation": "remove", "before": before[key], "after": None})
            else:
                changes.extend(json_changes(before[key], after[key], child_path))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        size = max(len(before), len(after))
        for index in range(size):
            child_path = f"{path}[{index}]"
            if index >= len(before):
                changes.append({"path": child_path, "operation": "add", "before": None, "after": after[index]})
            elif index >= len(after):
                changes.append({"path": child_path, "operation": "remove", "before": before[index], "after": None})
            else:
                changes.extend(json_changes(before[index], after[index], child_path))
        return changes
    return [{"path": path, "operation": "replace", "before": before, "after": after}]


class TurnTraceAssembler:
    """Combine authoritative rows with the narrow non-authoritative trace journals."""

    def __init__(self, store: StateStore, party: Any, branch: dict[str, Any] | None = None):
        self.store = store
        self.party = party
        self.branch = branch

    def envelope(self) -> dict[str, Any]:
        party_revision = int(getattr(self.party, "rp_contract_revision", 0) or 0)
        branch_revision = int((self.branch or {}).get("rp_contract_revision", party_revision) or 0)
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "party": {
                "id": self.party.id,
                "title": self.party.title,
                "scenario_type": self.party.scenario_type,
                "rp_contract_version": getattr(self.party, "rp_contract_version", "rp-core.v1"),
                "rp_contract_revision": party_revision,
            },
            "branch": (
                {
                    "id": self.branch["id"],
                    "label": self.branch.get("label") or self.branch["id"],
                    "rp_contract_revision": branch_revision,
                }
                if self.branch
                else None
            ),
            "state_campaign_id": self.store.campaign_id,
        }

    def list_traces(self, *, limit: int = 30, before: str | int | None = None) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 100)
        params: list[Any] = [self.store.campaign_id, self.store.campaign_id]
        cursor_filter = ""
        if before is not None:
            cursor_created, cursor_source, cursor_id = self._decode_cursor(before)
            cursor_filter = """
                WHERE roots.created_at < ?
                   OR (roots.created_at = ? AND roots.source_rank < ?)
                   OR (roots.created_at = ? AND roots.source_rank = ? AND roots.root_id < ?)
            """
            params.extend(
                [cursor_created, cursor_created, cursor_source, cursor_created, cursor_source, cursor_id]
            )
        params.append(limit + 1)
        with self.store.connect() as connection:
            rows = connection.execute(
                f"""
                WITH roots AS (
                    SELECT id AS root_id, 1 AS source_rank,
                           request_id, idempotency_key, status, response_json, error,
                           created_at, updated_at
                    FROM turn_requests
                    WHERE campaign_id = ?
                    UNION ALL
                    SELECT turns.id AS root_id, 0 AS source_rank,
                           turns.request_id, turns.idempotency_key, 'completed',
                           turns.response_json, NULL, turns.created_at, turns.created_at
                    FROM turns
                    WHERE turns.campaign_id = ?
                      AND NOT EXISTS (
                          SELECT 1 FROM turn_requests
                          WHERE turn_requests.campaign_id = turns.campaign_id
                            AND turn_requests.request_id = turns.request_id
                      )
                )
                SELECT * FROM roots
                {cursor_filter}
                ORDER BY created_at DESC, source_rank DESC, root_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        has_more = len(rows) > limit
        selected = rows[:limit]
        traces = [self.summary(dict(row)) for row in selected]
        next_before = self._encode_cursor(selected[-1]) if has_more and selected else None
        return self.envelope() | {"next_before": next_before, "traces": traces}

    @staticmethod
    def _encode_cursor(row: sqlite3.Row) -> str:
        return f"{int(row['created_at'])}:{int(row['source_rank'])}:{int(row['root_id'])}"

    @staticmethod
    def _decode_cursor(value: str | int) -> tuple[int, int, int]:
        parts = str(value).split(":")
        if len(parts) != 3:
            raise ValueError("invalid turn trace cursor")
        try:
            created_at, source_rank, root_id = (int(part) for part in parts)
        except ValueError as exc:
            raise ValueError("invalid turn trace cursor") from exc
        if created_at < 0 or source_rank not in {0, 1} or root_id < 1:
            raise ValueError("invalid turn trace cursor")
        return created_at, source_rank, root_id

    def summary(self, root: dict[str, Any]) -> dict[str, Any]:
        request_id = str(root["request_id"])
        turn = self._turn(request_id)
        metadata = _json(turn.get("metadata_json") if turn else None, {}) or {}
        with self.store.connect() as connection:
            phase_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM turn_trace_events
                    WHERE campaign_id = ? AND request_id = ?
                    """,
                    (self.store.campaign_id, request_id),
                ).fetchone()["count"]
            )
            jobs = connection.execute(
                """
                SELECT status FROM service_jobs
                WHERE campaign_id = ? AND request_id = ?
                """,
                (self.store.campaign_id, request_id),
            ).fetchall()
        settling = any(str(row["status"]) not in {"completed", "failed", "dead"} for row in jobs)
        capture_status = "complete" if phase_count and (turn or root["status"] == "failed") else "partial"
        warnings = [] if capture_status == "complete" else ["Historical request has only partial trace capture."]
        preview = str(turn.get("player_message") or "") if turn else ""
        if not preview and root.get("error"):
            preview = str(root["error"])
        revision = metadata.get("rp_contract_revision")
        if revision is None:
            revision = (
                int(self.branch.get("rp_contract_revision", 0) or 0)
                if self.branch and (turn is None or phase_count > 0)
                else int(getattr(self.party, "rp_contract_revision", 0) or 0)
            )
        return {
            "request_id": request_id,
            "turn_id": int(turn["id"]) if turn else None,
            "party_turn": int(turn["party_turn"]) if turn and turn.get("party_turn") is not None else None,
            "status": root["status"],
            "capture_status": capture_status,
            "settling": settling,
            "created_at": int(root["created_at"]),
            "updated_at": int(root["updated_at"]),
            "preview": preview[:240],
            "phase_count": phase_count,
            "rp_contract_revision": int(revision or 0),
            "warnings": warnings,
        }

    def trace(self, request_id: str) -> dict[str, Any]:
        request_id = str(request_id).strip()
        root = self._request(request_id)
        turn = self._turn(request_id)
        if root is None and turn is None:
            raise ValueError(f"turn trace not found: {request_id}")
        if root is None and turn is not None:
            root = {
                "request_id": request_id,
                "idempotency_key": turn["idempotency_key"],
                "status": "completed",
                "response_json": turn["response_json"],
                "error": None,
                "created_at": turn["created_at"],
                "updated_at": turn["created_at"],
            }
        assert root is not None
        phases = self._recorded_phases(request_id)
        recorded = {phase["phase_key"] for phase in phases}
        omissions: list[str] = []

        if "player_input" not in recorded:
            if turn:
                phases.append(
                    self._phase(
                        "player_input",
                        "player_input",
                        "main",
                        "completed",
                        "Действие игрока",
                        input_value={"content": turn["player_message"]},
                        capture_status="complete",
                        order=10,
                    )
                )
            else:
                omissions.append("player_input_not_captured")

        if "gateway_assembly" not in recorded:
            messages = _json(turn.get("prompt_json") if turn else None, None)
            if isinstance(messages, list):
                phases.append(
                    self._phase(
                        "gateway_assembly",
                        "gateway_assembly",
                        "main",
                        "completed",
                        "Сборка prompt Gateway",
                        input_value={"messages": messages},
                        details={"source": "recorded_turn_prompt"},
                        capture_status="partial",
                        warnings=["Exact provider policies and repair payloads predate full trace capture."],
                        order=20,
                    )
                )
            else:
                omissions.append("recorded_prompt_missing")

        if turn:
            phases.extend(self._turn_authoritative_phases(turn))
        else:
            orphaned_state_phase = self._request_state_phase(request_id)
            if orphaned_state_phase is not None:
                phases.append(orphaned_state_phase)
                omissions.append("state_changed_without_committed_turn")
            phases.append(
                self._phase(
                    "request_terminal",
                    "request_terminal",
                    "main",
                    "failed" if root["status"] == "failed" else "running",
                    "Статус запроса",
                    output={"status": root["status"], "error": root.get("error")},
                    capture_status="partial" if not phases else "complete",
                    order=65,
                )
            )

        phases.extend(self._audit_phases(request_id))
        phases.extend(self._service_phases(request_id, turn))
        phases.extend(self._mutation_phases(request_id))
        annotations = self._annotations(request_id)
        for phase in phases:
            phase["annotations"] = annotations.get(phase["phase_key"], [])
        phases.sort(key=lambda item: (0 if item["lane"] == "main" else 1, item.pop("_order", 999), item["phase_key"]))

        trace_event_count = sum(1 for phase in phases if phase.get("metadata", {}).get("recorded_trace_event"))
        if not any(phase["event_type"] == "narrator_attempt" for phase in phases):
            turn_metadata = _json(turn.get("metadata_json") if turn else None, {}) or {}
            if turn_metadata.get("turn_kind") != "world_command":
                omissions.append("narrator_attempts_not_captured")
        if self.branch and trace_event_count == 0 and turn is not None:
            omissions.append("inherited_trace_unavailable")

        jobs = [phase for phase in phases if phase["lane"] == "background" and phase["event_type"] == "service_job"]
        settling = any(phase["status"] == "running" for phase in jobs)
        capture_status = "complete" if trace_event_count and not omissions else "partial"
        metadata = _json(turn.get("metadata_json") if turn else None, {}) or {}
        revision = metadata.get("rp_contract_revision")
        if revision is None:
            revision = (
                int(self.branch.get("rp_contract_revision", 0) or 0)
                if self.branch and (turn is None or trace_event_count > 0)
                else int(getattr(self.party, "rp_contract_revision", 0) or 0)
            )
        warnings = []
        if capture_status != "complete":
            warnings.append("Some historical phases were not captured by the active trace contract.")
        result = {
            "request_id": request_id,
            "turn_id": int(turn["id"]) if turn else None,
            "party_turn": int(turn["party_turn"]) if turn and turn.get("party_turn") is not None else None,
            "status": root["status"],
            "capture_status": capture_status,
            "settling": settling,
            "created_at": int(root["created_at"]),
            "updated_at": int(root["updated_at"]),
            "rp_contract_revision": int(revision or 0),
            "warnings": warnings,
            "omissions": list(dict.fromkeys(omissions)),
            "phases": phases,
        }
        return self.envelope() | {"trace": result}

    def add_annotation(
        self,
        *,
        request_id: str,
        annotation_id: str,
        phase_key: str,
        body: str,
        author_user_id: str | None,
    ) -> dict[str, Any]:
        trace = self.trace(request_id)["trace"]
        if phase_key not in {phase["phase_key"] for phase in trace["phases"]}:
            raise ValueError(f"trace phase not found: {phase_key}")
        annotation = self.store.add_trace_annotation(
            annotation_id=annotation_id,
            request_id=request_id,
            phase_key=phase_key,
            author_user_id=author_user_id,
            body=body,
        )
        duplicate = bool(annotation.pop("duplicate", False))
        return {"annotation": annotation, "duplicate": duplicate}

    def _request(self, request_id: str) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM turn_requests
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (self.store.campaign_id, request_id),
            ).fetchone()
        return dict(row) if row else None

    def _turn(self, request_id: str) -> dict[str, Any] | None:
        with self.store.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM turns
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (self.store.campaign_id, request_id),
            ).fetchone()
        return dict(row) if row else None

    def _recorded_phases(self, request_id: str) -> list[dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM turn_trace_events
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY id ASC
                """,
                (self.store.campaign_id, request_id),
            ).fetchall()
        phases: list[dict[str, Any]] = []
        for row in rows:
            payload = _json(row["payload_json"], {}) or {}
            event_metadata = {
                key: value
                for key, value in payload.items()
                if key not in {"input", "output", "details", "warnings", "capture_status", "error", "metadata"}
            }
            phase = self._phase(
                str(row["phase_key"]),
                str(row["alignment_key"]),
                str(row["lane"]),
                _phase_status(str(row["status"])),
                self._title(str(row["event_type"]), str(row["phase_key"])),
                event_type=str(row["event_type"]),
                input_value=payload.get("input"),
                output=payload.get("output"),
                details=payload.get("details"),
                metadata=event_metadata | (payload.get("metadata") or {}) | {
                    "recorded_trace_event": True,
                    "created_at": int(row["created_at"]),
                    "completed_at": row["completed_at"],
                },
                warnings=list(payload.get("warnings") or []),
                capture_status=str(payload.get("capture_status") or "complete"),
                order=self._order(str(row["event_type"]), payload),
            )
            if payload.get("error") is not None:
                phase["details"] = (phase.get("details") or {}) | {"error": payload["error"]}
            phases.append(phase)
        return phases

    def _turn_authoritative_phases(self, turn: dict[str, Any]) -> list[dict[str, Any]]:
        phases: list[dict[str, Any]] = []
        turn_id = int(turn["id"])
        metadata = _json(turn.get("metadata_json"), {}) or {}
        response = _json(turn.get("response_json"), {}) or {}
        state_phase = self._state_phase_for_turn(turn, metadata)
        if state_phase is not None:
            phases.append(state_phase)

        with self.store.connect() as connection:
            checks = connection.execute(
                "SELECT * FROM checks WHERE campaign_id = ? AND turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
            causes = connection.execute(
                "SELECT * FROM relationship_causes WHERE campaign_id = ? AND turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
            artifacts = connection.execute(
                "SELECT * FROM training_artifacts WHERE campaign_id = ? AND turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
            workspace = connection.execute(
                "SELECT * FROM training_workspace_files WHERE campaign_id = ? AND turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
            artifact_events = connection.execute(
                "SELECT * FROM training_artifact_events WHERE campaign_id = ? AND consumed_turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
            workspace_events = connection.execute(
                "SELECT * FROM training_workspace_events WHERE campaign_id = ? AND consumed_turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
            memory_chapters = connection.execute(
                "SELECT * FROM memory_chapters WHERE campaign_id = ? AND to_turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
            memory_summaries = connection.execute(
                "SELECT * FROM memory_summaries WHERE campaign_id = ? AND to_turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
            story_snapshots = connection.execute(
                "SELECT * FROM rp_story_memory_snapshots WHERE campaign_id = ? AND to_turn_id = ? ORDER BY id",
                (self.store.campaign_id, turn_id),
            ).fetchall()
        for row in checks:
            data = dict(row)
            data["modifiers"] = _json(data.pop("modifiers_json"), [])
            phases.append(
                self._phase(
                    f"check:{row['id']}",
                    "check",
                    "main",
                    "completed",
                    "Проверка правила",
                    event_type="check",
                    output=data,
                    order=35,
                )
            )
        if causes:
            phases.append(
                self._phase(
                    "relationship_causes",
                    "relationship_causes",
                    "background",
                    "completed",
                    "Причины отношений",
                    event_type="relationship_projection",
                    output={"rows": [dict(row) for row in causes]},
                    order=85,
                )
            )
        if artifacts or workspace or artifact_events or workspace_events:
            phases.append(
                self._phase(
                    "training_projection",
                    "training_projection",
                    "main",
                    "completed",
                    "Training-проекции",
                    event_type="training_projection",
                    output={
                        "artifacts": [self._decoded_row(row) for row in artifacts],
                        "workspace_files": [self._decoded_row(row) for row in workspace],
                        "consumed_artifact_events": [self._decoded_row(row) for row in artifact_events],
                        "consumed_workspace_events": [self._decoded_row(row) for row in workspace_events],
                    },
                    order=55,
                )
            )
        if memory_chapters or memory_summaries or story_snapshots:
            phases.append(
                self._phase(
                    "memory_projection",
                    "memory_projection",
                    "background",
                    "completed",
                    "Проекции памяти",
                    event_type="memory_projection",
                    output={
                        "chapters": [self._decoded_row(row) for row in memory_chapters],
                        "legacy_summaries": [self._decoded_row(row) for row in memory_summaries],
                        "rp_story_snapshots": [self._decoded_row(row) for row in story_snapshots],
                    },
                    order=86,
                )
            )

        existing_commit = any(
            phase["phase_key"] == "turn_commit" for phase in self._recorded_phases(str(turn["request_id"]))
        )
        if not existing_commit:
            phases.append(
                self._phase(
                    "turn_commit",
                    "turn_commit",
                    "main",
                    "completed",
                    "Зафиксированный ход",
                    event_type="turn_commit",
                    input_value={"player_message": turn["player_message"]},
                    output={
                        "narrative_response": turn["narrative_response"],
                        "response": response,
                    },
                    details={
                        "turn_id": turn_id,
                        "party_turn": turn.get("party_turn"),
                        "state_version": turn["state_version"],
                    },
                    metadata=metadata,
                    order=60,
                )
            )
        else:
            # The recorded commit phase owns correlation; the authoritative row is
            # still exposed by enriching it in the caller through a separate phase.
            phases.append(
                self._phase(
                    "turn_result",
                    "turn_commit",
                    "main",
                    "completed",
                    "Итоговый ответ",
                    event_type="turn_result",
                    input_value={"player_message": turn["player_message"]},
                    output={"narrative_response": turn["narrative_response"], "response": response},
                    details={"turn_id": turn_id, "state_version": turn["state_version"]},
                    metadata=metadata,
                    order=61,
                )
            )
        return phases

    def _state_phase_for_turn(
        self,
        turn: dict[str, Any],
        metadata: dict[str, Any],
    ) -> dict[str, Any] | None:
        request_id = str(turn["request_id"])
        state_version = int(turn["state_version"])
        outcome = metadata.get("outcome") if isinstance(metadata.get("outcome"), dict) else {}
        check_id = outcome.get("check_id")
        with self.store.connect() as connection:
            current = connection.execute(
                """
                SELECT state_json, reason FROM state_versions
                WHERE campaign_id = ? AND version = ?
                """,
                (self.store.campaign_id, state_version),
            ).fetchone()
            previous = connection.execute(
                """
                SELECT state_json FROM state_versions
                WHERE campaign_id = ? AND version = ?
                """,
                (self.store.campaign_id, state_version - 1),
            ).fetchone()
            patch_row = (
                connection.execute(
                    """
                    SELECT * FROM state_patches
                    WHERE campaign_id = ? AND check_id = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (self.store.campaign_id, check_id),
                ).fetchone()
                if check_id
                else None
            )
            rollback = connection.execute(
                """
                SELECT 1 FROM audit_events
                WHERE campaign_id = ? AND request_id = ? AND event_type = 'world_rollback'
                LIMIT 1
                """,
                (self.store.campaign_id, request_id),
            ).fetchone()
        if current is None:
            return None
        reason = str(current["reason"])
        correlated_reasons = {
            f"turn:{request_id}",
            f"party_start:{request_id}",
            f"world_apply:{request_id}",
        }
        if reason not in correlated_reasons and not (rollback and reason.startswith("rollback:")):
            return None
        before = _json(previous["state_json"], {}) if previous else None
        after = _json(current["state_json"], {})
        return self._phase(
            "state_delta",
            "state_delta",
            "main",
            "completed",
            "Каноническое состояние",
            event_type="state_delta",
            input_value={"before": before},
            output={"after": after},
            details={
                "changes": json_changes(before, after) if before is not None else [],
                "state_patch": self._decoded_row(patch_row) if patch_row else None,
                "state_version": state_version,
                "reason": reason,
                "committed_turn": True,
            },
            capture_status="complete" if previous else "partial",
            warnings=[] if previous else ["Previous state version is unavailable."],
            order=50,
        )

    def _request_state_phase(self, request_id: str) -> dict[str, Any] | None:
        """Expose an authoritative state write even when the request never committed a turn."""

        with self.store.connect() as connection:
            current = connection.execute(
                """
                SELECT version, state_json, reason FROM state_versions
                WHERE campaign_id = ? AND reason = ?
                ORDER BY version DESC LIMIT 1
                """,
                (self.store.campaign_id, f"turn:{request_id}"),
            ).fetchone()
            if current is None:
                return None
            previous = connection.execute(
                """
                SELECT state_json FROM state_versions
                WHERE campaign_id = ? AND version = ?
                """,
                (self.store.campaign_id, int(current["version"]) - 1),
            ).fetchone()
        before = _json(previous["state_json"], {}) if previous else None
        after = _json(current["state_json"], {})
        warnings = ["State changed even though the request has no committed turn."]
        if previous is None:
            warnings.append("Previous state version is unavailable.")
        return self._phase(
            "state_delta",
            "state_delta",
            "main",
            "completed",
            "Каноническое состояние",
            event_type="state_delta",
            input_value={"before": before},
            output={"after": after},
            details={
                "changes": json_changes(before, after) if before is not None else [],
                "state_version": int(current["version"]),
                "reason": current["reason"],
                "committed_turn": False,
            },
            capture_status="complete" if previous else "partial",
            warnings=warnings,
            order=50,
        )

    def _audit_phases(self, request_id: str) -> list[dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM audit_events
                WHERE campaign_id = ? AND request_id = ?
                  AND event_type != 'turn_trace_annotation_added'
                ORDER BY id ASC
                """,
                (self.store.campaign_id, request_id),
            ).fetchall()
        return [
            self._phase(
                f"audit:{row['event_type']}:{row['id']}",
                f"audit:{row['event_type']}",
                "main",
                "failed" if any(token in str(row["event_type"]) for token in ("failed", "error", "timeout")) else "completed",
                f"Gateway: {row['event_type']}",
                event_type="audit",
                output=_json(row["event_json"], {}),
                metadata={"audit_event_id": int(row["id"]), "created_at": int(row["created_at"])},
                order=45,
            )
            for row in rows
        ]

    def _service_phases(self, request_id: str, turn: dict[str, Any] | None) -> list[dict[str, Any]]:
        phases: list[dict[str, Any]] = []
        with self.store.connect() as connection:
            jobs = connection.execute(
                """
                SELECT * FROM service_jobs
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY id ASC
                """,
                (self.store.campaign_id, request_id),
            ).fetchall()
            tables = {
                str(row["name"])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            calls: list[sqlite3.Row] = []
            service_columns: set[str] = set()
            if "service_call_log" in tables:
                service_columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(service_call_log)")
                }
                if "request_id" in service_columns:
                    if turn is not None:
                        calls = connection.execute(
                            """
                            SELECT * FROM service_call_log
                            WHERE party_id = ?
                              AND (request_id = ? OR (request_id IS NULL AND turn_id = ?))
                            ORDER BY id ASC
                            """,
                            (self.store.campaign_id, request_id, int(turn["id"])),
                        ).fetchall()
                    else:
                        calls = connection.execute(
                            """
                            SELECT * FROM service_call_log
                            WHERE party_id = ? AND request_id = ?
                            ORDER BY id ASC
                            """,
                            (self.store.campaign_id, request_id),
                        ).fetchall()
                elif turn is not None:
                    calls = connection.execute(
                        """
                        SELECT * FROM service_call_log
                        WHERE party_id = ? AND turn_id = ?
                        ORDER BY id ASC
                        """,
                        (self.store.campaign_id, int(turn["id"])),
                    ).fetchall()
        for row in jobs:
            data = dict(row)
            phases.append(
                self._phase(
                    f"service_job:{row['id']}",
                    f"service_job:{row['job_type']}",
                    "background",
                    _phase_status(str(row["status"])),
                    f"Фоновая задача: {row['job_type']}",
                    event_type="service_job",
                    output=data,
                    capture_status="complete",
                    order=80,
                )
            )
        for row in calls:
            data = dict(row)
            prompt = _json(data.get("prompt_text"), data.get("prompt_text"))
            raw = data.get("raw_response")
            metadata: dict[str, Any] = {
                key: data.get(key)
                for key in (
                    "provider",
                    "model",
                    "attempt",
                    "latency_ms",
                    "http_status",
                    "trace_schema_version",
                    "created_at",
                )
                if key in service_columns
            }
            if "usage_json" in data:
                metadata["usage"] = _json(data.get("usage_json"), {})
            if "error_json" in data and data.get("error_json"):
                metadata["error"] = _json(data.get("error_json"), data.get("error_json"))
            phases.append(
                self._phase(
                    f"service:{row['role']}:{row['id']}",
                    f"service:{row['role']}",
                    "background",
                    _phase_status(str(row["status"])),
                    f"Служебная модель: {row['role']}",
                    event_type="service_model_call",
                    input_value={"messages": prompt},
                    output={"raw_response": raw},
                    metadata=metadata,
                    capture_status="complete" if data.get("request_id") else "partial",
                    warnings=[] if data.get("request_id") else ["Legacy call correlation used turn_id."],
                    order=82,
                )
            )
        return phases

    def _mutation_phases(self, request_id: str) -> list[dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM turn_state_mutations
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY id ASC
                """,
                (self.store.campaign_id, request_id),
            ).fetchall()
        if not rows:
            return []
        changes_by_lane: dict[str, list[dict[str, Any]]] = {"main": [], "background": []}
        for row in rows:
            lane = str(row["lane"] or "background")
            changes_by_lane[lane if lane in changes_by_lane else "background"].append(
                {
                    "id": int(row["id"]),
                    "store": row["store_name"],
                    "entity_key": row["entity_key"],
                    "before": _json(row["before_json"], None),
                    "after": _json(row["after_json"], None),
                    "source": row["source"],
                    "reason": row["reason"],
                    "created_at": int(row["created_at"]),
                }
            )
        phases = []
        for lane in ("main", "background"):
            changes = changes_by_lane[lane]
            if not changes:
                continue
            phases.append(self._phase(
                f"projection_mutations:{lane}",
                "projection_mutations",
                lane,
                "completed",
                "Изменения проекций",
                event_type="projection_mutations",
                input_value={"before": [item["before"] for item in changes]},
                output={"after": [item["after"] for item in changes]},
                details={"changes": changes},
                order=79 if lane == "main" else 84,
            ))
        return phases

    def _annotations(self, request_id: str) -> dict[str, list[dict[str, Any]]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM turn_phase_annotations
                WHERE campaign_id = ? AND request_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (self.store.campaign_id, request_id),
            ).fetchall()
        result: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            item = {
                "id": row["id"],
                "phase_key": row["phase_key"],
                "author_user_id": row["author_user_id"],
                "body": row["body"],
                "created_at": int(row["created_at"]),
            }
            result.setdefault(str(row["phase_key"]), []).append(item)
        return result

    @staticmethod
    def _decoded_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for key in list(data):
            if key.endswith("_json"):
                data[key[:-5]] = _json(data.pop(key), {})
        return data

    @staticmethod
    def _phase(
        phase_key: str,
        alignment_key: str,
        lane: str,
        status: str,
        title: str,
        *,
        event_type: str | None = None,
        input_value: Any = None,
        output: Any = None,
        details: Any = None,
        metadata: dict[str, Any] | None = None,
        warnings: list[str] | None = None,
        capture_status: str = "complete",
        order: int = 999,
    ) -> dict[str, Any]:
        return {
            "phase_key": phase_key,
            "alignment_key": alignment_key,
            "lane": lane,
            "event_type": event_type or alignment_key,
            "status": status,
            "capture_status": capture_status,
            "title": title,
            "input": input_value,
            "output": output,
            "details": details,
            "metadata": metadata or {},
            "warnings": warnings or [],
            "annotations": [],
            "_order": order,
        }

    @staticmethod
    def _title(event_type: str, phase_key: str) -> str:
        labels = {
            "player_input": "Действие игрока",
            "gateway_assembly": "Сборка prompt Gateway",
            "narrator_attempt": "Попытка narrator",
            "validation": "Проверка ответа",
            "turn_commit": "Commit хода",
            "request_failed": "Запрос отклонён",
        }
        return labels.get(event_type, phase_key.replace("_", " "))

    @staticmethod
    def _order(event_type: str, payload: dict[str, Any]) -> int:
        base = {
            "player_input": 10,
            "gateway_assembly": 20,
            "narrator_attempt": 30,
            "validation": 40,
            "turn_commit": 60,
            "request_failed": 70,
        }.get(event_type, 45)
        attempt = int((payload.get("metadata") or {}).get("attempt_index") or payload.get("attempt_index") or 0)
        return base + min(attempt, 9)
