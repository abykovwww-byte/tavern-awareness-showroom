"""Safe, party-scoped interactive artifacts for deterministic training worlds."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from app.models.schemas import (
    InteractionEvidence,
    NarrativeBundle,
    TrainingArtifactEventRequest,
    TrainingArtifactEventResponse,
    TrainingArtifactSnapshot,
    WorldPackSummary,
)
from app.services.narrative import response_text, with_text
from app.services.state_store import StateStore


ARTIFACT_SCHEMA = "rp-training-artifacts.v1"
INDEX_SCHEMA = "rp-training-site-catalog.v1"
SUPPORTED_RENDERERS = {
    "credential-form",
    "otp-form",
    "file-share",
    "document-approval",
    "payment-review",
    "tracking-form",
    "meeting-join",
    "survey-form",
    "support-download",
}
SUPPORTED_THEMES = {"office-blue", "office-neutral", "service-green", "warning-amber", "minimal-light"}
SUPPORTED_FIELD_TYPES = {"text", "password", "otp", "email"}
SUPPORTED_ACTIONS = {"submit", "close", "report"}
SUPPORTED_POLICY_EVENTS = {"link_opened", "form_submitted", "credentials_submitted", "site_closed", "reported"}
SAFE_HOST_SUFFIXES = (".test", ".invalid", ".example")
MARKUP_RE = re.compile(r"[<>]|javascript:|data:|vbscript:", re.IGNORECASE)


@dataclass(frozen=True)
class ArtifactMaterialization:
    response: dict[str, Any]
    text: str
    public_artifacts: list[dict[str, Any]]
    persistence_records: list[dict[str, Any]]
    valid: bool
    violations: list[str]


class TrainingArtifactService:
    """Loads authored blueprints and keeps model output inside their allowlist."""

    _catalog_cache: dict[str, tuple[tuple[tuple[str, int], ...], dict[str, Any]]] = {}

    def __init__(self, worldpack: WorldPackSummary | None, store: StateStore, *, enabled: bool = True):
        self.worldpack = worldpack
        self.store = store
        self.catalog = self._load_catalog(worldpack) if worldpack and enabled else None

    @classmethod
    def supports(cls, worldpack: WorldPackSummary | None) -> bool:
        return bool(worldpack and cls._load_catalog(worldpack) is not None)

    @property
    def enabled(self) -> bool:
        return self.catalog is not None

    def contract_for_state(self, state: dict[str, Any]) -> dict[str, Any] | None:
        if not self.catalog:
            return None
        turn = int(state.get("meta", {}).get("turn", 0) or 0)
        surface = self.catalog["surfaces"].get(turn)
        if not surface:
            return None
        blueprint = self.catalog["blueprints"][surface["blueprint_id"]]
        return {
            "schema_version": "rp-gateway.training-artifact-contract.v1",
            "surface_turn": turn,
            "artifact_key": surface["artifact_key"],
            "blueprint_id": blueprint["id"],
            "display_url": blueprint["fixed"]["display_url"],
            "renderer": blueprint["renderer"],
            "theme": blueprint["theme"],
            "slots": copy.deepcopy(blueprint["llm_slots"]),
        }

    @staticmethod
    def prompt_block(contract: dict[str, Any] | None) -> str:
        if not contract:
            return ""
        return "\n".join(
            [
                "TRAINING_ARTIFACT_CONTRACT",
                "Return JSON only, matching rp-gateway.narrative-bundle.v1.",
                "Use exactly one artifact with the supplied artifact_key and blueprint_id.",
                "Fill only the declared string slots and keep every max_length.",
                "The narrative email/message must contain the exact fixed display_url.",
                "Never output HTML, CSS, JavaScript, data URLs, remote assets, credentials, scoring, correctness, or remediation.",
                json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
            ]
        )

    def materialize_response(
        self,
        response: dict[str, Any],
        contract: dict[str, Any] | None,
    ) -> ArtifactMaterialization:
        if not contract:
            return ArtifactMaterialization(response, response_text(response), [], [], True, [])
        violations: list[str] = []
        try:
            bundle = NarrativeBundle.model_validate_json(self._json_content(response_text(response)))
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            return ArtifactMaterialization(
                response,
                response_text(response),
                [],
                [],
                False,
                [f"invalid narrative bundle: {str(exc)[:500]}"],
            )
        if len(bundle.artifacts) != 1:
            violations.append("narrative bundle must contain exactly one artifact")
        artifact = bundle.artifacts[0] if len(bundle.artifacts) == 1 else None
        if artifact and artifact.artifact_key != contract["artifact_key"]:
            violations.append("artifact_key does not match Gateway contract")
        if artifact and artifact.blueprint_id != contract["blueprint_id"]:
            violations.append("blueprint_id does not match Gateway contract")
        if contract["display_url"] not in bundle.narrative_text:
            violations.append("narrative text does not contain the fixed display_url")
        if MARKUP_RE.search(bundle.narrative_text):
            violations.append("narrative bundle contains forbidden markup or executable URL syntax")
        if artifact:
            required_slots = set(contract["slots"])
            supplied_slots = set(artifact.slots)
            if supplied_slots != required_slots:
                violations.append("artifact slots do not exactly match the Gateway contract")
            for slot_id, slot_contract in contract["slots"].items():
                value = artifact.slots.get(slot_id, "")
                if slot_contract.get("required") and not value.strip():
                    violations.append(f"required artifact slot is empty: {slot_id}")
                if len(value) > int(slot_contract.get("max_length") or 0):
                    violations.append(f"artifact slot exceeds max_length: {slot_id}")
                if MARKUP_RE.search(value) or "://" in value:
                    violations.append(f"artifact slot contains markup or URL syntax: {slot_id}")
        if violations or artifact is None:
            return ArtifactMaterialization(response, bundle.narrative_text, [], [], False, violations)
        public, persistence = self._snapshot(contract, artifact.slots)
        updated = with_text(response, bundle.narrative_text)
        choices = list(updated.get("choices") or [])
        first = dict(choices[0])
        message = dict(first.get("message") or {})
        message["artifacts"] = [public]
        first["message"] = message
        choices[0] = first
        updated["choices"] = choices
        return ArtifactMaterialization(updated, bundle.narrative_text, [public], [persistence], True, [])

    def fallback_materialization(
        self,
        response: dict[str, Any],
        text: str,
        contract: dict[str, Any] | None,
    ) -> ArtifactMaterialization:
        updated = with_text(response, text)
        if not contract:
            return ArtifactMaterialization(updated, text, [], [], True, [])
        blueprint = self.catalog["blueprints"][contract["blueprint_id"]]
        fallback_text = text
        if contract["display_url"] not in fallback_text:
            fallback_text = f"{fallback_text.rstrip()}\n\nСсылка: {contract['display_url']}"
            updated = with_text(updated, fallback_text)
        public, persistence = self._snapshot(contract, blueprint["fallback_content"])
        choices = list(updated.get("choices") or [])
        first = dict(choices[0])
        message = dict(first.get("message") or {})
        message["artifacts"] = [public]
        first["message"] = message
        choices[0] = first
        updated["choices"] = choices
        return ArtifactMaterialization(updated, fallback_text, [public], [persistence], True, [])

    def pending_evidence(self) -> list[InteractionEvidence]:
        return [InteractionEvidence.model_validate(item) for item in self.store.unconsumed_training_artifact_evidence()]

    def record_event(self, request: TrainingArtifactEventRequest) -> TrainingArtifactEventResponse:
        artifact = self.store.training_artifact(request.artifact_id)
        if artifact is None:
            raise ValueError("training artifact not found")
        public = artifact["public"]
        hidden = artifact["policy"]
        if int(public.get("artifact_revision", 0)) != request.artifact_revision:
            raise ValueError("stale training artifact revision")
        current_turn = int(self.store.get_state().get("meta", {}).get("turn", 0) or 0)
        if int(public.get("surface_turn", 0)) != current_turn:
            raise ValueError("training artifact is outside the active authored surface")
        field_ids = set(public.get("field_ids") or [])
        if not set(request.filled_field_ids).issubset(field_ids):
            raise ValueError("unknown training artifact field id")
        actions = set(public.get("actions") or [])
        required_action = {
            "form_submitted": "submit",
            "site_closed": "close",
            "reported": "report",
        }.get(request.event_type)
        if required_action and required_action not in actions:
            raise ValueError("training artifact action is not allowed")
        event_type = request.event_type
        if event_type == "form_submitted":
            credential_fields = set(hidden.get("credential_field_ids") or [])
            if credential_fields.intersection(request.filled_field_ids):
                event_type = "credentials_submitted"
        event_policy = dict((hidden.get("events") or {}).get(event_type) or {})
        result = self.store.record_training_artifact_event(
            event_id=request.event_id,
            artifact_id=request.artifact_id,
            artifact_revision=request.artifact_revision,
            event_type=event_type,
            filled_field_ids=request.filled_field_ids,
            evidence={
                "evidence": str(event_policy.get("evidence") or ""),
                "score_rule_id": str(event_policy.get("score_rule_id") or ""),
                "score_once": bool(event_policy.get("score_once", True)),
                "decision_result": str(event_policy.get("decision_result") or "neutral"),
            },
        )
        return TrainingArtifactEventResponse(**result)

    def _snapshot(self, contract: dict[str, Any], slots: dict[str, str]) -> tuple[dict[str, Any], dict[str, Any]]:
        blueprint = self.catalog["blueprints"][contract["blueprint_id"]]
        fixed = blueprint["fixed"]
        digest = hashlib.sha256(
            f"{self.store.campaign_id}:{contract['surface_turn']}:{contract['artifact_key']}:{blueprint['revision']}".encode()
        ).hexdigest()[:24]
        public = TrainingArtifactSnapshot(
            artifact_id=f"artifact_{digest}",
            artifact_key=contract["artifact_key"],
            artifact_revision=int(blueprint["revision"]),
            surface_turn=int(contract["surface_turn"]),
            blueprint_id=blueprint["id"],
            renderer=blueprint["renderer"],
            theme=blueprint["theme"],
            display_url=fixed["display_url"],
            field_ids=list(fixed.get("field_ids") or []),
            field_types=dict(fixed.get("field_types") or {}),
            actions=list(fixed.get("actions") or []),
            slots=dict(slots),
        ).model_dump(mode="json")
        policy = {
            "credential_field_ids": list(fixed.get("credential_field_ids") or []),
            "events": copy.deepcopy(self.catalog["policy"].get(blueprint["id"]) or {}),
        }
        return public, {"public": public, "policy": policy}

    @staticmethod
    def _json_content(value: str) -> str:
        text = value.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    @classmethod
    def _load_catalog(cls, worldpack: WorldPackSummary) -> dict[str, Any] | None:
        manifest = worldpack.manifest if isinstance(worldpack.manifest, dict) else {}
        config = manifest.get("training_artifacts")
        if not isinstance(config, dict):
            return None
        if config.get("schema_version") != ARTIFACT_SCHEMA:
            raise ValueError("unsupported training artifact manifest schema")
        root = Path(worldpack.manifest_path).resolve().parent
        index_path = cls._safe_path(root, config.get("site_catalog"))
        policy_path = cls._safe_path(root, config.get("interaction_policy"))
        index = cls._read_json(index_path)
        blueprint_entries = index.get("blueprints") if isinstance(index, dict) else None
        if index.get("schema_version") != INDEX_SCHEMA or not isinstance(blueprint_entries, list):
            raise ValueError("invalid training artifact site catalog")
        paths = [index_path, policy_path]
        for entry in blueprint_entries:
            if not isinstance(entry, dict):
                raise ValueError("invalid training artifact blueprint entry")
            paths.append(cls._safe_path(root, entry.get("file")))
        signature = tuple((str(path), path.stat().st_mtime_ns) for path in paths)
        cache_key = str(index_path)
        cached = cls._catalog_cache.get(cache_key)
        if cached and cached[0] == signature:
            return cached[1]
        blueprints: dict[str, dict[str, Any]] = {}
        for entry, path in zip(blueprint_entries, paths[2:]):
            blueprint = cls._read_json(path)
            cls._validate_blueprint(blueprint)
            if blueprint["id"] != entry.get("id") or blueprint["id"] in blueprints:
                raise ValueError("duplicate or mismatched training artifact blueprint id")
            blueprints[blueprint["id"]] = blueprint
        expected_count = int(config.get("default_site_count") or 0)
        if expected_count and len(blueprints) != expected_count:
            raise ValueError("training artifact catalog does not match default_site_count")
        surfaces: dict[int, dict[str, Any]] = {}
        for surface in index.get("surfaces") or []:
            if not isinstance(surface, dict):
                raise ValueError("invalid training artifact surface")
            turn = int(surface.get("turn") or 0)
            blueprint_id = str(surface.get("blueprint_id") or "")
            artifact_key = str(surface.get("artifact_key") or "")
            if turn < 1 or turn in surfaces or blueprint_id not in blueprints:
                raise ValueError("invalid or duplicate training artifact surface turn")
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", artifact_key):
                raise ValueError("invalid training artifact key")
            surfaces[turn] = {"turn": turn, "blueprint_id": blueprint_id, "artifact_key": artifact_key}
        policy = cls._read_json(policy_path)
        if not isinstance(policy, dict) or policy.get("schema_version") != "rp-training-site-policy.v1":
            raise ValueError("invalid training artifact interaction policy")
        event_policy = policy.get("blueprints")
        if not isinstance(event_policy, dict):
            raise ValueError("training artifact interaction policy must contain blueprints")
        cls._validate_policy(event_policy, blueprints, surfaces)
        catalog = {"blueprints": blueprints, "surfaces": surfaces, "policy": event_policy}
        cls._catalog_cache[cache_key] = (signature, catalog)
        return catalog

    @classmethod
    def _validate_blueprint(cls, blueprint: Any) -> None:
        if not isinstance(blueprint, dict):
            raise ValueError("training artifact blueprint must be an object")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", str(blueprint.get("id") or "")):
            raise ValueError("invalid training artifact blueprint id")
        if int(blueprint.get("revision") or 0) < 1:
            raise ValueError("invalid training artifact blueprint revision")
        if blueprint.get("renderer") not in SUPPORTED_RENDERERS or blueprint.get("theme") not in SUPPORTED_THEMES:
            raise ValueError("unknown training artifact renderer or theme")
        fixed = blueprint.get("fixed")
        slots = blueprint.get("llm_slots")
        fallback = blueprint.get("fallback_content")
        if not isinstance(fixed, dict) or not isinstance(slots, dict) or not isinstance(fallback, dict):
            raise ValueError("training artifact blueprint sections are missing")
        if any(key in blueprint for key in ("policy", "events", "score", "decision_result", "score_rule_id")):
            raise ValueError("server-only training artifact policy leaked into a public blueprint")
        cls._validate_url(str(fixed.get("display_url") or ""))
        field_ids = list(fixed.get("field_ids") or [])
        if len(field_ids) != len(set(field_ids)) or any(not re.fullmatch(r"[a-z0-9-]{1,80}", str(item)) for item in field_ids):
            raise ValueError("invalid or duplicate training artifact field id")
        credential_ids = set(fixed.get("credential_field_ids") or [])
        if not credential_ids.issubset(set(field_ids)):
            raise ValueError("unknown credential field id")
        field_types = fixed.get("field_types") or {}
        if not isinstance(field_types, dict) or set(field_types) != set(field_ids):
            raise ValueError("training artifact field types must exactly cover declared fields")
        if any(value not in SUPPORTED_FIELD_TYPES for value in field_types.values()):
            raise ValueError("unknown training artifact field type")
        actions = fixed.get("actions") or []
        if len(actions) != len(set(actions)) or not set(actions).issubset(SUPPORTED_ACTIONS):
            raise ValueError("unknown training artifact action")
        if set(fallback) != set(slots):
            raise ValueError("fallback content must exactly cover artifact slots")
        for slot_id, contract in slots.items():
            if not re.fullmatch(r"[a-z0-9_]{1,80}", str(slot_id)) or not isinstance(contract, dict):
                raise ValueError("invalid training artifact slot")
            max_length = int(contract.get("max_length") or 0)
            value = fallback.get(slot_id)
            if max_length < 1 or max_length > 2000 or not isinstance(value, str) or not value.strip() or len(value) > max_length:
                raise ValueError("invalid training artifact fallback content")
            if MARKUP_RE.search(value) or "://" in value:
                raise ValueError("unsafe training artifact fallback content")

    @classmethod
    def _validate_policy(
        cls,
        policy: dict[str, Any],
        blueprints: dict[str, dict[str, Any]],
        surfaces: dict[int, dict[str, Any]],
    ) -> None:
        if set(policy) != set(blueprints):
            raise ValueError("training artifact policy must exactly cover catalog blueprints")
        scheduled_ids = {surface["blueprint_id"] for surface in surfaces.values()}
        for blueprint_id, events in policy.items():
            if not isinstance(events, dict) or not set(events).issubset(SUPPORTED_POLICY_EVENTS):
                raise ValueError("invalid training artifact event policy")
            blueprint = blueprints[blueprint_id]
            actions = set(blueprint["fixed"].get("actions") or [])
            for event_type, rule in events.items():
                if not isinstance(rule, dict) or set(rule) != {
                    "evidence", "score_rule_id", "score_once", "decision_result"
                }:
                    raise ValueError("invalid training artifact event rule")
                evidence = rule.get("evidence")
                score_rule_id = rule.get("score_rule_id")
                if not isinstance(evidence, str) or len(evidence) > 240 or MARKUP_RE.search(evidence):
                    raise ValueError("invalid training artifact event evidence")
                if not isinstance(score_rule_id, str) or (
                    score_rule_id and not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,119}", score_rule_id)
                ):
                    raise ValueError("invalid training artifact score rule id")
                if not isinstance(rule.get("score_once"), bool):
                    raise ValueError("invalid training artifact score_once value")
                if rule.get("decision_result") not in {"pass", "fail", "neutral"}:
                    raise ValueError("invalid training artifact decision result")
                if rule.get("decision_result") != "neutral" and not score_rule_id:
                    raise ValueError("scored training artifact event requires score_rule_id")
                required_action = {
                    "form_submitted": "submit",
                    "credentials_submitted": "submit",
                    "reported": "report",
                    "site_closed": "close",
                }.get(event_type)
                if required_action and required_action not in actions:
                    raise ValueError("training artifact event policy references an unavailable action")
            if blueprint_id in scheduled_ids and not events:
                raise ValueError("scheduled training artifact blueprint requires event policy")

    @staticmethod
    def _validate_url(value: str) -> None:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not hostname.endswith(SAFE_HOST_SUFFIXES) or parsed.username or parsed.password:
            raise ValueError("training artifact URL must use HTTPS and a reserved domain")

    @staticmethod
    def _safe_path(root: Path, relative: Any) -> Path:
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError("training artifact path is missing")
        target = (root / relative).resolve()
        if target != root and root not in target.parents:
            raise ValueError("training artifact path escapes WorldPack")
        if not target.is_file():
            raise ValueError(f"training artifact file not found: {relative}")
        return target

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid training artifact JSON: {path.name}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"training artifact JSON must be an object: {path.name}")
        return value
