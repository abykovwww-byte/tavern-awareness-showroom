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
    WorldPromptCreate,
    WorldPackSummary,
)
from app.services.nvidia_catalog import (
    fetch_build_nvidia_profiles,
    fetch_integrate_api_profiles,
    static_model_profiles,
)
from app.services.state_store import StateStore


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slug(value: str) -> str:
    clean = re.sub(r"[^\w\-]+", "-", value.strip().lower(), flags=re.UNICODE).strip("-")
    return clean or f"id-{uuid.uuid4().hex[:8]}"


class PartyStore:
    def __init__(self, settings: Settings):
        self.settings = settings
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
                    worldpack_id TEXT NOT NULL REFERENCES worldpacks(id),
                    player_character_id TEXT NOT NULL REFERENCES player_characters(id),
                    model_profile_id TEXT NOT NULL REFERENCES model_profiles(id),
                    state_campaign_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_cache (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def seed_model_profiles(self) -> None:
        self.prune_unused_live_model_profiles()
        for profile in static_model_profiles(self.settings):
            self.upsert_model_profile(profile)
        self.refresh_live_model_profiles_if_due()

    def prune_unused_live_model_profiles(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM model_profiles
                WHERE id NOT IN (SELECT model_profile_id FROM parties)
                  AND (
                    params_json LIKE '%"source": "nvidia_api_live"%'
                    OR params_json LIKE '%"source": "build_nvidia_live"%'
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
        if not self.settings.nvidia_model_catalog_live:
            return
        if self.settings.app_env == "test" or self.settings.nvidia_api_base.startswith("mock://"):
            return
        cache_key = "nvidia_model_catalog_refresh_v2"
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

        seen: set[str] = set()
        for profile in profiles:
            if profile["id"] in seen:
                continue
            seen.add(profile["id"])
            self.upsert_model_profile(profile)
        status = {
            "source": "live" if seen else "static_fallback",
            "error": ";".join(errors),
            "profiles": len(seen),
        }
        self.cache_set(cache_key, status)

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
            lorebook = files.get("sillytavern_lorebook") or files.get("world_info")
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

    def upsert_worldpack(self, pack: WorldPackSummary) -> None:
        timestamp = now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO worldpacks(
                    id, title, slug, status, premise, manifest_path, state_seed_path,
                    lorebook_path, manifest_json, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def create_prompt_worldpack(self, request: WorldPromptCreate) -> WorldPackSummary:
        title = " ".join(request.title.split())[:160] or "Свой мир"
        prompt = " ".join(request.prompt.split())[:6000]
        pack_id = f"prompt-{slug(title)[:42]}-{uuid.uuid4().hex[:8]}"
        generated_root = Path(self.settings.party_state_root) / "_generated_worldpacks" / pack_id
        generated_root.mkdir(parents=True, exist_ok=True)
        state = self.prompt_world_state(pack_id, title, prompt)
        manifest = {
            "id": pack_id,
            "title": title,
            "language": "ru",
            "mode": "prompt world",
            "status": "playable",
            "premise": prompt[:600],
            "player_role": "Персонаж, заданный игроком для этой партии.",
            "generated_by": "rp-light-gui",
            "prompt": prompt,
            "files": {"state_seed": "state-seed.json"},
        }
        manifest_path = generated_root / "manifest.json"
        state_seed_path = generated_root / "state-seed.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        state_seed_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = WorldPackSummary(
            id=pack_id,
            title=title,
            slug=pack_id,
            status="playable",
            premise=prompt[:600],
            manifest_path=str(manifest_path.resolve()),
            state_seed_path=str(state_seed_path.resolve()),
            lorebook_path=None,
            manifest=manifest,
        )
        self.upsert_worldpack(summary)
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

    def list_worldpacks(self) -> list[WorldPackSummary]:
        self.scan_worldpacks()
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM worldpacks ORDER BY title").fetchall()
        return [self.worldpack_from_row(row) for row in rows]

    def get_worldpack(self, worldpack_id: str) -> WorldPackSummary:
        self.scan_worldpacks()
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM worldpacks WHERE id = ?", (worldpack_id,)).fetchone()
        if row is None:
            raise ValueError(f"worldpack not found: {worldpack_id}")
        return self.worldpack_from_row(row)

    def worldpack_from_row(self, row: sqlite3.Row) -> WorldPackSummary:
        return WorldPackSummary(
            id=row["id"],
            title=row["title"],
            slug=row["slug"],
            status=row["status"],
            premise=row["premise"],
            manifest_path=row["manifest_path"],
            state_seed_path=row["state_seed_path"],
            lorebook_path=row["lorebook_path"],
            manifest=json.loads(row["manifest_json"]),
        )

    def player_templates(self, worldpack_id: str) -> list[PlayerTemplate]:
        pack = self.get_worldpack(worldpack_id)
        role = str(pack.manifest.get("player_role") or "Player character")
        return [
            PlayerTemplate(
                id=f"{worldpack_id}:default-player",
                name="World pack player role",
                description=role,
                profile={"source": "manifest.player_role", "worldpack_id": worldpack_id},
            )
        ]

    def list_player_characters(self, worldpack_id: str | None = None) -> list[PlayerCharacterSummary]:
        self.scan_worldpacks()
        sql = "SELECT * FROM player_characters"
        params: tuple[Any, ...] = ()
        if worldpack_id:
            sql += " WHERE worldpack_id = ?"
            params = (worldpack_id,)
        sql += " ORDER BY updated_at DESC"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self.character_from_row(row) for row in rows]

    def create_player_character(self, request: PlayerCharacterCreate) -> PlayerCharacterSummary:
        self.get_worldpack(request.worldpack_id)
        character_id = f"pc_{uuid.uuid4().hex[:12]}"
        timestamp = now_iso()
        profile = dict(request.profile)
        profile.setdefault("name", request.name)
        profile.setdefault("worldpack_id", request.worldpack_id)
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO player_characters(
                    id, worldpack_id, name, description, status,
                    starting_state_patch_json, profile_json, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    character_id,
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
        return self.get_player_character(character_id)

    def get_player_character(self, character_id: str) -> PlayerCharacterSummary:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM player_characters WHERE id = ?", (character_id,)).fetchone()
        if row is None:
            raise ValueError(f"player character not found: {character_id}")
        return self.character_from_row(row)

    def character_from_row(self, row: sqlite3.Row) -> PlayerCharacterSummary:
        return PlayerCharacterSummary(
            id=row["id"],
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
        return sorted(profiles, key=lambda profile: (int(profile.params.get("rank", 9999)), profile.title))

    def get_model_profile(self, model_profile_id: str) -> ModelProfileSummary:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM model_profiles WHERE id = ?", (model_profile_id,)).fetchone()
        if row is None:
            raise ValueError(f"model profile not found: {model_profile_id}")
        return self.model_profile_from_row(row)

    def model_profile_from_row(self, row: sqlite3.Row) -> ModelProfileSummary:
        params = json.loads(row["params_json"])
        return ModelProfileSummary(
            id=row["id"],
            title=row["title"],
            provider=row["provider"],
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
        )

    def list_parties(self) -> list[PartySummary]:
        self.scan_worldpacks()
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM parties ORDER BY updated_at DESC").fetchall()
        return [self.party_from_row(row, include_related=True) for row in rows]

    def create_party(self, request: PartyCreate) -> PartySummary:
        pack = self.get_worldpack(request.worldpack_id)
        character = self.get_player_character(request.player_character_id)
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
                    id, title, worldpack_id, player_character_id, model_profile_id,
                    state_campaign_id, status, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    party_id,
                    request.title,
                    request.worldpack_id,
                    request.player_character_id,
                    request.model_profile_id,
                    party_id,
                    "active",
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_party(party_id)

    def get_party(self, party_id: str) -> PartySummary:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM parties WHERE id = ?", (party_id,)).fetchone()
        if row is None:
            raise ValueError(f"party not found: {party_id}")
        return self.party_from_row(row, include_related=True)

    def activate_party(self, party_id: str) -> PartySummary:
        timestamp = now_iso()
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE parties SET status = 'active', updated_at = ? WHERE id = ?",
                (timestamp, party_id),
            ).rowcount
        if updated == 0:
            raise ValueError(f"party not found: {party_id}")
        return self.get_party(party_id)

    def update_party_model(self, party_id: str, model_profile_id: str) -> PartySummary:
        self.get_model_profile(model_profile_id)
        timestamp = now_iso()
        with self.connect() as connection:
            updated = connection.execute(
                "UPDATE parties SET model_profile_id = ?, updated_at = ? WHERE id = ?",
                (model_profile_id, timestamp, party_id),
            ).rowcount
        if updated == 0:
            raise ValueError(f"party not found: {party_id}")
        return self.get_party(party_id)

    def delete_party(self, party_id: str) -> None:
        party = self.get_party(party_id)
        with self.connect() as connection:
            connection.execute("DELETE FROM parties WHERE id = ?", (party_id,))
            for table in ("turns", "checks", "state_patches", "state_versions", "audit_events", "memory_summaries"):
                connection.execute(f"DELETE FROM {table} WHERE campaign_id = ?", (party.state_campaign_id,))
            connection.execute("DELETE FROM campaigns WHERE id = ?", (party.state_campaign_id,))
        self.delete_party_state_dir(party.state_campaign_id)

    def delete_party_state_dir(self, state_campaign_id: str) -> None:
        root = Path(self.settings.party_state_root).resolve()
        target = (root / state_campaign_id).resolve()
        if target == root or root not in target.parents:
            raise ValueError("party state path is outside party state root")
        if target.exists():
            shutil.rmtree(target)

    def party_from_row(self, row: sqlite3.Row, include_related: bool = False) -> PartySummary:
        party = PartySummary(
            id=row["id"],
            title=row["title"],
            worldpack_id=row["worldpack_id"],
            player_character_id=row["player_character_id"],
            model_profile_id=row["model_profile_id"],
            state_campaign_id=row["state_campaign_id"],
            status=row["status"],
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

    def store_for_party(self, party_id: str) -> StateStore:
        party = self.get_party(party_id)
        return StateStore(self.settings.sqlite_path, party.state_campaign_id, str(self.state_path_for(party.state_campaign_id)))

    def apply_starting_patch(self, state: dict[str, Any], patch_json: str | None) -> dict[str, Any]:
        if not patch_json:
            return state
        data = json.loads(patch_json)
        operations = data.get("patch", data) if isinstance(data, dict) else data
        if not isinstance(operations, list):
            raise ValueError("starting state patch must be a JSON Patch list")
        return apply_patch(state, operations)
