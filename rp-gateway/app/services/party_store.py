"""Party/session registry for the light GUI."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
import uuid
from calendar import timegm
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.config import Settings
from app.core.json_patch import apply_patch
from app.models.schemas import (
    ModelProfileSummary,
    PartyCreate,
    PartySummary,
    PlayerCharacterCreate,
    PlayerCharacterSummary,
    PlayerTemplate,
    WORLD_PROMPT_MAX_CHARS,
    WorldPromptCreate,
    WorldPackSummary,
)
from app.services.nvidia_catalog import (
    MIN_RP_CONTEXT_TOKENS,
    fetch_build_nvidia_profiles,
    fetch_integrate_api_profiles,
    fetch_provider_api_profiles,
    enrich_openrouter_profile_params,
    is_quality_rp_model,
    is_rp_candidate,
    normalize_provider,
    static_model_profiles,
)
from app.services.context_budget import model_context_limit_tokens
from app.services.state_store import StateStore


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slug(value: str) -> str:
    clean = re.sub(r"[^\w\-]+", "-", value.strip().lower(), flags=re.UNICODE).strip("-")
    return clean or f"id-{uuid.uuid4().hex[:8]}"


class PartyStore:
    def __init__(self, settings: Settings, default_owner_user_id: str | None = None):
        self.settings = settings
        self.default_owner_user_id = default_owner_user_id
        Path(settings.sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        Path(settings.party_state_root).mkdir(parents=True, exist_ok=True)
        self.init_db()
        self.seed_model_profiles()

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
                CREATE TABLE IF NOT EXISTS worldpacks (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT,
                    visibility TEXT NOT NULL DEFAULT 'public',
                    title TEXT NOT NULL,
                    slug TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    premise TEXT NOT NULL DEFAULT '',
                    manifest_path TEXT NOT NULL,
                    state_seed_path TEXT NOT NULL,
                    lorebook_path TEXT,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS player_characters (
                    id TEXT PRIMARY KEY,
                    worldpack_id TEXT NOT NULL REFERENCES worldpacks(id),
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    starting_state_patch_json TEXT,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_profiles (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    api_key_source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS parties (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    scenario_type TEXT NOT NULL DEFAULT 'rp',
                    rp_contract_version TEXT NOT NULL DEFAULT 'rp-core.v1',
                    worldpack_id TEXT NOT NULL REFERENCES worldpacks(id),
                    player_character_id TEXT NOT NULL REFERENCES player_characters(id),
                    model_profile_id TEXT NOT NULL REFERENCES model_profiles(id),
                    state_campaign_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    dataset_review_status TEXT NOT NULL DEFAULT 'review',
                    dataset_tags_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS party_branches (
                    id TEXT PRIMARY KEY,
                    party_id TEXT NOT NULL REFERENCES parties(id) ON DELETE CASCADE,
                    owner_user_id TEXT,
                    label TEXT NOT NULL,
                    branch_type TEXT NOT NULL DEFAULT 'manual',
                    source_checkpoint_id INTEGER NOT NULL,
                    state_campaign_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_party_branches_party_created
                    ON party_branches(party_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS autotest_runs (
                    id TEXT PRIMARY KEY,
                    owner_user_id TEXT,
                    source_party_id TEXT NOT NULL,
                    test_party_id TEXT NOT NULL UNIQUE,
                    player_model_profile_id TEXT NOT NULL,
                    player_prompt TEXT NOT NULL,
                    requested_turns INTEGER NOT NULL,
                    completed_turns INTEGER NOT NULL DEFAULT 0,
                    fallback_turns INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    current_phase TEXT NOT NULL DEFAULT 'queued',
                    stop_requested INTEGER NOT NULL DEFAULT 0,
                    last_player_action TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_autotest_runs_status
                    ON autotest_runs(status, updated_at);
                CREATE TABLE IF NOT EXISTS dataset_turn_labels (
                    campaign_id TEXT NOT NULL,
                    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    review_status TEXT NOT NULL DEFAULT 'review',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    updated_by_user_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(campaign_id, turn_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dataset_turn_labels_status
                    ON dataset_turn_labels(review_status, campaign_id, turn_id);
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
                CREATE TABLE IF NOT EXISTS app_cache (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self.migrate_owner_columns(connection)
            self.migrate_worldpack_visibility(connection)
            self.migrate_scenario_type(connection)
            self.migrate_rp_contract_version(connection)
            self.migrate_autotest_branches(connection)
            self.migrate_dataset_columns(connection)
            self.migrate_turn_feedback_columns(connection)

    def migrate_owner_columns(self, connection: sqlite3.Connection) -> None:
        worldpack_columns = {row["name"] for row in connection.execute("PRAGMA table_info(worldpacks)").fetchall()}
        if "owner_user_id" not in worldpack_columns:
            connection.execute("ALTER TABLE worldpacks ADD COLUMN owner_user_id TEXT")
        for table in ("player_characters", "parties"):
            columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            if "owner_user_id" not in columns:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN owner_user_id TEXT")
            if self.default_owner_user_id:
                connection.execute(
                    f"UPDATE {table} SET owner_user_id = ? WHERE owner_user_id IS NULL OR owner_user_id = ''",
                    (self.default_owner_user_id,),
                )

    def migrate_worldpack_visibility(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(worldpacks)").fetchall()}
        if "visibility" not in columns:
            connection.execute("ALTER TABLE worldpacks ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'")
        connection.execute(
            "UPDATE worldpacks SET visibility = 'public' "
            "WHERE visibility IS NULL OR visibility NOT IN ('public', 'private')"
        )

    def migrate_scenario_type(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(parties)").fetchall()}
        if "scenario_type" not in columns:
            connection.execute("ALTER TABLE parties ADD COLUMN scenario_type TEXT NOT NULL DEFAULT 'rp'")

    def migrate_rp_contract_version(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(parties)").fetchall()}
        if "rp_contract_version" not in columns:
            connection.execute(
                "ALTER TABLE parties ADD COLUMN rp_contract_version TEXT NOT NULL DEFAULT 'rp-core.v1'"
            )
        connection.execute(
            "UPDATE parties SET rp_contract_version = 'rp-core.v1' "
            "WHERE rp_contract_version NOT IN ('rp-core.v1', 'rp-core.v2')"
        )

    def migrate_autotest_branches(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(autotest_runs)").fetchall()}
        if "branch_id" not in columns:
            connection.execute("ALTER TABLE autotest_runs ADD COLUMN branch_id TEXT")
        if "checkpoint_id" not in columns:
            connection.execute("ALTER TABLE autotest_runs ADD COLUMN checkpoint_id INTEGER")
        if "fallback_turns" not in columns:
            connection.execute("ALTER TABLE autotest_runs ADD COLUMN fallback_turns INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_autotest_runs_branch ON autotest_runs(branch_id, updated_at)"
        )

    def migrate_dataset_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(parties)").fetchall()}
        if "dataset_review_status" not in columns:
            connection.execute("ALTER TABLE parties ADD COLUMN dataset_review_status TEXT NOT NULL DEFAULT 'review'")
        if "dataset_tags_json" not in columns:
            connection.execute("ALTER TABLE parties ADD COLUMN dataset_tags_json TEXT NOT NULL DEFAULT '[]'")
        connection.execute(
            "UPDATE parties SET dataset_review_status = 'review' "
            "WHERE dataset_review_status NOT IN ('excluded', 'review', 'approved')"
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

    def seed_model_profiles(self) -> None:
        for profile in static_model_profiles(self.settings):
            self.upsert_model_profile(profile)
        self.refresh_live_model_profiles_if_due()

    def prune_unused_live_model_profiles(self) -> None:
        with self.connect() as connection:
            has_showroom = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'showroom_scenarios'"
            ).fetchone()
            showroom_reference = (
                "AND id NOT IN (SELECT model_profile_id FROM showroom_scenarios)" if has_showroom else ""
            )
            connection.execute(
                f"""
                DELETE FROM model_profiles
                WHERE id NOT IN (SELECT model_profile_id FROM parties)
                  {showroom_reference}
                  AND (
                    params_json LIKE '%"source": "nvidia_api_live"%'
                    OR params_json LIKE '%"source": "build_nvidia_live"%'
                    OR params_json LIKE '%"source": "gemini_api_live"%'
                    OR params_json LIKE '%"source": "openrouter_api_live"%'
                  )
                """
            )

    def upsert_model_profile(self, profile: dict[str, Any]) -> None:
        timestamp = now_iso()
        params = dict(profile.get("params") or {})
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO model_profiles(
                    id, title, provider, base_url, model, params_json,
                    api_key_source, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    provider = excluded.provider,
                    base_url = excluded.base_url,
                    model = excluded.model,
                    params_json = excluded.params_json,
                    api_key_source = excluded.api_key_source,
                    updated_at = excluded.updated_at
                """,
                (
                    profile["id"],
                    profile["title"],
                    profile["provider"],
                    profile["base_url"],
                    profile["model"],
                    json.dumps(params, ensure_ascii=False),
                    profile["api_key_source"],
                    timestamp,
                    timestamp,
                ),
            )

    def refresh_live_model_profiles_if_due(self) -> None:
        if self.settings.app_env == "test" or self.settings.nvidia_api_base.startswith("mock://"):
            return
        if self.settings.nvidia_model_catalog_live:
            self.refresh_nvidia_catalog_if_due()
        if self.settings.gemini_model_catalog_live and self.settings.gemini_api_key:
            self.refresh_provider_catalog_if_due("gemini")
        if self.settings.openrouter_model_catalog_live:
            self.refresh_provider_catalog_if_due("openrouter")

    def refresh_nvidia_catalog_if_due(self) -> None:
        cache_key = "nvidia_model_catalog_refresh_v3"
        if not self.cache_due(cache_key, self.settings.nvidia_model_catalog_ttl_seconds):
            return
        profiles: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            profiles.extend(fetch_build_nvidia_profiles(self.settings))
        except Exception as exc:  # noqa: BLE001 - live catalog is best-effort only
            errors.append(f"build:{type(exc).__name__}")
        if self.settings.nvidia_api_key:
            try:
                profiles.extend(fetch_integrate_api_profiles(self.settings))
            except Exception as exc:  # noqa: BLE001 - live catalog is best-effort only
                errors.append(f"api:{type(exc).__name__}")
        self.store_catalog_refresh(cache_key, profiles, errors)

    def refresh_provider_catalog_if_due(self, provider: str) -> None:
        cache_key = f"{provider}_model_catalog_refresh_v1"
        if not self.cache_due(cache_key, self.settings.provider_model_catalog_ttl_seconds):
            return
        profiles: list[dict[str, Any]] = []
        errors: list[str] = []
        try:
            profiles.extend(fetch_provider_api_profiles(self.settings, provider))
        except Exception as exc:  # noqa: BLE001 - live catalog is best-effort only
            errors.append(f"api:{type(exc).__name__}")
        self.store_catalog_refresh(cache_key, profiles, errors)

    def store_catalog_refresh(self, cache_key: str, profiles: list[dict[str, Any]], errors: list[str]) -> None:
        seen: set[str] = set()
        for profile in profiles:
            if profile["id"] in seen:
                continue
            seen.add(profile["id"])
            self.upsert_model_profile(profile)
        self.cache_set(
            cache_key,
            {
                "source": "live" if seen else "static_fallback",
                "error": ";".join(errors),
                "profiles": len(seen),
            },
        )

    def cache_due(self, key: str, ttl_seconds: int) -> bool:
        if ttl_seconds <= 0:
            return True
        with self.connect() as connection:
            row = connection.execute("SELECT updated_at FROM app_cache WHERE key = ?", (key,)).fetchone()
        if row is None:
            return True
        try:
            updated = time.strptime(row["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
            updated_ts = timegm(updated)
        except (TypeError, ValueError):
            return True
        return time.time() - updated_ts >= ttl_seconds

    def cache_set(self, key: str, value: dict[str, Any]) -> None:
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO app_cache(key, value_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), timestamp),
            )

    def scan_worldpacks(self) -> list[WorldPackSummary]:
        root = Path(self.settings.worldpacks_path)
        packs: list[WorldPackSummary] = []
        if not root.exists():
            return []
        for manifest_path in sorted(root.glob("*/manifest.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            pack_dir = manifest_path.parent
            pack_id = str(manifest.get("id") or pack_dir.name)
            files = manifest.get("files", {}) if isinstance(manifest.get("files"), dict) else {}
            state_seed = pack_dir / str(files.get("state_seed") or "state-seed.json")
            lorebook = files.get("world_info")
            lorebook_path = str((pack_dir / str(lorebook)).resolve()) if lorebook else None
            title = str(manifest.get("title") or pack_id)
            premise = self.extract_premise(manifest)
            status = "playable" if state_seed.exists() else "draft"
            summary = WorldPackSummary(
                id=pack_id,
                title=title,
                slug=pack_dir.name,
                status=status,
                premise=premise,
                manifest_path=str(manifest_path.resolve()),
                state_seed_path=str(state_seed.resolve()),
                lorebook_path=lorebook_path,
                manifest=manifest,
            )
            packs.append(summary)
            self.upsert_worldpack(summary)
        return packs

    def upsert_worldpack(self, pack: WorldPackSummary, owner_user_id: str | None = None) -> None:
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO worldpacks(
                    id, owner_user_id, title, slug, status, premise, manifest_path, state_seed_path,
                    lorebook_path, manifest_json, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    slug = excluded.slug,
                    status = excluded.status,
                    premise = excluded.premise,
                    manifest_path = excluded.manifest_path,
                    state_seed_path = excluded.state_seed_path,
                    lorebook_path = excluded.lorebook_path,
                    manifest_json = excluded.manifest_json,
                    updated_at = excluded.updated_at
                """,
                (
                    pack.id,
                    owner_user_id,
                    pack.title,
                    pack.slug,
                    pack.status,
                    pack.premise,
                    pack.manifest_path,
                    pack.state_seed_path,
                    pack.lorebook_path,
                    json.dumps(pack.manifest, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )

    def create_prompt_worldpack(self, request: WorldPromptCreate, owner_user_id: str | None = None) -> WorldPackSummary:
        title = " ".join(request.title.split())[:160] or "Свой мир"
        is_markdown = request.source == "markdown_file"
        if is_markdown:
            prompt = request.prompt.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()
            state_prompt = prompt[:WORLD_PROMPT_MAX_CHARS].rstrip()
        else:
            prompt = " ".join(request.prompt.split())
            state_prompt = prompt
        pack_id = f"prompt-{slug(title)[:42]}-{uuid.uuid4().hex[:8]}"
        generated_root = Path(self.settings.party_state_root) / "_generated_worldpacks" / pack_id
        generated_root.mkdir(parents=True, exist_ok=True)
        files = {"state_seed": "state-seed.json"}
        if is_markdown:
            (generated_root / "world.md").write_text(prompt + "\n", encoding="utf-8")
            files["gm_system"] = "world.md"
        state = self.prompt_world_state(pack_id, title, state_prompt)
        manifest = {
            "id": pack_id,
            "title": title,
            "language": "ru",
            "mode": "prompt world",
            "status": "playable",
            "premise": prompt[:600],
            "player_role": "Персонаж, заданный игроком для этой партии.",
            "generated_by": "rp-light-gui",
            "rp_contract": {"schema_version": "rp-core.v2"},
            "prompt": state_prompt,
            "prompt_source": request.source,
            "prompt_source_filename": request.source_filename if is_markdown else None,
            "prompt_source_characters": len(prompt),
            "prompt_truncated_in_state": is_markdown and len(prompt) > len(state_prompt),
            "files": files,
        }
        manifest_path = generated_root / "manifest.json"
        state_seed_path = generated_root / "state-seed.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state_seed_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = WorldPackSummary(
            id=pack_id,
            owner_user_id=owner_user_id,
            title=title,
            slug=pack_id,
            status="playable",
            premise=prompt[:600],
            manifest_path=str(manifest_path.resolve()),
            state_seed_path=str(state_seed_path.resolve()),
            lorebook_path=None,
            manifest=manifest,
        )
        self.upsert_worldpack(summary, owner_user_id=owner_user_id)
        return summary

    def prompt_world_state(self, pack_id: str, title: str, prompt: str) -> dict[str, Any]:
        return {
            "meta": {
                "campaign_id": pack_id,
                "schema_version": "1.0.0",
                "state_version": 1,
                "turn": 0,
                "last_updated": "1970-01-01T00:00:00Z",
            },
            "player": {
                "location": "начальная сцена",
                "status": "active",
                "reputation": {},
                "resources": {},
                "known_abilities": [],
                "constraints": [],
                "known_world_facts": [{"id": "world_prompt", "text": prompt, "source": "world_prompt", "turn": 0}],
            },
            "characters": {},
            "factions": {},
            "locations": {
                "initial_scene": {
                    "name": "Начальная сцена",
                    "description": prompt[:500],
                    "status": "available",
                    "hard_constraints": [],
                }
            },
            "resources": {},
            "relationships": {},
            "active_threads": [
                {
                    "id": "main_prompt_thread",
                    "description": f"{title}: развивать мир по prompt игрока, не переписывая подтвержденный state.",
                    "status": "active",
                    "turn": 0,
                }
            ],
            "completed_threads": [],
            "world_constraints": [
                {
                    "id": "attempts_not_facts",
                    "text": "Player declarations of outcome are attempts until confirmed in state.",
                    "scope": "global",
                    "turn": 0,
                },
                {
                    "id": "world_prompt",
                    "text": prompt,
                    "scope": "global",
                    "turn": 0,
                },
            ],
            "timeline": [{"turn": 0, "event": f"Мир создан из prompt: {title}", "confirmed": True, "participants": ["player"]}],
            "last_turn": {"turn": 0, "player_message": "", "narrator_response": "", "state_patch_id": ""},
            "uncertain_facts": [],
        }

    def extract_premise(self, manifest: dict[str, Any]) -> str:
        for key in ("premise", "summary", "description", "player_role"):
            value = manifest.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:600]
        assumptions = manifest.get("assumptions")
        if isinstance(assumptions, list) and assumptions:
            return str(assumptions[0])[:600]
        return ""

    def list_worldpacks(
        self,
        owner_user_id: str | None = None,
        *,
        include_private: bool = False,
    ) -> list[WorldPackSummary]:
        self.scan_worldpacks()
        sql = "SELECT * FROM worldpacks"
        filters: list[str] = []
        params: list[Any] = []
        if owner_user_id:
            filters.append("(owner_user_id IS NULL OR owner_user_id = ?)")
            params.append(owner_user_id)
        if not include_private:
            filters.append("visibility = 'public'")
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY title"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self.worldpack_from_row(row) for row in rows]

    def get_worldpack(
        self,
        worldpack_id: str,
        owner_user_id: str | None = None,
        *,
        include_private: bool = True,
    ) -> WorldPackSummary:
        self.scan_worldpacks()
        sql = "SELECT * FROM worldpacks WHERE id = ?"
        params: list[Any] = [worldpack_id]
        if owner_user_id:
            sql += " AND (owner_user_id IS NULL OR owner_user_id = ?)"
            params.append(owner_user_id)
        if not include_private:
            sql += " AND visibility = 'public'"
        with self.connect() as connection:
            row = connection.execute(sql, tuple(params)).fetchone()
        if row is None:
            raise ValueError(f"worldpack not found: {worldpack_id}")
        return self.worldpack_from_row(row)

    def set_worldpack_visibility(self, worldpack_id: str, visibility: str) -> WorldPackSummary:
        if visibility not in {"public", "private"}:
            raise ValueError("visibility must be public or private")
        self.scan_worldpacks()
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE worldpacks SET visibility = ?, updated_at = ? WHERE id = ?",
                (visibility, now_iso(), worldpack_id),
            ).rowcount
        if updated == 0:
            raise ValueError(f"worldpack not found: {worldpack_id}")
        return self.get_worldpack(worldpack_id)

    def worldpack_from_row(self, row: sqlite3.Row) -> WorldPackSummary:
        return WorldPackSummary(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            visibility=row["visibility"],
            title=row["title"],
            slug=row["slug"],
            status=row["status"],
            premise=row["premise"],
            manifest_path=row["manifest_path"],
            state_seed_path=row["state_seed_path"],
            lorebook_path=row["lorebook_path"],
            manifest=json.loads(row["manifest_json"]),
        )

    def player_templates(
        self,
        worldpack_id: str,
        owner_user_id: str | None = None,
        *,
        include_private: bool = True,
    ) -> list[PlayerTemplate]:
        pack = self.get_worldpack(worldpack_id, owner_user_id=owner_user_id, include_private=include_private)
        role = str(pack.manifest.get("player_role") or "Player character")
        return [
            PlayerTemplate(
                id=f"{worldpack_id}:default-player",
                name="World pack player role",
                description=role,
                profile={"source": "manifest.player_role", "worldpack_id": worldpack_id},
            )
        ]

    def opening_scene_text(self, pack: WorldPackSummary) -> str:
        files = pack.manifest.get("files", {}) if isinstance(pack.manifest.get("files"), dict) else {}
        opening = files.get("opening_scene")
        if not opening:
            return ""
        base = Path(pack.manifest_path).resolve().parent
        target = (base / str(opening)).resolve()
        if base not in target.parents:
            return ""
        try:
            return target.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def list_player_characters(
        self,
        worldpack_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list[PlayerCharacterSummary]:
        self.scan_worldpacks()
        sql = "SELECT * FROM player_characters"
        filters: list[str] = []
        params: list[Any] = []
        if worldpack_id:
            filters.append("worldpack_id = ?")
            params.append(worldpack_id)
        if owner_user_id:
            filters.append("owner_user_id = ?")
            params.append(owner_user_id)
        if filters:
            sql += " WHERE " + " AND ".join(filters)
        sql += " ORDER BY updated_at DESC"
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [self.character_from_row(row) for row in rows]

    def create_player_character(self, request: PlayerCharacterCreate, owner_user_id: str | None = None) -> PlayerCharacterSummary:
        self.get_worldpack(request.worldpack_id, owner_user_id=owner_user_id)
        character_id = f"pc_{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        profile = dict(request.profile)
        profile.setdefault("name", request.name)
        profile.setdefault("worldpack_id", request.worldpack_id)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO player_characters(
                    id, owner_user_id, worldpack_id, name, description, status,
                    starting_state_patch_json, profile_json, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    character_id,
                    owner_user_id,
                    request.worldpack_id,
                    request.name,
                    request.description,
                    "active",
                    request.starting_state_patch_json,
                    json.dumps(profile, ensure_ascii=False),
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_player_character(character_id, owner_user_id=owner_user_id)

    def get_player_character(self, character_id: str, owner_user_id: str | None = None) -> PlayerCharacterSummary:
        sql = "SELECT * FROM player_characters WHERE id = ?"
        params: list[Any] = [character_id]
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        with self.connect() as connection:
            row = connection.execute(sql, tuple(params)).fetchone()
        if row is None:
            raise ValueError(f"player character not found: {character_id}")
        return self.character_from_row(row)

    def delete_player_character(self, character_id: str, owner_user_id: str | None = None) -> None:
        sql = "DELETE FROM player_characters WHERE id = ?"
        params: list[Any] = [character_id]
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        with self.connect() as connection:
            deleted = connection.execute(sql, tuple(params)).rowcount
        if deleted == 0:
            raise ValueError(f"player character not found: {character_id}")

    def character_from_row(self, row: sqlite3.Row) -> PlayerCharacterSummary:
        return PlayerCharacterSummary(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            worldpack_id=row["worldpack_id"],
            name=row["name"],
            description=row["description"],
            status=row["status"],
            starting_state_patch_json=row["starting_state_patch_json"],
            profile=json.loads(row["profile_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_model_profiles(self) -> list[ModelProfileSummary]:
        self.seed_model_profiles()
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM model_profiles ORDER BY title").fetchall()
        profiles = [self.model_profile_from_row(row) for row in rows]
        profiles = [profile for profile in profiles if self.model_profile_is_visible(profile)]
        return sorted(profiles, key=lambda profile: (int(profile.params.get("rank", 9999)), profile.title))

    def list_autotest_model_profiles(self) -> list[ModelProfileSummary]:
        """Return the explicitly supported LLM-player profiles.

        Local Gemma is allowed here even when its 32k context is intentionally
        hidden from the long-context narrator picker. The auto-player receives
        only a bounded visible transcript, so that narrator restriction does
        not apply.
        """
        self.seed_model_profiles()
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM model_profiles ORDER BY title").fetchall()
        profiles = [self.model_profile_from_row(row) for row in rows]
        selected: list[ModelProfileSummary] = []
        for profile in profiles:
            provider = normalize_provider(profile.provider)
            if provider == "local":
                if self.settings.local_llm_enabled and profile.model == self.settings.local_llm_model_alias:
                    selected.append(profile)
            elif provider == "openrouter" and self.model_profile_is_visible(profile):
                selected.append(profile)
        return sorted(selected, key=lambda profile: (int(profile.params.get("rank", 9999)), profile.title))

    def model_profile_is_visible(self, profile: ModelProfileSummary) -> bool:
        provider = normalize_provider(profile.provider)
        if provider == "openrouter" and profile.model.lower().endswith(":batch"):
            return False
        if (model_context_limit_tokens(profile) or 0) < MIN_RP_CONTEXT_TOKENS:
            return False
        configured = {
            self.settings.narrative_model,
            *self.settings.nvidia_fallback_models,
            *self.settings.gemini_models,
            *self.settings.openrouter_models,
        }
        if profile.model in configured:
            return True
        if provider == "local":
            return bool(self.settings.local_llm_enabled) and profile.model == self.settings.local_llm_model_alias
        if provider == "nvidia":
            return is_rp_candidate(profile.model)
        if provider == "gemini":
            return profile.model.startswith("gemini-") and is_quality_rp_model(profile.model)
        if provider == "openrouter":
            return profile.rp_specialized or is_quality_rp_model(profile.model)
        return False

    def get_model_profile(self, model_profile_id: str) -> ModelProfileSummary:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM model_profiles WHERE id = ?", (model_profile_id,)).fetchone()
        if row is None:
            raise ValueError(f"model profile not found: {model_profile_id}")
        return self.model_profile_from_row(row)

    def model_profile_from_row(self, row: sqlite3.Row) -> ModelProfileSummary:
        params = json.loads(row["params_json"])
        provider = normalize_provider(row["provider"])
        if provider == "openrouter":
            params = enrich_openrouter_profile_params(row["model"], params)
        return ModelProfileSummary(
            id=row["id"],
            title=str(params.get("title_override") or row["title"]),
            provider=provider,
            base_url=row["base_url"],
            model=row["model"],
            params=params,
            api_key_source=row["api_key_source"],
            description=str(params.get("description") or ""),
            rp_fit=str(params.get("rp_fit") or ""),
            context_window=str(params.get("context_window") or ""),
            tags=[str(tag) for tag in params.get("tags", [])],
            source=str(params.get("source") or "static"),
            availability=str(params.get("availability") or ""),
            is_free=bool(params.get("is_free", False)),
            pricing_prompt=str(params.get("pricing_prompt") or ""),
            pricing_completion=str(params.get("pricing_completion") or ""),
            pricing_input_cache_read=str(params.get("pricing_input_cache_read") or ""),
            pricing_input_cache_write=str(params.get("pricing_input_cache_write") or ""),
            pricing_input_cache_write_1h=str(params.get("pricing_input_cache_write_1h") or ""),
            rp_specialized=bool(params.get("rp_specialized", False)),
        )

    def list_parties(self, owner_user_id: str | None = None) -> list[PartySummary]:
        self.scan_worldpacks()
        sql = "SELECT * FROM parties WHERE id NOT IN (SELECT test_party_id FROM autotest_runs WHERE branch_id IS NULL)"
        params: tuple[Any, ...] = ()
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params = (owner_user_id,)
        sql += " ORDER BY updated_at DESC"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self.party_from_row(row, include_related=True) for row in rows]

    def create_party(self, request: PartyCreate, owner_user_id: str | None = None) -> PartySummary:
        pack = self.get_worldpack(request.worldpack_id, owner_user_id=owner_user_id)
        scenario_types = pack.manifest.get("scenario_types") if isinstance(pack.manifest, dict) else None
        supported = scenario_types.get("supported") if isinstance(scenario_types, dict) else None
        if isinstance(supported, list) and supported and request.scenario_type not in supported:
            raise ValueError(f"worldpack {pack.id} does not support scenario type {request.scenario_type}")
        rp_contract = pack.manifest.get("rp_contract") if isinstance(pack.manifest, dict) else None
        rp_contract_version = (
            str(rp_contract.get("schema_version") or "rp-core.v1")
            if request.scenario_type == "rp" and isinstance(rp_contract, dict)
            else "rp-core.v1"
        )
        if rp_contract_version not in {"rp-core.v1", "rp-core.v2"}:
            raise ValueError(f"worldpack {pack.id} declares unsupported RP contract {rp_contract_version}")
        character = self.get_player_character(request.player_character_id, owner_user_id=owner_user_id)
        if character.worldpack_id != pack.id:
            raise ValueError("player character belongs to a different worldpack")
        self.get_model_profile(request.model_profile_id)

        party_id = f"party_{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        self.initialize_party_state(party_id, pack, character)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO parties(
                    id, owner_user_id, title, scenario_type, rp_contract_version,
                    worldpack_id, player_character_id, model_profile_id,
                    state_campaign_id, status, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    party_id,
                    owner_user_id,
                    request.title,
                    request.scenario_type,
                    rp_contract_version,
                    request.worldpack_id,
                    request.player_character_id,
                    request.model_profile_id,
                    party_id,
                    "active",
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_party(party_id, owner_user_id=owner_user_id)

    def get_party(self, party_id: str, owner_user_id: str | None = None) -> PartySummary:
        sql = "SELECT * FROM parties WHERE id = ?"
        params: list[Any] = [party_id]
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        with self.connect() as connection:
            row = connection.execute(sql, tuple(params)).fetchone()
        if row is None:
            raise ValueError(f"party not found: {party_id}")
        return self.party_from_row(row, include_related=True)

    def activate_party(self, party_id: str, owner_user_id: str | None = None) -> PartySummary:
        timestamp = now_iso()
        sql = "UPDATE parties SET status = 'active', updated_at = ? WHERE id = ?"
        params: list[Any] = [timestamp, party_id]
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        with self.connect() as connection:
            updated = connection.execute(sql, tuple(params)).rowcount
        if updated == 0:
            raise ValueError(f"party not found: {party_id}")
        return self.get_party(party_id, owner_user_id=owner_user_id)

    def complete_party(self, party_id: str, owner_user_id: str | None = None) -> PartySummary:
        party = self.get_party(party_id, owner_user_id=owner_user_id)
        if party.status == "completed":
            return party
        timestamp = now_iso()
        sql = "UPDATE parties SET status = 'completed', updated_at = ? WHERE id = ?"
        params: list[Any] = [timestamp, party_id]
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        with self.connect() as connection:
            updated = connection.execute(sql, tuple(params)).rowcount
        if updated == 0:
            raise ValueError(f"party not found: {party_id}")
        return self.get_party(party_id, owner_user_id=owner_user_id)

    def update_party_model(self, party_id: str, model_profile_id: str, owner_user_id: str | None = None) -> PartySummary:
        self.get_model_profile(model_profile_id)
        timestamp = now_iso()
        sql = "UPDATE parties SET model_profile_id = ?, updated_at = ? WHERE id = ?"
        params: list[Any] = [model_profile_id, timestamp, party_id]
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        with self.connect() as connection:
            updated = connection.execute(sql, tuple(params)).rowcount
        if updated == 0:
            raise ValueError(f"party not found: {party_id}")
        return self.get_party(party_id, owner_user_id=owner_user_id)

    @staticmethod
    def normalize_dataset_tags(tags: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in tags:
            tag = re.sub(r"\s+", "-", str(raw).strip().lower())
            tag = re.sub(r"[^\w:.-]+", "", tag, flags=re.UNICODE)[:80]
            if tag and tag not in normalized:
                normalized.append(tag)
        return normalized[:40]

    @staticmethod
    def validate_dataset_review_status(review_status: str) -> str:
        if review_status not in {"excluded", "review", "approved"}:
            raise ValueError("dataset review status must be excluded, review, or approved")
        return review_status

    def update_party_dataset(
        self,
        party_id: str,
        *,
        review_status: str,
        tags: list[str],
        owner_user_id: str | None = None,
    ) -> PartySummary:
        self.get_party(party_id, owner_user_id=owner_user_id)
        status = self.validate_dataset_review_status(review_status)
        normalized_tags = self.normalize_dataset_tags(tags)
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE parties
                SET dataset_review_status = ?, dataset_tags_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, json.dumps(normalized_tags, ensure_ascii=False), now_iso(), party_id),
            )
        return self.get_party(party_id, owner_user_id=owner_user_id)

    def dataset_campaign_id(
        self,
        party_id: str,
        branch_id: str | None,
        owner_user_id: str | None,
    ) -> tuple[PartySummary, str]:
        party = self.get_party(party_id, owner_user_id=owner_user_id)
        if not branch_id:
            return party, party.state_campaign_id
        branch = self.get_party_branch(party_id, branch_id, owner_user_id=owner_user_id)
        return party, str(branch["state_campaign_id"])

    def set_turn_dataset_label(
        self,
        party_id: str,
        turn_id: int,
        *,
        branch_id: str | None,
        review_status: str,
        tags: list[str],
        notes: str,
        owner_user_id: str | None,
        updated_by_user_id: str | None,
    ) -> dict[str, Any]:
        _, campaign_id = self.dataset_campaign_id(party_id, branch_id, owner_user_id)
        status = self.validate_dataset_review_status(review_status)
        normalized_tags = self.normalize_dataset_tags(tags)
        with self.connect() as connection:
            turn = connection.execute(
                "SELECT id FROM turns WHERE id = ? AND campaign_id = ?",
                (int(turn_id), campaign_id),
            ).fetchone()
            if turn is None:
                raise ValueError(f"turn not found: {turn_id}")
            connection.execute(
                """
                INSERT INTO dataset_turn_labels(
                    campaign_id, turn_id, review_status, tags_json, notes,
                    updated_by_user_id, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(campaign_id, turn_id) DO UPDATE SET
                    review_status = excluded.review_status,
                    tags_json = excluded.tags_json,
                    notes = excluded.notes,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (
                    campaign_id,
                    int(turn_id),
                    status,
                    json.dumps(normalized_tags, ensure_ascii=False),
                    notes.strip(),
                    updated_by_user_id,
                    now_iso(),
                ),
            )
        return self.get_turn_dataset_label(campaign_id, int(turn_id))

    def get_turn_dataset_label(self, campaign_id: str, turn_id: int) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM dataset_turn_labels WHERE campaign_id = ? AND turn_id = ?",
                (campaign_id, int(turn_id)),
            ).fetchone()
        if row is None:
            return {
                "campaign_id": campaign_id,
                "turn_id": int(turn_id),
                "review_status": "review",
                "tags": [],
                "notes": "",
                "updated_by_user_id": None,
                "updated_at": None,
            }
        label = dict(row)
        label["tags"] = json.loads(label.pop("tags_json") or "[]")
        return label

    def list_dataset_turns(
        self,
        party_id: str,
        *,
        branch_id: str | None = None,
        owner_user_id: str | None = None,
        limit: int | None = 500,
    ) -> list[dict[str, Any]]:
        party, campaign_id = self.dataset_campaign_id(party_id, branch_id, owner_user_id)
        limit_sql = " LIMIT ?" if limit is not None else ""
        params: tuple[Any, ...] = (
            (campaign_id, min(max(limit, 1), 5000)) if limit is not None else (campaign_id,)
        )
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT t.*, l.review_status, l.tags_json, l.notes, l.updated_at AS label_updated_at,
                       COALESCE(f.rating, 0) AS player_rating_value,
                       COALESCE(f.liked, 0) AS player_liked, f.source_ui AS feedback_source
                FROM turns t
                LEFT JOIN dataset_turn_labels l
                  ON l.campaign_id = t.campaign_id AND l.turn_id = t.id
                LEFT JOIN turn_feedback f
                  ON f.campaign_id = t.campaign_id AND f.turn_id = t.id
                WHERE t.campaign_id = ?
                ORDER BY t.id ASC
                {limit_sql}
                """,
                params,
            ).fetchall()
        return [self.dataset_turn_from_row(party, branch_id, row) for row in rows]

    def export_dataset_records(
        self,
        *,
        owner_user_id: str | None,
        scenario_type: str | None = None,
        include_branches: bool = True,
    ) -> dict[str, Any]:
        parties = self.list_parties(owner_user_id=owner_user_id)
        records: list[dict[str, Any]] = []
        for party in parties:
            if party.dataset_review_status != "approved":
                continue
            if scenario_type and party.scenario_type != scenario_type:
                continue
            records.extend(
                turn for turn in self.list_dataset_turns(
                    party.id,
                    owner_user_id=owner_user_id,
                    limit=None,
                )
                if turn["review_status"] == "approved"
            )
            if include_branches:
                branch_sql = "SELECT id FROM party_branches WHERE party_id = ?"
                branch_params: tuple[Any, ...] = (party.id,)
                if owner_user_id:
                    branch_sql += " AND owner_user_id = ?"
                    branch_params = (party.id, owner_user_id)
                with self.connect() as connection:
                    branches = connection.execute(branch_sql + " ORDER BY created_at ASC", branch_params).fetchall()
                for branch in branches:
                    records.extend(
                        turn for turn in self.list_dataset_turns(
                            party.id,
                            branch_id=branch["id"],
                            owner_user_id=owner_user_id,
                            limit=None,
                        )
                        if turn["review_status"] == "approved"
                    )
        exportable = [turn for turn in records if turn.get("prompt_messages")]
        return {
            "records": [self.dataset_sft_record(turn) for turn in exportable],
            "approved_turns": len(records),
            "skipped_missing_prompt": len(records) - len(exportable),
        }

    def dataset_turn_from_row(
        self,
        party: PartySummary,
        branch_id: str | None,
        row: sqlite3.Row,
    ) -> dict[str, Any]:
        response = json.loads(row["response_json"] or "{}")
        metadata = json.loads(row["metadata_json"] or "{}")
        prompt_messages = json.loads(row["prompt_json"]) if row["prompt_json"] else None
        choices = response.get("choices") if isinstance(response, dict) else []
        first_choice = choices[0] if isinstance(choices, list) and choices else {}
        response_id = str(response.get("id") or "") if isinstance(response, dict) else ""
        finish_reason = str(first_choice.get("finish_reason") or "") if isinstance(first_choice, dict) else ""
        auto_tags = [party.scenario_type, "branch" if branch_id else "main"]
        if str(row["player_message"]).startswith("[AUTO_START]"):
            auto_tags.append("opening-scene")
        if branch_id:
            auto_tags.append("autotest" if str(row["idempotency_key"]).startswith("autotest:") else "manual-branch")
        if response_id.startswith("fallback-") or finish_reason == "provider_fallback" or metadata.get("fallback"):
            auto_tags.append("fallback")
        if metadata.get("repaired"):
            auto_tags.append("repaired")
        if metadata.get("validator_valid") is False:
            auto_tags.append("validator-invalid")
        if not prompt_messages:
            auto_tags.append("missing-prompt")
        if bool(row["player_liked"]):
            auto_tags.append("player-liked")
        if int(row["player_rating_value"]) == -1:
            auto_tags.append("player-disliked")
        capabilities = metadata.get("training_capabilities") if isinstance(metadata, dict) else None
        if isinstance(capabilities, dict):
            auto_tags.append("interactive-links" if capabilities.get("interactive_links_enabled") else "noninteractive-links")
            auto_tags.append("interactive-workspace" if capabilities.get("interactive_workspace_enabled") else "noninteractive-workspace")
        player_rating = {1: "positive", -1: "negative"}.get(int(row["player_rating_value"]), "none")
        with self.connect() as connection:
            artifact_rows = connection.execute(
                """
                SELECT public_json FROM training_artifacts
                WHERE campaign_id = ? AND turn_id = ?
                ORDER BY artifact_key ASC
                """,
                (row["campaign_id"], int(row["id"])),
            ).fetchall()
            evidence_rows = connection.execute(
                """
                SELECT e.id, e.event_id, e.artifact_id, e.event_type, e.evidence_json, e.created_at
                FROM training_artifact_events e
                WHERE e.campaign_id = ? AND e.consumed_turn_id = ?
                ORDER BY e.id ASC
                """,
                (row["campaign_id"], int(row["id"])),
            ).fetchall()
            workspace_rows = connection.execute(
                """
                SELECT public_json FROM training_workspace_files
                WHERE campaign_id = ? AND turn_id = ?
                ORDER BY file_key ASC
                """,
                (row["campaign_id"], int(row["id"])),
            ).fetchall()
            workspace_evidence_rows = connection.execute(
                """
                SELECT e.id, e.event_id, e.file_id, e.event_type, e.evidence_json, e.created_at
                FROM training_workspace_events e
                WHERE e.campaign_id = ? AND e.consumed_turn_id = ?
                ORDER BY e.id ASC
                """,
                (row["campaign_id"], int(row["id"])),
            ).fetchall()
        artifacts = [json.loads(item["public_json"]) for item in artifact_rows]
        workspace_files = [json.loads(item["public_json"]) for item in workspace_rows]
        interaction_evidence = []
        for item in evidence_rows:
            evidence = json.loads(item["evidence_json"] or "{}")
            interaction_evidence.append(
                {
                    "event_sequence": int(item["id"]),
                    "event_id": item["event_id"],
                    "artifact_id": item["artifact_id"],
                    "event_type": item["event_type"],
                    "evidence": str(evidence.get("evidence") or ""),
                    "score_rule_id": str(evidence.get("score_rule_id") or ""),
                    "decision_result": str(evidence.get("decision_result") or "neutral"),
                    "created_at": int(item["created_at"]),
                }
            )
        workspace_interaction_evidence = []
        for item in workspace_evidence_rows:
            evidence = json.loads(item["evidence_json"] or "{}")
            workspace_interaction_evidence.append(
                {
                    "event_sequence": int(item["id"]),
                    "event_id": item["event_id"],
                    "file_id": item["file_id"],
                    "event_type": item["event_type"],
                    "evidence": str(evidence.get("evidence") or ""),
                    "score_rule_id": str(evidence.get("score_rule_id") or ""),
                    "decision_result": str(evidence.get("decision_result") or "neutral"),
                    "created_at": int(item["created_at"]),
                }
            )
        return {
            "schema_version": "rp-gateway.dataset-candidate.v1",
            "party_id": party.id,
            "campaign_id": row["campaign_id"],
            "branch_id": branch_id,
            "turn_id": int(row["id"]),
            "request_id": row["request_id"],
            "player_message": row["player_message"],
            "scenario_type": party.scenario_type,
            "worldpack_id": party.worldpack_id,
            "party_tags": party.dataset_tags,
            "review_status": row["review_status"] or "review",
            "tags": json.loads(row["tags_json"] or "[]") if row["tags_json"] else [],
            "auto_tags": self.normalize_dataset_tags(auto_tags),
            "notes": row["notes"] or "",
            "prompt_messages": prompt_messages,
            "assistant_response": row["narrative_response"],
            "artifacts": artifacts,
            "interaction_evidence": interaction_evidence,
            "workspace_files": workspace_files,
            "workspace_interaction_evidence": workspace_interaction_evidence,
            "player_feedback": {
                "rating": player_rating,
                "liked": bool(row["player_liked"]),
                "disliked": player_rating == "negative",
                "source_ui": row["feedback_source"],
            },
            "metadata": metadata,
            "state_version": int(row["state_version"]),
            "created_at": int(row["created_at"]),
        }

    @staticmethod
    def dataset_sft_record(turn: dict[str, Any]) -> dict[str, Any]:
        messages = [
            {"role": str(message.get("role") or "user"), "content": str(message.get("content") or "")}
            for message in turn["prompt_messages"]
            if isinstance(message, dict)
        ]
        messages.append({"role": "assistant", "content": turn["assistant_response"]})
        return {
            "messages": messages,
            "metadata": {
                "schema_version": "rp-gateway.sft.v1",
                "sample_id": f"{turn['campaign_id']}:{turn['turn_id']}",
                "group_id": turn["campaign_id"],
                "party_id": turn["party_id"],
                "branch_id": turn["branch_id"],
                "turn_id": turn["turn_id"],
                "scenario_type": turn["scenario_type"],
                "worldpack_id": turn["worldpack_id"],
                "tags": PartyStore.normalize_dataset_tags(
                    [*turn["party_tags"], *turn["tags"], *turn["auto_tags"]]
                ),
                "player_feedback": turn["player_feedback"],
                "state_version": turn["state_version"],
                "source": turn["metadata"],
                "artifacts": turn.get("artifacts") or [],
                "interaction_evidence": turn.get("interaction_evidence") or [],
                "training_capabilities": turn["metadata"].get("training_capabilities") or {},
                "workspace_files": turn.get("workspace_files") or [],
                "workspace_interaction_evidence": turn.get("workspace_interaction_evidence") or [],
            },
        }

    def delete_party(self, party_id: str, owner_user_id: str | None = None) -> None:
        party = self.get_party(party_id, owner_user_id=owner_user_id)
        branches = self.list_party_branches(party_id, owner_user_id=owner_user_id, limit=200)
        with self.connect() as connection:
            campaign_ids = [party.state_campaign_id, *[branch["state_campaign_id"] for branch in branches]]
            for campaign_id in campaign_ids:
                connection.execute("DELETE FROM dataset_turn_labels WHERE campaign_id = ?", (campaign_id,))
                connection.execute("DELETE FROM turn_feedback WHERE campaign_id = ?", (campaign_id,))
                for table in (
                    "turns",
                    "turn_requests",
                    "checks",
                    "state_patches",
                    "state_versions",
                    "audit_events",
                    "memory_summaries",
                    "memory_chapters",
                    "rp_story_memory_snapshots",
                    "journal_entries",
                    "lore_cards",
                    "memory_checkpoints",
                    "service_jobs",
                    "training_runtime_snapshots",
                ):
                    connection.execute(f"DELETE FROM {table} WHERE campaign_id = ?", (campaign_id,))
                connection.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))
            connection.execute("DELETE FROM party_branches WHERE party_id = ?", (party_id,))
            connection.execute("DELETE FROM parties WHERE id = ?", (party_id,))
        self.delete_party_state_dir(party.state_campaign_id)

    def delete_party_state_dir(self, state_campaign_id: str) -> None:
        root = Path(self.settings.party_state_root).resolve()
        target = (root / state_campaign_id).resolve()
        if target == root or root not in target.parents:
            raise ValueError("party state path is outside party state root")
        if target.exists():
            shutil.rmtree(target)

    def delete_user_data(self, owner_user_id: str) -> None:
        with self.connect() as connection:
            party_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM parties WHERE owner_user_id = ? ORDER BY created_at ASC",
                    (owner_user_id,),
                ).fetchall()
            ]
        for party_id in party_ids:
            self.delete_party(party_id, owner_user_id=owner_user_id)
        with self.connect() as connection:
            connection.execute("DELETE FROM autotest_runs WHERE owner_user_id = ?", (owner_user_id,))
            connection.execute("DELETE FROM player_characters WHERE owner_user_id = ?", (owner_user_id,))

    def create_party_branch(
        self,
        *,
        party_id: str,
        checkpoint_id: int,
        label: str,
        branch_type: str = "manual",
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        party = self.get_party(party_id, owner_user_id=owner_user_id)
        source_store = self.store_for_party(party.id, owner_user_id=owner_user_id)
        checkpoint = source_store.get_memory_checkpoint(checkpoint_id)
        branch_id = f"branch_{uuid.uuid4().hex[:12]}"
        state_campaign_id = f"{party.id}--{branch_id}"
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO party_branches(
                    id, party_id, owner_user_id, label, branch_type,
                    source_checkpoint_id, state_campaign_id, status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    branch_id,
                    party.id,
                    owner_user_id,
                    label.strip(),
                    branch_type,
                    int(checkpoint["id"]),
                    state_campaign_id,
                    timestamp,
                    timestamp,
                ),
            )
        try:
            source_store.fork_from_checkpoint(
                checkpoint_id=int(checkpoint["id"]),
                target_campaign_id=state_campaign_id,
                target_state_path=str(self.state_path_for_branch(party.id, branch_id)),
            )
        except Exception:
            with self.connect() as connection:
                connection.execute("DELETE FROM party_branches WHERE id = ?", (branch_id,))
            raise
        return self.get_party_branch(party.id, branch_id, owner_user_id=owner_user_id)

    def get_party_branch(
        self,
        party_id: str,
        branch_id: str,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        sql = "SELECT * FROM party_branches WHERE id = ? AND party_id = ?"
        params: list[Any] = [branch_id, party_id]
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        with self.connect() as connection:
            row = connection.execute(sql, tuple(params)).fetchone()
        if row is None:
            raise ValueError(f"party branch not found: {branch_id}")
        return dict(row)

    def list_party_branches(
        self,
        party_id: str,
        owner_user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.get_party(party_id, owner_user_id=owner_user_id)
        sql = "SELECT * FROM party_branches WHERE party_id = ?"
        params: list[Any] = [party_id]
        if owner_user_id:
            sql += " AND owner_user_id = ?"
            params.append(owner_user_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(max(limit, 1), 200))
        with self.connect() as connection:
            rows = connection.execute(sql, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def list_all_party_branches(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM party_branches ORDER BY created_at ASC").fetchall()
        return [dict(row) for row in rows]

    def store_for_branch(
        self,
        party_id: str,
        branch_id: str,
        owner_user_id: str | None = None,
    ) -> StateStore:
        branch = self.get_party_branch(party_id, branch_id, owner_user_id=owner_user_id)
        return StateStore(
            self.settings.sqlite_path,
            branch["state_campaign_id"],
            str(self.state_path_for_branch(party_id, branch_id)),
        )

    def state_path_for_branch(self, party_id: str, branch_id: str) -> Path:
        return Path(self.settings.party_state_root) / party_id / "branches" / branch_id / "current.json"

    def create_autotest_run(
        self,
        *,
        owner_user_id: str | None,
        source_party_id: str,
        branch_id: str,
        checkpoint_id: int,
        player_model_profile_id: str,
        player_prompt: str,
        requested_turns: int,
    ) -> dict[str, Any]:
        if not 1 <= requested_turns <= 30:
            raise ValueError("autotest turn count must be between 1 and 30")
        run_id = f"autotest_{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO autotest_runs(
                    id, owner_user_id, source_party_id, test_party_id, branch_id, checkpoint_id,
                    player_model_profile_id, player_prompt, requested_turns,
                    completed_turns, fallback_turns, status, current_phase, stop_requested,
                    last_player_action, error, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'running', 'player', 0, NULL, NULL, ?, ?)
                """,
                (
                    run_id,
                    owner_user_id,
                    source_party_id,
                    f"branch:{branch_id}",
                    branch_id,
                    checkpoint_id,
                    player_model_profile_id,
                    player_prompt.strip(),
                    requested_turns,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE party_branches SET status = 'running', updated_at = ? WHERE id = ?",
                (timestamp, branch_id),
            )
        return self.get_autotest_run(run_id)

    def get_autotest_run(self, run_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM autotest_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise ValueError(f"autotest run not found: {run_id}")
        return self.autotest_run_from_row(row)

    def list_autotest_runs(
        self,
        limit: int = 50,
        source_party_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where = "WHERE source_party_id = ?" if source_party_id else ""
        parameters: tuple[Any, ...] = (source_party_id, min(max(limit, 1), 200)) if source_party_id else (
            min(max(limit, 1), 200),
        )
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM autotest_runs {where} ORDER BY created_at DESC LIMIT ?",
                parameters,
            ).fetchall()
        return [self.autotest_run_from_row(row) for row in rows]

    def resumable_autotest_runs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM autotest_runs WHERE status IN ('running', 'stopping') ORDER BY created_at ASC"
            ).fetchall()
        return [self.autotest_run_from_row(row) for row in rows]

    def active_autotest_for_party(self, party_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM autotest_runs
                WHERE test_party_id = ? AND status IN ('running', 'stopping')
                ORDER BY created_at DESC LIMIT 1
                """,
                (party_id,),
            ).fetchone()
        return self.autotest_run_from_row(row) if row else None

    def update_autotest_run(self, run_id: str, **updates: Any) -> dict[str, Any]:
        allowed = {
            "completed_turns",
            "fallback_turns",
            "status",
            "current_phase",
            "stop_requested",
            "last_player_action",
            "error",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key not in allowed:
                continue
            assignments.append(f"{key} = ?")
            values.append(int(value) if key == "stop_requested" else value)
        if not assignments:
            return self.get_autotest_run(run_id)
        assignments.append("updated_at = ?")
        values.append(now_iso())
        values.append(run_id)
        with self.connect() as connection:
            updated = connection.execute(
                f"UPDATE autotest_runs SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            ).rowcount
            if "status" in updates:
                row = connection.execute("SELECT branch_id FROM autotest_runs WHERE id = ?", (run_id,)).fetchone()
                if row and row["branch_id"]:
                    connection.execute(
                        "UPDATE party_branches SET status = ?, updated_at = ? WHERE id = ?",
                        (updates["status"], now_iso(), row["branch_id"]),
                    )
        if updated == 0:
            raise ValueError(f"autotest run not found: {run_id}")
        return self.get_autotest_run(run_id)

    def request_autotest_stop(self, run_id: str) -> dict[str, Any]:
        run = self.get_autotest_run(run_id)
        if run["status"] in {"completed", "failed", "stopped"}:
            return run
        return self.update_autotest_run(run_id, stop_requested=True, status="stopping")

    @staticmethod
    def autotest_run_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "owner_user_id": row["owner_user_id"],
            "source_party_id": row["source_party_id"],
            "test_party_id": row["test_party_id"],
            "branch_id": row["branch_id"],
            "checkpoint_id": row["checkpoint_id"],
            "player_model_profile_id": row["player_model_profile_id"],
            "player_prompt": row["player_prompt"],
            "requested_turns": row["requested_turns"],
            "completed_turns": row["completed_turns"],
            "fallback_turns": row["fallback_turns"],
            "status": row["status"],
            "current_phase": row["current_phase"],
            "stop_requested": bool(row["stop_requested"]),
            "last_player_action": row["last_player_action"],
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def party_from_row(self, row: sqlite3.Row, include_related: bool = False) -> PartySummary:
        party = PartySummary(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            title=row["title"],
            scenario_type=row["scenario_type"],
            rp_contract_version=row["rp_contract_version"],
            worldpack_id=row["worldpack_id"],
            player_character_id=row["player_character_id"],
            model_profile_id=row["model_profile_id"],
            state_campaign_id=row["state_campaign_id"],
            status=row["status"],
            dataset_review_status=row["dataset_review_status"],
            dataset_tags=json.loads(row["dataset_tags_json"] or "[]"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        if include_related:
            try:
                party.worldpack = self.get_worldpack(party.worldpack_id)
                party.player_character = self.get_player_character(party.player_character_id)
                party.model_profile = self.get_model_profile(party.model_profile_id)
            except ValueError:
                pass
        return party

    def state_path_for(self, state_campaign_id: str) -> Path:
        return Path(self.settings.party_state_root) / state_campaign_id / "current.json"

    def initialize_party_state(self, party_id: str, pack: WorldPackSummary, character: PlayerCharacterSummary) -> None:
        state_path = self.state_path_for(party_id)
        if state_path.exists():
            return
        seed_path = Path(pack.state_seed_path)
        if seed_path.exists():
            state = json.loads(seed_path.read_text(encoding="utf-8"))
        else:
            state = {
                "meta": {
                    "campaign_id": party_id,
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
        state.setdefault("meta", {})
        state["meta"]["campaign_id"] = party_id
        state["meta"]["state_version"] = 1
        state.setdefault("player", {})
        state["player"]["character_id"] = character.id
        state["player"]["name"] = character.name
        state["player"]["description"] = character.description
        state["player"].setdefault("known_world_facts", [])
        if character.description:
            state["player"]["known_world_facts"].append(
                {"id": "player_character_prompt", "text": character.description, "source": "player_character", "turn": 0}
            )
        state = self.apply_starting_patch(state, character.starting_state_patch_json)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def store_for_party(self, party_id: str, owner_user_id: str | None = None) -> StateStore:
        party = self.get_party(party_id, owner_user_id=owner_user_id)
        return StateStore(self.settings.sqlite_path, party.state_campaign_id, str(self.state_path_for(party.state_campaign_id)))

    def apply_starting_patch(self, state: dict[str, Any], patch_json: str | None) -> dict[str, Any]:
        if not patch_json:
            return state
        data = json.loads(patch_json)
        operations = data.get("patch", data) if isinstance(data, dict) else data
        if not isinstance(operations, list):
            raise ValueError("starting state patch must be a JSON Patch list")
        return apply_patch(state, operations)
