"""Party-scoped department workspace for deterministic training scenarios."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models.schemas import (
    InteractionEvidence,
    NarrativeBundle,
    TrainingWorkspaceEventRequest,
    TrainingWorkspaceEventResponse,
    WorldPackSummary,
)
from app.services.narrative import json_object_content, response_text
from app.services.state_store import StateStore


WORKSPACE_SCHEMA = "rp-training-workspace.v1"
FOLDERS_SCHEMA = "rp-training-workspace-folders.v1"
FILES_SCHEMA = "rp-training-workspace-files.v1"
POLICY_SCHEMA = "rp-training-workspace-policy.v1"
SUPPORTED_RENDERERS = {"text-document", "policy-document", "spreadsheet", "pdf-preview", "image-preview"}
SUPPORTED_MEDIA = {"text", "document", "spreadsheet", "pdf", "image"}
SUPPORTED_EVENTS = {"file_opened", "file_downloaded", "file_reported", "link_opened", "active_content_enabled"}
RESOURCE_CLASSIFICATIONS = {"public_training", "restricted_internal"}
MARKUP_RE = re.compile(r"[<>]|javascript:|data:|vbscript:", re.IGNORECASE)
ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,119}")


@dataclass(frozen=True)
class WorkspaceMaterialization:
    text: str
    public_files: list[dict[str, Any]]
    persistence_records: list[dict[str, Any]]
    valid: bool
    violations: list[str]


class TrainingWorkspaceService:
    _catalog_cache: dict[str, tuple[tuple[tuple[str, int], ...], dict[str, Any]]] = {}

    def __init__(self, worldpack: WorldPackSummary | None, store: StateStore, *, enabled: bool = True):
        self.worldpack = worldpack
        self.store = store
        self.catalog = self._load_catalog(worldpack) if worldpack and enabled else None

    @property
    def enabled(self) -> bool:
        return self.catalog is not None

    @classmethod
    def supports(cls, worldpack: WorldPackSummary | None) -> bool:
        return bool(worldpack and cls._load_catalog(worldpack) is not None)

    @classmethod
    def supports_anonymous_showroom(cls, worldpack: WorldPackSummary | None) -> bool:
        if not worldpack:
            return False
        catalog = cls._load_catalog(worldpack)
        return bool(
            catalog
            and all(
                not blueprint.get("resource_path")
                or blueprint.get("resource_classification") == "public_training"
                for blueprint in catalog["blueprints"].values()
            )
        )

    def contract_for_state(self, state: dict[str, Any], *, party_start: bool = False) -> dict[str, Any] | None:
        if not self.catalog:
            return None
        turn = int(state.get("meta", {}).get("turn", 0) or 0)
        due: list[dict[str, Any]] = []
        existing = {item.get("file_key") for item in self.store.training_workspace_snapshot(turn)}
        for blueprint in self.catalog["blueprints"].values():
            lifecycle = blueprint["lifecycle"]
            at_start = lifecycle["materialize"] == "party_start"
            at_turn = lifecycle["materialize"] == "turn" and int(lifecycle["turn"]) == turn
            if (party_start and at_start) or (not party_start and at_turn):
                if blueprint["file_key"] not in existing:
                    due.append(
                        {
                            "file_key": blueprint["file_key"],
                            "blueprint_id": blueprint["id"],
                            "slots": copy.deepcopy(blueprint["llm_slots"]),
                        }
                    )
        if not due:
            return None
        return {
            "schema_version": "rp-gateway.training-workspace-contract.v1",
            "materialized_turn": turn,
            "files": due,
        }

    @staticmethod
    def prompt_block(contract: dict[str, Any] | None) -> str:
        if not contract:
            return ""
        return "\n".join(
            [
                "TRAINING_WORKSPACE_CONTRACT",
                "Return JSON only with schema_version rp-gateway.narrative-bundle.v2.",
                "Return narrative_text, artifacts, and workspace_files.",
                "For workspace_files use exactly the supplied file_key and blueprint_id and fill only declared string slots.",
                "Never choose folders, paths, renderers, MIME types, file classification, correctness, answer keys, or scoring.",
                json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
            ]
        )

    def materialize_response(
        self,
        response: dict[str, Any],
        contract: dict[str, Any] | None,
    ) -> WorkspaceMaterialization:
        if not contract:
            return WorkspaceMaterialization(response_text(response), [], [], True, [])
        try:
            bundle = NarrativeBundle.model_validate_json(self._json_content(response_text(response)))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            return WorkspaceMaterialization(response_text(response), [], [], False, [f"invalid workspace narrative bundle: {str(exc)[:500]}"])
        expected = {item["file_key"]: item for item in contract["files"]}
        supplied = {item.file_key: item for item in bundle.workspace_files}
        violations: list[str] = []
        if set(supplied) != set(expected):
            violations.append("workspace_files do not exactly match Gateway contract")
        records: list[dict[str, Any]] = []
        public_files: list[dict[str, Any]] = []
        for file_key, expected_item in expected.items():
            content = supplied.get(file_key)
            if content is None or content.blueprint_id != expected_item["blueprint_id"]:
                violations.append(f"workspace blueprint does not match contract: {file_key}")
                continue
            blueprint = self.catalog["blueprints"][content.blueprint_id]
            slot_contract = blueprint["llm_slots"]
            if set(content.slots) != set(slot_contract):
                violations.append(f"workspace slots do not match contract: {file_key}")
                continue
            for slot_id, value in content.slots.items():
                limit = int(slot_contract[slot_id]["max_length"])
                if (slot_contract[slot_id].get("required", True) and not value.strip()) or len(value) > limit:
                    violations.append(f"invalid workspace slot: {file_key}.{slot_id}")
                if MARKUP_RE.search(value):
                    violations.append(f"unsafe workspace slot: {file_key}.{slot_id}")
            if not violations:
                public, record = self._snapshot(blueprint, content.slots, int(contract["materialized_turn"]))
                public_files.append(public)
                records.append(record)
        return WorkspaceMaterialization(bundle.narrative_text, public_files, records, not violations, violations)

    def fallback_materialization(self, contract: dict[str, Any] | None, text: str = "") -> WorkspaceMaterialization:
        if not contract:
            return WorkspaceMaterialization(text, [], [], True, [])
        public_files: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        for item in contract["files"]:
            blueprint = self.catalog["blueprints"][item["blueprint_id"]]
            public, record = self._snapshot(blueprint, blueprint["fallback_content"], int(contract["materialized_turn"]))
            public_files.append(public)
            records.append(record)
        return WorkspaceMaterialization(text, public_files, records, True, [])

    def snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        turn = int(state.get("meta", {}).get("turn", 0) or 0)
        return {
            "schema_version": "rp-gateway.training-workspace.v1",
            "folders": copy.deepcopy(self.catalog["folders"]) if self.catalog else [],
            "files": self.store.training_workspace_snapshot(turn) if self.catalog else [],
        }

    def pending_evidence(self) -> list[InteractionEvidence]:
        return [InteractionEvidence.model_validate(item) for item in self.store.unconsumed_training_workspace_evidence()]

    def record_event(self, request: TrainingWorkspaceEventRequest, state: dict[str, Any]) -> TrainingWorkspaceEventResponse:
        item = self.store.training_workspace_file(request.file_id)
        if item is None:
            raise ValueError("training workspace file not found")
        public = item["public"]
        policy = item["policy"]
        if int(public.get("file_revision", 0)) != request.file_revision:
            raise ValueError("stale training workspace file revision")
        current_ids = {entry.get("file_id") for entry in self.snapshot(state)["files"]}
        if request.file_id not in current_ids:
            raise ValueError("training workspace file is not currently available")
        event_policy = policy.get("events", {}).get(request.event_type)
        if not isinstance(event_policy, dict):
            raise ValueError("training workspace action is not allowed")
        result = self.store.record_training_workspace_event(
            event_id=request.event_id,
            file_id=request.file_id,
            file_revision=request.file_revision,
            event_type=request.event_type,
            evidence={
                "evidence": str(event_policy.get("evidence") or ""),
                "score_rule_id": str(event_policy.get("score_rule_id") or ""),
                "score_once": bool(event_policy.get("score_once", True)),
                "decision_result": str(event_policy.get("decision_result") or "neutral"),
            },
        )
        return TrainingWorkspaceEventResponse.model_validate(result)

    def resource_for_file(
        self,
        file_id: str,
        state: dict[str, Any],
        *,
        public_only: bool = False,
    ) -> tuple[Path, str, str]:
        item = self.store.training_workspace_file(file_id)
        if item is None or file_id not in {entry.get("file_id") for entry in self.snapshot(state)["files"]}:
            raise ValueError("training workspace file is not currently available")
        relative = item["policy"].get("resource_path")
        if not relative or not self.worldpack:
            raise ValueError("training workspace file has no authored resource")
        if public_only and item["policy"].get("resource_classification") != "public_training":
            raise ValueError("training workspace resource is not available to anonymous Showroom visitors")
        root = Path(self.worldpack.manifest_path).resolve().parent
        path = self._safe_path(root, relative)
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return path, mime_type, str(item["public"].get("display_name") or path.name) + str(item["public"].get("extension") or "")

    def _snapshot(
        self,
        blueprint: dict[str, Any],
        slots: dict[str, str],
        materialized_turn: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        lifecycle = blueprint["lifecycle"]
        digest = hashlib.sha256(
            f"{self.store.campaign_id}:{blueprint['file_key']}:{blueprint['revision']}".encode()
        ).hexdigest()[:24]
        public = {
            "schema_version": "rp-gateway.training-workspace-file.v1",
            "file_id": f"workspace_{digest}",
            "file_key": blueprint["file_key"],
            "file_revision": int(blueprint["revision"]),
            "blueprint_id": blueprint["id"],
            "folder_id": blueprint["folder_id"],
            "display_name": blueprint["display_name"],
            "extension": blueprint["extension"],
            "renderer": blueprint["renderer"],
            "media_family": blueprint["media_family"],
            "available_from_turn": int(lifecycle.get("available_from_turn") or materialized_turn),
            "available_until_turn": lifecycle.get("available_until_turn"),
            "materialized_turn": materialized_turn,
            "slots": dict(slots),
            "actions": list(blueprint["actions"]),
        }
        resource_path = str(blueprint.get("resource_path") or "")
        if resource_path and self.worldpack:
            resource = self._safe_path(Path(self.worldpack.manifest_path).resolve().parent, resource_path)
            public["resource_sha256"] = hashlib.sha256(resource.read_bytes()).hexdigest()
        policy = {
            "events": copy.deepcopy(self.catalog["policy"].get(blueprint["id"]) or {}),
            "resource_path": resource_path,
            "resource_classification": str(blueprint.get("resource_classification") or ""),
        }
        return public, {"public": public, "policy": policy}

    @staticmethod
    def _json_content(value: str) -> str:
        return json_object_content(value)

    @classmethod
    def _load_catalog(cls, worldpack: WorldPackSummary) -> dict[str, Any] | None:
        manifest = worldpack.manifest if isinstance(worldpack.manifest, dict) else {}
        config = manifest.get("training_workspace")
        if not isinstance(config, dict):
            return None
        if config.get("schema_version") != WORKSPACE_SCHEMA:
            raise ValueError("unsupported training workspace manifest schema")
        root = Path(worldpack.manifest_path).resolve().parent
        folder_path = cls._safe_path(root, config.get("folder_catalog"))
        file_index_path = cls._safe_path(root, config.get("file_catalog"))
        policy_path = cls._safe_path(root, config.get("interaction_policy"))
        folders_doc = cls._read_json(folder_path)
        files_doc = cls._read_json(file_index_path)
        entries = files_doc.get("files")
        if folders_doc.get("schema_version") != FOLDERS_SCHEMA or not isinstance(folders_doc.get("folders"), list):
            raise ValueError("invalid training workspace folder catalog")
        if files_doc.get("schema_version") != FILES_SCHEMA or not isinstance(entries, list):
            raise ValueError("invalid training workspace file catalog")
        folders = cls._validate_folders(folders_doc["folders"])
        paths = [folder_path, file_index_path, policy_path]
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("invalid training workspace file entry")
            paths.append(cls._safe_path(root, entry.get("file")))
        blueprints: dict[str, dict[str, Any]] = {}
        file_keys: set[str] = set()
        for entry, path in zip(entries, paths[3:]):
            blueprint = cls._read_json(path)
            cls._validate_blueprint(blueprint, set(folders))
            if blueprint.get("resource_path"):
                paths.append(cls._safe_path(root, blueprint["resource_path"]))
            if blueprint["id"] != entry.get("id") or blueprint["id"] in blueprints:
                raise ValueError("duplicate or mismatched training workspace blueprint id")
            if blueprint["file_key"] in file_keys:
                raise ValueError("duplicate training workspace file key")
            blueprints[blueprint["id"]] = blueprint
            file_keys.add(blueprint["file_key"])
        signature = tuple((str(path), path.stat().st_mtime_ns) for path in paths)
        cache_key = str(file_index_path)
        cached = cls._catalog_cache.get(cache_key)
        if cached and cached[0] == signature:
            return cached[1]
        policy_doc = cls._read_json(policy_path)
        policy = policy_doc.get("files")
        if policy_doc.get("schema_version") != POLICY_SCHEMA or not isinstance(policy, dict):
            raise ValueError("invalid training workspace policy")
        cls._validate_policy(policy, blueprints)
        catalog = {"folders": list(folders.values()), "blueprints": blueprints, "policy": policy}
        cls._catalog_cache[cache_key] = (signature, catalog)
        return catalog

    @classmethod
    def _validate_folders(cls, folders: list[Any]) -> dict[str, dict[str, Any]]:
        normalized: dict[str, dict[str, Any]] = {}
        for item in folders:
            if not isinstance(item, dict) or not ID_RE.fullmatch(str(item.get("id") or "")):
                raise ValueError("invalid training workspace folder")
            folder_id = str(item["id"])
            if folder_id in normalized or not str(item.get("label") or "").strip():
                raise ValueError("duplicate or unlabeled training workspace folder")
            normalized[folder_id] = {
                "id": folder_id,
                "parent_id": item.get("parent_id"),
                "label": str(item["label"]).strip(),
                "sort_order": int(item.get("sort_order") or 0),
            }
        for folder in normalized.values():
            parent = folder.get("parent_id")
            if parent is not None and parent not in normalized:
                raise ValueError("orphan training workspace folder")
            seen = {folder["id"]}
            while parent is not None:
                if parent in seen:
                    raise ValueError("cyclic training workspace folders")
                seen.add(parent)
                parent = normalized[parent].get("parent_id")
        return normalized

    @classmethod
    def _validate_blueprint(cls, item: dict[str, Any], folder_ids: set[str]) -> None:
        if not ID_RE.fullmatch(str(item.get("id") or "")) or not ID_RE.fullmatch(str(item.get("file_key") or "")):
            raise ValueError("invalid training workspace blueprint id")
        if int(item.get("revision") or 0) < 1 or item.get("folder_id") not in folder_ids:
            raise ValueError("invalid training workspace blueprint revision or folder")
        if item.get("renderer") not in SUPPORTED_RENDERERS or item.get("media_family") not in SUPPORTED_MEDIA:
            raise ValueError("unsupported training workspace renderer or media family")
        extension = str(item.get("extension") or "")
        if not re.fullmatch(r"\.[a-z0-9]{1,8}", extension) or not str(item.get("display_name") or "").strip():
            raise ValueError("invalid training workspace display name or extension")
        lifecycle = item.get("lifecycle")
        if not isinstance(lifecycle, dict) or lifecycle.get("materialize") not in {"party_start", "turn"}:
            raise ValueError("invalid training workspace lifecycle")
        if lifecycle.get("materialize") == "turn" and int(lifecycle.get("turn") or 0) < 1:
            raise ValueError("turn materialization requires a positive turn")
        slots = item.get("llm_slots")
        fallback = item.get("fallback_content")
        if not isinstance(slots, dict) or not isinstance(fallback, dict) or set(slots) != set(fallback):
            raise ValueError("invalid training workspace slots or fallback")
        for slot_id, contract in slots.items():
            value = fallback.get(slot_id)
            if not re.fullmatch(r"[a-z0-9_]{1,80}", str(slot_id)) or not isinstance(contract, dict):
                raise ValueError("invalid training workspace slot")
            limit = int(contract.get("max_length") or 0)
            if not isinstance(value, str) or not value.strip() or limit < 1 or limit > 10000 or len(value) > limit:
                raise ValueError("invalid training workspace fallback content")
            if MARKUP_RE.search(value):
                raise ValueError("unsafe training workspace fallback content")
        actions = item.get("actions")
        if not isinstance(actions, list) or not set(actions).issubset(SUPPORTED_EVENTS):
            raise ValueError("invalid training workspace actions")
        resource_path = item.get("resource_path")
        classification = item.get("resource_classification")
        if resource_path and classification not in RESOURCE_CLASSIFICATIONS:
            raise ValueError("training workspace resource requires a valid classification")
        if not resource_path and classification is not None:
            raise ValueError("training workspace resource classification requires a resource")

    @classmethod
    def _validate_policy(cls, policy: dict[str, Any], blueprints: dict[str, dict[str, Any]]) -> None:
        if set(policy) != set(blueprints):
            raise ValueError("training workspace policy must cover every file")
        for blueprint_id, events in policy.items():
            if not isinstance(events, dict) or not set(events).issubset(set(blueprints[blueprint_id]["actions"])):
                raise ValueError("invalid training workspace event policy")
            for event_type, rule in events.items():
                if event_type not in SUPPORTED_EVENTS or not isinstance(rule, dict):
                    raise ValueError("invalid training workspace event rule")
                if set(rule) != {"evidence", "score_rule_id", "score_once", "decision_result"}:
                    raise ValueError("invalid training workspace event rule fields")
                if rule["decision_result"] not in {"pass", "fail", "neutral"} or not isinstance(rule["score_once"], bool):
                    raise ValueError("invalid training workspace scoring policy")
                if rule["decision_result"] != "neutral" and not ID_RE.fullmatch(str(rule["score_rule_id"] or "")):
                    raise ValueError("scored training workspace event requires score_rule_id")

    @staticmethod
    def _safe_path(root: Path, relative: Any) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("training workspace path is missing")
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise ValueError("training workspace path escapes WorldPack")
        if not target.is_file():
            raise ValueError(f"training workspace file not found: {relative}")
        return target

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid training workspace JSON: {path.name}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"training workspace JSON must be an object: {path.name}")
        return value
