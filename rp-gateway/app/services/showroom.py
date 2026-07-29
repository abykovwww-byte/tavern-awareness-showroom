"""Public showroom scenarios, anonymous runs, covers, and leaderboards."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.config import Settings
from app.models.schemas import (
    PartyCreate,
    PlayerCharacterCreate,
    ShowroomRunCreate,
    ShowroomScenarioCreate,
    WorldPromptCreate,
)
from app.services.party_store import PartyStore, now_iso, slug


SHOWROOM_WORLD_OWNER = "__showroom__"


class ShowroomStore:
    def __init__(self, settings: Settings, party_store: PartyStore):
        self.settings = settings
        self.party_store = party_store
        self.cover_dir = Path(settings.showroom_cover_dir)
        self.cover_dir.mkdir(parents=True, exist_ok=True)
        self.init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.settings.sqlite_path)
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
                CREATE TABLE IF NOT EXISTS showroom_scenarios (
                    id TEXT PRIMARY KEY,
                    slug TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    scenario_type TEXT NOT NULL,
                    model_profile_id TEXT NOT NULL REFERENCES model_profiles(id),
                    world_source TEXT NOT NULL,
                    worldpack_id TEXT NOT NULL REFERENCES worldpacks(id),
                    world_prompt TEXT,
                    cover_filename TEXT,
                    cover_mime_type TEXT,
                    leaderboard_enabled INTEGER NOT NULL DEFAULT 1,
                    leaderboard_metric TEXT NOT NULL DEFAULT 'state_path',
                    leaderboard_state_path TEXT NOT NULL DEFAULT 'meta.turn',
                    leaderboard_label TEXT NOT NULL DEFAULT 'Очки',
                    sort_order INTEGER NOT NULL DEFAULT 100,
                    revision INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_showroom_scenarios_status_order
                    ON showroom_scenarios(status, sort_order, updated_at DESC);

                CREATE TABLE IF NOT EXISTS showroom_visitors (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS showroom_runs (
                    id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL REFERENCES showroom_scenarios(id),
                    scenario_revision INTEGER NOT NULL,
                    visitor_id TEXT NOT NULL REFERENCES showroom_visitors(id),
                    party_id TEXT NOT NULL UNIQUE REFERENCES parties(id),
                    player_character_id TEXT NOT NULL REFERENCES player_characters(id),
                    display_name TEXT NOT NULL,
                    leaderboard_opt_in INTEGER NOT NULL DEFAULT 1,
                    client_request_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(visitor_id, client_request_id)
                );
                CREATE INDEX IF NOT EXISTS idx_showroom_runs_visitor_updated
                    ON showroom_runs(visitor_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_showroom_runs_scenario_updated
                    ON showroom_runs(scenario_id, updated_at DESC);
                """
            )

    def unique_slug(self, title: str, scenario_id: str | None = None) -> str:
        base = slug(title)[:80]
        candidate = base
        suffix = 2
        with self.connect() as connection:
            while True:
                sql = "SELECT id FROM showroom_scenarios WHERE slug = ?"
                params: list[Any] = [candidate]
                if scenario_id:
                    sql += " AND id != ?"
                    params.append(scenario_id)
                if connection.execute(sql, tuple(params)).fetchone() is None:
                    return candidate
                candidate = f"{base[:72]}-{suffix}"
                suffix += 1

    def create_scenario(self, request: ShowroomScenarioCreate, created_by: str | None) -> dict[str, Any]:
        model = self.party_store.get_model_profile(request.model_profile_id)
        worldpack_id, world_prompt = self.resolve_world(
            title=request.title,
            world_source=request.world_source,
            worldpack_id=request.worldpack_id,
            world_prompt=request.world_prompt,
        )
        pack = self.party_store.get_worldpack(worldpack_id)
        self.validate_scenario_type(pack.manifest, request.scenario_type)
        scenario_id = f"scenario_{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO showroom_scenarios(
                    id, slug, title, description, status, scenario_type, model_profile_id,
                    world_source, worldpack_id, world_prompt, leaderboard_enabled,
                    leaderboard_metric, leaderboard_state_path, leaderboard_label,
                    sort_order, revision, created_by, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    scenario_id,
                    self.unique_slug(request.title),
                    request.title.strip(),
                    request.description.strip(),
                    request.status,
                    request.scenario_type,
                    model.id,
                    request.world_source,
                    worldpack_id,
                    world_prompt,
                    int(request.leaderboard_enabled),
                    request.leaderboard_metric,
                    request.leaderboard_state_path.strip(),
                    request.leaderboard_label.strip(),
                    request.sort_order,
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_scenario(scenario_id, public_only=False)

    def update_scenario(self, scenario_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        current = self.get_scenario(scenario_id, public_only=False, include_internal=True)
        merged = {**current, **changes}
        title = str(merged["title"]).strip()
        model = self.party_store.get_model_profile(str(merged["model_profile_id"]))

        requested_source = str(merged["world_source"])
        world_changed = requested_source != current["world_source"]
        if requested_source == "preset":
            world_changed = world_changed or str(merged.get("worldpack_id") or "") != current["worldpack_id"]
        else:
            world_changed = world_changed or str(merged.get("world_prompt") or "").strip() != str(
                current.get("world_prompt") or ""
            ).strip()
        if world_changed:
            worldpack_id, world_prompt = self.resolve_world(
                title=title,
                world_source=str(merged["world_source"]),
                worldpack_id=merged.get("worldpack_id"),
                world_prompt=merged.get("world_prompt"),
            )
        else:
            worldpack_id = str(current["worldpack_id"])
            world_prompt = current.get("world_prompt")
        pack = self.party_store.get_worldpack(worldpack_id)
        self.validate_scenario_type(pack.manifest, str(merged["scenario_type"]))

        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE showroom_scenarios SET
                    slug = ?, title = ?, description = ?, status = ?, scenario_type = ?,
                    model_profile_id = ?, world_source = ?, worldpack_id = ?, world_prompt = ?,
                    leaderboard_enabled = ?, leaderboard_metric = ?, leaderboard_state_path = ?,
                    leaderboard_label = ?, sort_order = ?, revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (
                    self.unique_slug(title, scenario_id=scenario_id),
                    title,
                    str(merged.get("description") or "").strip(),
                    str(merged["status"]),
                    str(merged["scenario_type"]),
                    model.id,
                    str(merged["world_source"]),
                    worldpack_id,
                    world_prompt,
                    int(bool(merged["leaderboard_enabled"])),
                    str(merged["leaderboard_metric"]),
                    str(merged["leaderboard_state_path"]).strip(),
                    str(merged["leaderboard_label"]).strip(),
                    int(merged["sort_order"]),
                    timestamp,
                    scenario_id,
                ),
            )
        return self.get_scenario(scenario_id, public_only=False)

    def resolve_world(
        self,
        *,
        title: str,
        world_source: str,
        worldpack_id: str | None,
        world_prompt: str | None,
    ) -> tuple[str, str | None]:
        if world_source == "preset":
            if not worldpack_id:
                raise ValueError("worldpack_id is required for preset world source")
            pack = self.party_store.get_worldpack(str(worldpack_id))
            return pack.id, None
        if world_source != "prompt":
            raise ValueError("world_source must be preset or prompt")
        prompt = str(world_prompt or "").strip()
        if not prompt:
            raise ValueError("world_prompt is required for prompt world source")
        pack = self.party_store.create_prompt_worldpack(
            WorldPromptCreate(title=f"{title} — внутренний мир", prompt=prompt),
            owner_user_id=SHOWROOM_WORLD_OWNER,
        )
        return pack.id, prompt

    @staticmethod
    def validate_scenario_type(manifest: dict[str, Any], scenario_type: str) -> None:
        scenario_types = manifest.get("scenario_types") if isinstance(manifest, dict) else None
        supported = scenario_types.get("supported") if isinstance(scenario_types, dict) else None
        if isinstance(supported, list) and supported and scenario_type not in supported:
            raise ValueError(f"worldpack does not support scenario type {scenario_type}")

    def list_scenarios(self, public_only: bool) -> list[dict[str, Any]]:
        sql = "SELECT * FROM showroom_scenarios"
        if public_only:
            sql += " WHERE status = 'published'"
        sql += " ORDER BY sort_order, updated_at DESC"
        with self.connect() as connection:
            rows = connection.execute(sql).fetchall()
        return [self.scenario_from_row(row, public=public_only) for row in rows]

    def get_scenario(
        self,
        scenario_id: str,
        *,
        public_only: bool,
        include_internal: bool = False,
    ) -> dict[str, Any]:
        sql = "SELECT * FROM showroom_scenarios WHERE id = ?"
        if public_only:
            sql += " AND status = 'published'"
        with self.connect() as connection:
            row = connection.execute(sql, (scenario_id,)).fetchone()
        if row is None:
            raise ValueError(f"showroom scenario not found: {scenario_id}")
        scenario = self.scenario_from_row(row, public=public_only)
        if include_internal:
            scenario["world_prompt"] = row["world_prompt"]
            scenario["cover_filename"] = row["cover_filename"]
        return scenario

    def scenario_from_row(self, row: sqlite3.Row, *, public: bool) -> dict[str, Any]:
        pack = self.party_store.get_worldpack(row["worldpack_id"])
        model = self.party_store.get_model_profile(row["model_profile_id"])
        result: dict[str, Any] = {
            "id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "description": row["description"],
            "status": row["status"],
            "scenario_type": row["scenario_type"],
            "world_source": row["world_source"],
            "worldpack_id": row["worldpack_id"],
            "world": {"id": pack.id, "title": pack.title},
            "cover_url": f"/api/showroom/scenarios/{row['id']}/cover" if row["cover_filename"] else None,
            "leaderboard_enabled": bool(row["leaderboard_enabled"]),
            "leaderboard_metric": row["leaderboard_metric"],
            "leaderboard_state_path": row["leaderboard_state_path"],
            "leaderboard_label": row["leaderboard_label"],
            "sort_order": row["sort_order"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if not public:
            result.update(
                {
                    "model_profile_id": row["model_profile_id"],
                    "model_profile": {
                        "id": model.id,
                        "title": model.title,
                        "provider": model.provider,
                        "model": model.model,
                    },
                    "world_prompt": row["world_prompt"],
                    "created_by": row["created_by"],
                }
            )
        return result

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def visitor_id(self, token: str | None) -> str | None:
        if not token:
            return None
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id FROM showroom_visitors WHERE token_hash = ?",
                (self.token_hash(token),),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE showroom_visitors SET updated_at = ? WHERE id = ?",
                    (now_iso(), row["id"]),
                )
        return str(row["id"]) if row else None

    def ensure_visitor(self, token: str | None) -> tuple[str, str | None]:
        existing = self.visitor_id(token)
        if existing:
            return existing, None
        visitor_id = f"visitor_{uuid.uuid4().hex[:16]}"
        new_token = secrets.token_urlsafe(32)
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO showroom_visitors(id, token_hash, created_at, updated_at) VALUES(?, ?, ?, ?)",
                (visitor_id, self.token_hash(new_token), timestamp, timestamp),
            )
        return visitor_id, new_token

    def create_run(self, scenario_id: str, visitor_id: str, request: ShowroomRunCreate) -> dict[str, Any]:
        if request.client_request_id:
            with self.connect() as connection:
                existing = connection.execute(
                    "SELECT * FROM showroom_runs WHERE visitor_id = ? AND client_request_id = ?",
                    (visitor_id, request.client_request_id),
                ).fetchone()
            if existing:
                return self.run_from_row(existing)

        scenario = self.get_scenario(scenario_id, public_only=False, include_internal=True)
        if scenario["status"] != "published":
            raise ValueError(f"showroom scenario not found: {scenario_id}")
        character = self.party_store.create_player_character(
            PlayerCharacterCreate(
                worldpack_id=scenario["worldpack_id"],
                name=request.character_name.strip(),
                description=request.character_prompt.strip(),
                profile={
                    "source": "showroom",
                    "scenario_id": scenario_id,
                    "scenario_revision": scenario["revision"],
                },
            ),
            owner_user_id=SHOWROOM_WORLD_OWNER,
        )
        party = None
        try:
            party = self.party_store.create_party(
                PartyCreate(
                    title=f"{scenario['title']} — {request.character_name.strip()}",
                    scenario_type=scenario["scenario_type"],
                    worldpack_id=scenario["worldpack_id"],
                    player_character_id=character.id,
                    model_profile_id=scenario["model_profile_id"],
                ),
                owner_user_id=SHOWROOM_WORLD_OWNER,
            )
            run_id = f"run_{uuid.uuid4().hex[:16]}"
            timestamp = now_iso()
            with self.connect() as connection:
                connection.execute(
                    """
                    INSERT INTO showroom_runs(
                        id, scenario_id, scenario_revision, visitor_id, party_id,
                        player_character_id, display_name, leaderboard_opt_in,
                        client_request_id, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        scenario_id,
                        scenario["revision"],
                        visitor_id,
                        party.id,
                        character.id,
                        character.name,
                        int(request.leaderboard_opt_in),
                        request.client_request_id,
                        timestamp,
                        timestamp,
                    ),
                )
            return self.get_run(run_id, visitor_id)
        except Exception:
            if party is not None:
                self.party_store.delete_party(party.id)
            self.party_store.delete_player_character(character.id)
            raise

    def list_runs(self, visitor_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM showroom_runs WHERE visitor_id = ? ORDER BY updated_at DESC",
                (visitor_id,),
            ).fetchall()
        return [self.run_from_row(row) for row in rows]

    def get_run(self, run_id: str, visitor_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM showroom_runs WHERE id = ? AND visitor_id = ?",
                (run_id, visitor_id),
            ).fetchone()
        if row is None:
            raise ValueError(f"showroom run not found: {run_id}")
        return self.run_from_row(row)

    def run_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        scenario = self.get_scenario(row["scenario_id"], public_only=False)
        party = self.party_store.get_party(row["party_id"])
        return {
            "id": row["id"],
            "scenario_id": row["scenario_id"],
            "scenario_revision": row["scenario_revision"],
            "display_name": row["display_name"],
            "leaderboard_opt_in": bool(row["leaderboard_opt_in"]),
            "party_status": party.status,
            "scenario": {
                "id": scenario["id"],
                "title": scenario["title"],
                "scenario_type": scenario["scenario_type"],
                "cover_url": scenario["cover_url"],
                "leaderboard_enabled": scenario["leaderboard_enabled"],
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def party_id_for_run(self, run_id: str, visitor_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT party_id FROM showroom_runs WHERE id = ? AND visitor_id = ?",
                (run_id, visitor_id),
            ).fetchone()
        if row is None:
            raise ValueError(f"showroom run not found: {run_id}")
        return str(row["party_id"])

    def touch_run(self, run_id: str) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE showroom_runs SET updated_at = ? WHERE id = ?", (now_iso(), run_id))

    def leaderboard(self, scenario_id: str, limit: int = 50) -> dict[str, Any]:
        scenario = self.get_scenario(scenario_id, public_only=True, include_internal=True)
        if not scenario["leaderboard_enabled"]:
            return {"scenario_id": scenario_id, "label": scenario["leaderboard_label"], "entries": []}
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM showroom_runs
                WHERE scenario_id = ? AND leaderboard_opt_in = 1
                ORDER BY updated_at DESC
                """,
                (scenario_id,),
            ).fetchall()
        entries: list[dict[str, Any]] = []
        for row in rows:
            try:
                score = self.score_for_run(row, scenario)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            entries.append(
                {
                    "run_id": row["id"],
                    "display_name": row["display_name"],
                    "score": score,
                    "updated_at": row["updated_at"],
                }
            )
        entries.sort(key=lambda item: (-float(item["score"]), item["updated_at"], item["run_id"]))
        ranked = [{**entry, "rank": index + 1} for index, entry in enumerate(entries[: max(1, min(limit, 100))])]
        return {
            "scenario_id": scenario_id,
            "scenario_title": scenario["title"],
            "label": scenario["leaderboard_label"],
            "entries": ranked,
        }

    def score_for_run(self, row: sqlite3.Row, scenario: dict[str, Any]) -> int | float:
        store = self.party_store.store_for_party(row["party_id"])
        if scenario["leaderboard_metric"] == "turn_count":
            return len(store.turn_history(limit=100000))
        value: Any = store.get_state()
        for part in str(scenario["leaderboard_state_path"]).split("."):
            if not isinstance(value, dict) or part not in value:
                raise KeyError(part)
            value = value[part]
        if isinstance(value, dict) and isinstance(value.get("quantity"), (int, float)):
            value = value["quantity"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise TypeError("leaderboard state path must resolve to a number")
        return value

    def save_cover(self, scenario_id: str, content_type: str, data: bytes) -> dict[str, Any]:
        self.get_scenario(scenario_id, public_only=False)
        if not data:
            raise ValueError("cover image is empty")
        if len(data) > self.settings.showroom_cover_max_bytes:
            raise ValueError("cover image is too large")
        detected = self.detect_image_type(data)
        if detected is None:
            raise ValueError("cover must be PNG, JPEG, or WebP")
        mime_type, extension = detected
        if content_type and content_type.split(";", 1)[0].strip().lower() not in {mime_type, "application/octet-stream"}:
            raise ValueError("cover content type does not match image data")
        filename = f"{scenario_id}.{extension}"
        target = self.cover_dir / filename
        target.write_bytes(data)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT cover_filename FROM showroom_scenarios WHERE id = ?",
                (scenario_id,),
            ).fetchone()
            old_filename = row["cover_filename"] if row else None
            connection.execute(
                "UPDATE showroom_scenarios SET cover_filename = ?, cover_mime_type = ?, updated_at = ? WHERE id = ?",
                (filename, mime_type, now_iso(), scenario_id),
            )
        if old_filename and old_filename != filename:
            old_target = self.cover_dir / Path(old_filename).name
            if old_target.parent == self.cover_dir and old_target.exists():
                old_target.unlink()
        return self.get_scenario(scenario_id, public_only=False)

    def delete_cover(self, scenario_id: str) -> dict[str, Any]:
        scenario = self.get_scenario(scenario_id, public_only=False, include_internal=True)
        filename = scenario.get("cover_filename")
        with self.connect() as connection:
            connection.execute(
                "UPDATE showroom_scenarios SET cover_filename = NULL, cover_mime_type = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), scenario_id),
            )
        if filename:
            target = self.cover_dir / Path(str(filename)).name
            if target.parent == self.cover_dir and target.exists():
                target.unlink()
        return self.get_scenario(scenario_id, public_only=False)

    def cover(self, scenario_id: str) -> tuple[Path, str]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT cover_filename, cover_mime_type FROM showroom_scenarios
                WHERE id = ? AND status = 'published'
                """,
                (scenario_id,),
            ).fetchone()
        if row is None or not row["cover_filename"]:
            raise ValueError("showroom cover not found")
        target = self.cover_dir / Path(row["cover_filename"]).name
        if target.parent != self.cover_dir or not target.is_file():
            raise ValueError("showroom cover not found")
        return target, str(row["cover_mime_type"])

    @staticmethod
    def detect_image_type(data: bytes) -> tuple[str, str] | None:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png", "png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg", "jpg"
        if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp", "webp"
        return None
