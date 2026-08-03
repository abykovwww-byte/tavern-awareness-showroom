"""WorldPack-owned deterministic runtime for generic training scenarios."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.models.schemas import InteractionEvidence, PatchOperation, StatePatch, WorldPackSummary
from app.services.state_store import StateStore


RUNTIME_SCHEMA = "rp-training-runtime.v1"
PROGRAM_SCHEMA = "rp-training-program.v1"
ASSESSMENT_SCHEMA = "rp-training-assessment.v1"
FALLBACKS_SCHEMA = "rp-training-fallbacks.v1"
SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,119}$")
EFFECT_TYPES = {"increment", "set", "append_evidence"}
PLACEHOLDER_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
ROLE_STOP_WORDS = {
    "работ",
    "рабочи",
    "компан",
    "ответ",
    "действ",
    "сотруд",
    "специ",
}


class TrainingRuntimeService:
    """Loads, snapshots and executes a declarative training program."""

    def __init__(self, worldpack: WorldPackSummary | None, store: StateStore):
        self.worldpack = worldpack
        self.store = store
        snapshot = store.training_runtime_snapshot()
        loaded = self._load_contract(worldpack) if snapshot is None and worldpack else None
        self.contract = snapshot or (store.training_runtime_snapshot(loaded) if loaded else None)
        if self.contract:
            self._validate_contract(self.contract)

    @property
    def enabled(self) -> bool:
        return self.contract is not None

    @property
    def contract_hash(self) -> str | None:
        return str(self.contract.get("contract_hash")) if self.contract else None

    @property
    def program(self) -> dict[str, Any]:
        return self.contract["program"] if self.contract else {}

    @property
    def assessment(self) -> dict[str, Any]:
        return self.contract["assessment"] if self.contract else {}

    def start_patch(self, state: dict[str, Any], party_id: str) -> StatePatch | None:
        if not self.enabled:
            return None
        first = self.turn_definition(1)
        if not first:
            return None
        progression = self.program["progression"]
        operations = [
            self.resource_value_operation(
                state,
                progression["current_window_resource"],
                first["window"],
                "Opens the first WorldPack-authored training turn.",
                1,
            ),
            self.resource_value_operation(
                state,
                progression["turns_remaining_resource"],
                int(progression["total_turns"]),
                "Tracks authored training turns remaining at party start.",
                1,
            ),
            PatchOperation(
                op="add",
                path="/timeline/-",
                value={
                    "turn": 1,
                    "event": f"Training turn 1 opened from contract {self.contract_hash}.",
                    "confirmed": True,
                    "participants": ["player"],
                },
                reason="Records the first generic training turn opened by party start.",
                turn=1,
            ),
        ]
        return StatePatch(
            turn=1,
            check_id=f"party_start_state:{party_id}",
            source="training-runtime",
            patch=operations,
        )

    def resolution_operations(
        self,
        state: dict[str, Any],
        player_text: str,
        next_turn: int,
        interaction_evidence: list[InteractionEvidence],
    ) -> list[PatchOperation]:
        if not self.enabled:
            return []
        answered_turn = int(state.get("meta", {}).get("turn", 0) or 0)
        detectors = self.evaluate_detectors(state, player_text, interaction_evidence)
        resources = state.get("player", {}).get("resources", {})
        if not isinstance(resources, dict):
            resources = {}
        values = copy.deepcopy(resources)
        reasons: dict[str, str] = {}

        for rule in self.assessment.get("rules", []):
            if not self.rule_applies_to_turn(rule, answered_turn):
                continue
            if not self.evaluate_expression(rule.get("when", True), detectors):
                continue
            rule_id = str(rule["id"])
            for effect in rule.get("effects", []):
                if "increment" in effect:
                    spec = effect["increment"]
                    resource = str(spec["resource"])
                    values[resource] = int(values.get(resource, 0) or 0) + int(spec["value"])
                    reasons[resource] = f"Training assessment rule {rule_id}."
                elif "set" in effect:
                    spec = effect["set"]
                    resource = str(spec["resource"])
                    values[resource] = spec.get("value")
                    reasons[resource] = f"Training assessment rule {rule_id}."
                elif "append_evidence" in effect:
                    spec = effect["append_evidence"]
                    resource = str(spec["resource"])
                    labels = [
                        str(label)
                        for detector_id, label in spec.get("labels", {}).items()
                        if detectors.get(str(detector_id), False)
                    ]
                    fallback_label = spec.get("fallback")
                    if not labels and fallback_label:
                        labels = [str(fallback_label)]
                    if labels:
                        existing = str(values.get(resource) or "").strip()
                        entry = f"ход {answered_turn}: {', '.join(dict.fromkeys(labels))}"
                        values[resource] = (f"{existing}; {entry}" if existing else entry)[-4000:]
                        reasons[resource] = f"Training evidence from assessment rule {rule_id}."

        for resource, aggregate in self.assessment.get("aggregates", {}).items():
            total = sum(int(values.get(item, 0) or 0) for item in aggregate.get("bounded_sum", []))
            minimum = int(aggregate.get("min", 0) or 0)
            maximum = int(aggregate.get("max", total) or total)
            values[str(resource)] = max(minimum, min(maximum, total))
            reasons[str(resource)] = "Recomputes a bounded WorldPack-authored training aggregate."

        operations: list[PatchOperation] = []
        for resource, value in values.items():
            if resources.get(resource) == value:
                continue
            operations.append(
                self.resource_value_operation(
                    state,
                    resource,
                    value,
                    reasons.get(resource, "Applies a WorldPack-authored training rule."),
                    next_turn,
                )
            )
        operations.extend(self.progression_operations(state, next_turn))
        return operations

    def progression_operations(self, state: dict[str, Any], next_turn: int) -> list[PatchOperation]:
        progression = self.program["progression"]
        total_turns = int(progression["total_turns"])
        turn = self.turn_definition(next_turn)
        window = turn["window"] if turn else str(progression["debrief_window"])
        remaining = max(total_turns - next_turn + 1, 0)
        operations = [
            self.resource_value_operation(
                state,
                progression["current_window_resource"],
                window,
                "Advances to the next WorldPack-authored training window.",
                next_turn,
            ),
            self.resource_value_operation(
                state,
                progression["turns_remaining_resource"],
                remaining,
                "Tracks remaining WorldPack-authored training turns.",
                next_turn,
            ),
        ]
        if next_turn > total_turns:
            operations.append(
                self.resource_value_operation(
                    state,
                    progression["completion_status_resource"],
                    progression.get("complete_value", "complete"),
                    "Marks the WorldPack-authored training program complete.",
                    next_turn,
                )
            )
        return operations

    def prompt_contract(
        self,
        state: dict[str, Any],
        interaction_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        turn = int(state.get("meta", {}).get("turn", 0) or 0)
        player = state.get("player", {})
        if not isinstance(player, dict):
            player = {}
        active = self.turn_definition(turn)
        if active:
            visible_state = self.visible_state(state, active.get("visible_state_paths", []))
            surface = copy.deepcopy(active["surface"])
            surface.pop("fallback", None)
            site = interaction_contract.get("site") if interaction_contract else None
            if surface.get("links") == "artifact":
                surface["effective_links"] = (
                    {"enabled": True, "display_url": site["display_url"]}
                    if site
                    else {"enabled": False, "required_value": "нет"}
                )
            return {
                "schema_version": "rp-gateway.training-turn-contract.v1",
                "contract_hash": self.contract_hash,
                "kind": "turn",
                "turn": turn,
                "window": active["window"],
                "instruction": active["instruction"],
                "surface": surface,
                "player": {
                    "name": str(player.get("name") or "Коллега"),
                    "description": str(player.get("description") or ""),
                },
                "visible_state": visible_state,
            }
        if turn > int(self.program["progression"]["total_turns"]):
            debrief = self.program["debrief"]
            return {
                "schema_version": "rp-gateway.training-turn-contract.v1",
                "contract_hash": self.contract_hash,
                "kind": "debrief",
                "turn": turn,
                "instruction": debrief["instruction"],
                "scores": {
                    item["resource"]: {
                        "value": int(self.resource_value(state, item["resource"]) or 0),
                        "max": int(item["max"]),
                        "label": item["label"],
                    }
                    for item in debrief.get("scores", [])
                },
                "evidence": {
                    resource: self.resource_value(state, resource)
                    for resource in debrief.get("evidence_resources", [])
                },
            }
        return None

    def validate_narrative(
        self,
        text: str,
        state: dict[str, Any],
        interaction_contract: dict[str, Any] | None = None,
    ) -> list[str]:
        contract = self.prompt_contract(state, interaction_contract)
        if not contract:
            return ["Training runtime has no active turn contract."]
        violations: list[str] = []
        if contract["kind"] == "debrief":
            if not text.lstrip().startswith(str(self.program["debrief"].get("header", "Итоговый разбор."))):
                violations.append("Training debrief must start with its authored header.")
            for score in self.program["debrief"].get("scores", []):
                expected = int(self.resource_value(state, score["resource"]) or 0)
                maximum = int(score["max"])
                found = {
                    int(value)
                    for value in re.findall(rf"\b(\d{{1,4}})\s*(?:из|/)\s*{maximum}\b", text, re.IGNORECASE)
                }
                if found != {expected}:
                    violations.append(
                        f"Training debrief must report canonical {score['resource']}={expected}/{maximum}."
                    )
            return violations

        turn = self.turn_definition(int(contract["turn"]))
        surface = turn["surface"]
        for pattern in self.program.get("global_validation", {}).get("forbidden_patterns", []):
            if re.search(str(pattern), text, re.IGNORECASE | re.DOTALL):
                violations.append(f"Training narrative contains globally forbidden content: {pattern}")
        if not text.lstrip().startswith(str(turn["header"])):
            violations.append(f"Training narrative must start with the authored header: {turn['header']}")
        marker = "ПИСЬМО" if surface["type"] == "email" else "СООБЩЕНИЕ"
        other_marker = "СООБЩЕНИЕ" if marker == "ПИСЬМО" else "ПИСЬМО"
        blocks = self.structured_blocks(text, marker)
        if len(blocks) != int(surface.get("count", 1)) or self.structured_blocks(text, other_marker):
            violations.append(f"Training turn must contain exactly {surface.get('count', 1)} {marker} block(s).")
            block = text.casefold()
        else:
            block = "\n".join(blocks).casefold()
        for field in surface.get("required_fields", []):
            if str(field).casefold() not in block:
                violations.append(f"Training surface is missing required field: {field}")
        for pattern in surface.get("required_patterns", []):
            if not re.search(str(pattern), block, re.IGNORECASE | re.DOTALL):
                violations.append(f"Training surface is missing authored fact: {pattern}")
        for pattern in surface.get("forbidden_patterns", []):
            if re.search(str(pattern), text, re.IGNORECASE | re.DOTALL):
                violations.append(f"Training surface contains forbidden fact: {pattern}")
        site = interaction_contract.get("site") if interaction_contract else None
        links_policy = str(surface.get("links", "none"))
        urls = re.findall(r"(?:https?://|www\.)[^\s<>]+", text, re.IGNORECASE)
        if links_policy == "none" and urls:
            violations.append("Training turn must not contain a URL.")
        if links_policy == "artifact":
            if site and str(site["display_url"]) not in text:
                violations.append("Training turn must contain the active artifact URL.")
            if site and any(str(site["display_url"]).casefold() not in url.casefold() for url in urls):
                violations.append("Training turn must not contain a URL outside the active artifact contract.")
            if not site and urls:
                violations.append("Training turn with disabled links must not contain a URL.")
            if not site and not re.search(r"(?mi)^Ссылки:\s*нет\s*$", text):
                violations.append("Training turn with disabled links must state 'Ссылки: нет'.")
        if surface.get("profile_adaptation"):
            markers = self.profile_markers(str(contract["player"].get("description") or ""))
            if markers and not any(marker in text.casefold() for marker in markers):
                violations.append("Training surface must use the stored player profession or responsibilities.")
        if surface.get("require_question") and "?" not in text[-300:]:
            violations.append("Training turn must end with a neutral player question.")
        return violations

    def fallback_text(
        self,
        state: dict[str, Any],
        interaction_contract: dict[str, Any] | None = None,
    ) -> str:
        if not self.enabled:
            return "Сценарий готов к следующему учебному действию."
        turn_number = int(state.get("meta", {}).get("turn", 0) or 0)
        turn = self.turn_definition(turn_number)
        if not turn:
            return self.render_template(str(self.program["debrief"]["fallback"]), state, interaction_contract)
        surface = turn["surface"]
        rendered = self.render_template(str(surface["fallback"]), state, interaction_contract)
        return f"{turn['header']}\n\n{rendered}\n\n{turn.get('question', 'Что ты делаешь и как отвечаешь?')}"

    def evaluate_detectors(
        self,
        state: dict[str, Any],
        text: str,
        evidence: list[InteractionEvidence],
    ) -> dict[str, bool]:
        definitions = self.assessment.get("detectors", {})
        results: dict[str, bool] = {}
        resolving: set[str] = set()

        def evaluate(detector_id: str) -> bool:
            if detector_id in results:
                return results[detector_id]
            if detector_id in resolving:
                raise ValueError(f"cyclic training detector: {detector_id}")
            resolving.add(detector_id)
            spec = definitions[detector_id]
            kind = spec["type"]
            if kind == "text_regex":
                matches = [
                    match
                    for pattern in spec.get("patterns", [])
                    for match in re.finditer(str(pattern), text, re.IGNORECASE | re.DOTALL)
                ]
                excluded = any(
                    re.search(str(pattern), text, re.IGNORECASE | re.DOTALL)
                    for pattern in spec.get("exclude_patterns", [])
                )
                value = bool(matches) and not excluded
            elif kind == "text_regex_count":
                count = sum(
                    len(re.findall(str(pattern), text, re.IGNORECASE | re.DOTALL))
                    for pattern in spec.get("patterns", [])
                )
                excluded = any(
                    re.search(str(pattern), text, re.IGNORECASE | re.DOTALL)
                    for pattern in spec.get("exclude_patterns", [])
                )
                value = count >= int(spec.get("min", 1)) and not excluded
            elif kind == "interaction_event":
                event_types = {str(item) for item in spec.get("event_types", [])}
                decision = spec.get("decision_result")
                value = any(
                    item.score_eligible
                    and item.event_type in event_types
                    and (decision is None or item.decision_result == decision)
                    for item in evidence
                )
            elif kind == "profile_overlap":
                description = str(state.get("player", {}).get("description") or "")
                value = bool(self.profile_terms(text) & self.profile_terms(description))
            elif kind == "expression":
                value = self.evaluate_expression(spec.get("value", False), results, evaluate)
            else:
                raise ValueError(f"unsupported training detector type: {kind}")
            resolving.remove(detector_id)
            results[detector_id] = value
            return value

        for detector_id in definitions:
            evaluate(str(detector_id))
        return results

    def evaluate_expression(
        self,
        expression: Any,
        detectors: dict[str, bool],
        resolver: Any | None = None,
    ) -> bool:
        if isinstance(expression, bool):
            return expression
        if isinstance(expression, str):
            return bool(resolver(expression) if resolver else detectors.get(expression, False))
        if not isinstance(expression, dict):
            return False
        if "all" in expression:
            return all(self.evaluate_expression(item, detectors, resolver) for item in expression["all"])
        if "any" in expression:
            return any(self.evaluate_expression(item, detectors, resolver) for item in expression["any"])
        if "not" in expression:
            return not self.evaluate_expression(expression["not"], detectors, resolver)
        return False

    def rule_applies_to_turn(self, rule: dict[str, Any], turn: int) -> bool:
        turns = rule.get("turns", "*")
        return turns == "*" or turn in {int(item) for item in turns}

    def turn_definition(self, turn: int) -> dict[str, Any] | None:
        for item in self.program.get("turns", []):
            if int(item["turn"]) == turn:
                return item
        return None

    def render_template(
        self,
        template: str,
        state: dict[str, Any],
        interaction_contract: dict[str, Any] | None,
    ) -> str:
        player = state.get("player", {}) if isinstance(state.get("player"), dict) else {}
        role_task = self.role_task(str(player.get("description") or ""))
        site = interaction_contract.get("site") if interaction_contract else None

        def replace(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            if key == "player.name":
                return str(player.get("name") or "Коллега")
            if key == "player.description":
                return str(player.get("description") or "специалист с ограниченными полномочиями")
            if key == "role.task":
                return role_task
            if key == "artifact.url":
                return str(site.get("display_url") if site else "нет")
            if key.startswith("resource."):
                return str(self.resource_value(state, key.removeprefix("resource.")) or 0)
            raise ValueError(f"unsupported training fallback placeholder: {key}")

        return PLACEHOLDER_RE.sub(replace, template)

    def role_task(self, description: str) -> str:
        lowered = description.casefold()
        for adapter in self.program.get("role_adapters", []):
            if any(re.search(str(pattern), lowered, re.IGNORECASE) for pattern in adapter.get("patterns", [])):
                return str(adapter["task"])
        return str(self.program.get("default_role_task") or "выполнить назначенный рабочий блок")

    def visible_state(self, state: dict[str, Any], paths: list[str]) -> dict[str, Any]:
        return {path: self.state_value(state, path) for path in paths}

    def resource_value(self, state: dict[str, Any], resource: str) -> Any:
        resources = state.get("player", {}).get("resources", {})
        return resources.get(resource) if isinstance(resources, dict) else None

    def state_value(self, state: dict[str, Any], path: str) -> Any:
        value: Any = state
        for part in path.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def resource_value_operation(
        self,
        state: dict[str, Any],
        resource: str,
        value: Any,
        reason: str,
        turn: int,
    ) -> PatchOperation:
        resources = state.get("player", {}).get("resources", {})
        op = "replace" if isinstance(resources, dict) and resource in resources else "add"
        escaped = resource.replace("~", "~0").replace("/", "~1")
        return PatchOperation(
            op=op,
            path=f"/player/resources/{escaped}",
            value=value,
            reason=reason,
            turn=turn,
        )

    @staticmethod
    def structured_blocks(text: str, marker: str) -> list[str]:
        matches = list(re.finditer(rf"(?m)^{re.escape(marker)}\s*$", text))
        return [
            text[match.start() : matches[index + 1].start() if index + 1 < len(matches) else len(text)]
            for index, match in enumerate(matches)
        ]

    @staticmethod
    def profile_terms(value: str) -> set[str]:
        return {
            token[:6]
            for token in TOKEN_RE.findall(value.casefold())
            if len(token) >= 6 and token[:6] not in ROLE_STOP_WORDS
        }

    @classmethod
    def profile_markers(cls, value: str) -> tuple[str, ...]:
        return tuple(sorted(cls.profile_terms(value)))

    @classmethod
    def _load_contract(cls, worldpack: WorldPackSummary) -> dict[str, Any] | None:
        declaration = worldpack.manifest.get("training_runtime")
        if not isinstance(declaration, dict):
            return None
        if declaration.get("schema_version") != RUNTIME_SCHEMA:
            raise ValueError("unsupported training_runtime schema_version")
        root = Path(worldpack.manifest_path).resolve().parent

        def load_json(key: str, required: bool = True) -> dict[str, Any]:
            relative = declaration.get(key)
            if not relative and not required:
                return {}
            if not isinstance(relative, str) or not relative.strip():
                raise ValueError(f"training_runtime.{key} must be a relative JSON path")
            target = (root / relative).resolve()
            if root not in target.parents:
                raise ValueError(f"training_runtime.{key} escapes the WorldPack root")
            try:
                value = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"cannot load training_runtime.{key}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"training_runtime.{key} must contain a JSON object")
            return value

        payload = {
            "schema_version": RUNTIME_SCHEMA,
            "worldpack_id": worldpack.id,
            "program": load_json("program"),
            "assessment": load_json("assessment"),
            "fallbacks": load_json("fallbacks", required=False),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["contract_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        cls._validate_contract(payload)
        return payload

    @classmethod
    def _validate_contract(cls, contract: dict[str, Any]) -> None:
        if contract.get("schema_version") != RUNTIME_SCHEMA:
            raise ValueError("invalid training runtime snapshot schema")
        contract_hash = str(contract.get("contract_hash") or "")
        unsigned = {key: value for key, value in contract.items() if key != "contract_hash"}
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if not contract_hash or hashlib.sha256(canonical.encode("utf-8")).hexdigest() != contract_hash:
            raise ValueError("training runtime contract hash mismatch")
        program = contract.get("program")
        assessment = contract.get("assessment")
        if not isinstance(program, dict) or program.get("schema_version") != PROGRAM_SCHEMA:
            raise ValueError("invalid training program schema")
        if not isinstance(assessment, dict) or assessment.get("schema_version") != ASSESSMENT_SCHEMA:
            raise ValueError("invalid training assessment schema")
        fallbacks = contract.get("fallbacks")
        if fallbacks and (
            not isinstance(fallbacks, dict) or fallbacks.get("schema_version") != FALLBACKS_SCHEMA
        ):
            raise ValueError("invalid training fallbacks schema")
        progression = program.get("progression")
        if not isinstance(progression, dict) or int(progression.get("total_turns", 0)) < 1:
            raise ValueError("training program requires a positive total_turns")
        for key in (
            "current_window_resource",
            "turns_remaining_resource",
            "completion_status_resource",
        ):
            cls._validate_resource_id(progression.get(key), f"training progression.{key}")
        if not isinstance(progression.get("debrief_window"), str):
            raise ValueError("training progression requires debrief_window")
        adapters = program.get("role_adapters", [])
        if not isinstance(adapters, list):
            raise ValueError("training program role_adapters must be a list")
        for adapter in adapters:
            if not isinstance(adapter, dict) or not isinstance(adapter.get("task"), str):
                raise ValueError("training role adapter requires a task")
            patterns = adapter.get("patterns")
            if not isinstance(patterns, list) or not patterns:
                raise ValueError("training role adapter requires patterns")
            for pattern in patterns:
                cls._compile_pattern(pattern, "training role adapter")
        turns = program.get("turns")
        if not isinstance(turns, list) or len(turns) != int(progression["total_turns"]):
            raise ValueError("training program turn count must match progression.total_turns")
        turn_ids = [int(item.get("turn", 0)) for item in turns if isinstance(item, dict)]
        if turn_ids != list(range(1, int(progression["total_turns"]) + 1)):
            raise ValueError("training program turns must be unique, ordered and contiguous from 1")
        for item in turns:
            for key in ("window", "header", "instruction"):
                if not isinstance(item.get(key), str) or not item[key].strip():
                    raise ValueError(f"training turn {item.get('turn')} requires {key}")
            surface = item.get("surface")
            if not isinstance(surface, dict) or surface.get("type") not in {"email", "messenger"}:
                raise ValueError(f"training turn {item.get('turn')} requires an email or messenger surface")
            if surface.get("links", "none") not in {"none", "artifact"}:
                raise ValueError(f"training turn {item.get('turn')} has an unsupported links policy")
            if not isinstance(surface.get("count", 1), int) or int(surface.get("count", 1)) < 1:
                raise ValueError(f"training turn {item.get('turn')} surface count must be positive")
            for pattern in [
                *program.get("global_validation", {}).get("forbidden_patterns", []),
                *surface.get("required_patterns", []),
                *surface.get("forbidden_patterns", []),
            ]:
                cls._compile_pattern(pattern, f"training turn {item.get('turn')}")
            if not isinstance(surface.get("fallback"), str) or not surface["fallback"].strip():
                raise ValueError(f"training turn {item.get('turn')} requires a fallback")
            cls._validate_placeholders(surface["fallback"])
        debrief = program.get("debrief")
        if not isinstance(debrief, dict) or not isinstance(debrief.get("fallback"), str):
            raise ValueError("training program requires a debrief fallback")
        for key in ("header", "instruction"):
            if not isinstance(debrief.get(key), str) or not debrief[key].strip():
                raise ValueError(f"training program debrief requires {key}")
        cls._validate_placeholders(debrief["fallback"])
        for score in debrief.get("scores", []):
            if not isinstance(score, dict):
                raise ValueError("training debrief scores must contain objects")
            cls._validate_resource_id(score.get("resource"), "training debrief score")
            if not isinstance(score.get("max"), int) or int(score["max"]) < 1:
                raise ValueError("training debrief score requires a positive integer max")
            if not isinstance(score.get("label"), str) or not score["label"].strip():
                raise ValueError("training debrief score requires a label")
        for resource in debrief.get("evidence_resources", []):
            cls._validate_resource_id(resource, "training debrief evidence")
        detectors = assessment.get("detectors")
        if not isinstance(detectors, dict):
            raise ValueError("training assessment detectors must be an object")
        detector_ids = set(detectors)
        for detector_id in detector_ids:
            if not SAFE_ID_RE.fullmatch(str(detector_id)):
                raise ValueError(f"invalid training detector id: {detector_id}")
            spec = detectors[detector_id]
            if not isinstance(spec, dict):
                raise ValueError(f"training detector {detector_id} must be an object")
            if spec.get("type") in {"text_regex", "text_regex_count"}:
                patterns = spec.get("patterns")
                if not isinstance(patterns, list) or not patterns:
                    raise ValueError(f"training detector {detector_id} requires patterns")
                for pattern in patterns:
                    cls._compile_pattern(pattern, f"training detector {detector_id}")
                exclusions = spec.get("exclude_patterns", [])
                if not isinstance(exclusions, list):
                    raise ValueError(
                        f"training detector {detector_id} exclude_patterns must be a list"
                    )
                for pattern in exclusions:
                    cls._compile_pattern(pattern, f"training detector {detector_id} exclusion")
            elif spec.get("type") not in {"interaction_event", "profile_overlap", "expression"}:
                raise ValueError(f"unsupported training detector type: {spec.get('type')}")
            if spec.get("type") == "expression":
                cls._validate_expression_refs(spec.get("value"), detector_ids, detector_id)
        rules = assessment.get("rules")
        if not isinstance(rules, list):
            raise ValueError("training assessment rules must be a list")
        rule_ids: set[str] = set()
        for rule in rules:
            if not isinstance(rule, dict):
                raise ValueError("training assessment rules must contain objects")
            rule_id = str(rule.get("id") or "")
            if not SAFE_ID_RE.fullmatch(rule_id) or rule_id in rule_ids:
                raise ValueError(f"invalid or duplicate training assessment rule id: {rule_id}")
            rule_ids.add(rule_id)
            cls._validate_expression_refs(rule.get("when", True), detector_ids, rule_id)
            rule_turns = rule.get("turns", "*")
            if rule_turns != "*" and (
                not isinstance(rule_turns, list)
                or any(not isinstance(turn, int) or turn < 1 for turn in rule_turns)
            ):
                raise ValueError(f"training assessment rule {rule_id} has invalid turns")
            effects = rule.get("effects")
            if not isinstance(effects, list) or not effects:
                raise ValueError(f"training assessment rule {rule_id} requires effects")
            for effect in effects:
                if not isinstance(effect, dict) or len(effect) != 1:
                    raise ValueError(f"training assessment rule {rule_id} has an invalid effect")
                effect_type, spec = next(iter(effect.items()))
                if effect_type not in EFFECT_TYPES or not isinstance(spec, dict):
                    raise ValueError(
                        f"training assessment rule {rule_id} has unsupported effect {effect_type}"
                    )
                cls._validate_resource_id(
                    spec.get("resource"),
                    f"training assessment rule {rule_id} effect",
                )
                if effect_type == "increment":
                    try:
                        int(spec["value"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(
                            f"training assessment rule {rule_id} increment requires an integer value"
                        ) from exc
                if effect_type == "append_evidence":
                    labels = spec.get("labels", {})
                    if not isinstance(labels, dict) or any(
                        detector_id not in detector_ids or not isinstance(label, str)
                        for detector_id, label in labels.items()
                    ):
                        raise ValueError(
                            f"training assessment rule {rule_id} has invalid evidence labels"
                        )
                    if "fallback" in spec and not isinstance(spec["fallback"], str):
                        raise ValueError(
                            f"training assessment rule {rule_id} has an invalid evidence fallback"
                        )

        aggregates = assessment.get("aggregates", {})
        if not isinstance(aggregates, dict):
            raise ValueError("training assessment aggregates must be an object")
        for resource, aggregate in aggregates.items():
            cls._validate_resource_id(resource, "training assessment aggregate")
            if not isinstance(aggregate, dict):
                raise ValueError(f"training aggregate {resource} must be an object")
            inputs = aggregate.get("bounded_sum")
            if not isinstance(inputs, list) or not inputs:
                raise ValueError(f"training aggregate {resource} requires bounded_sum inputs")
            for input_resource in inputs:
                cls._validate_resource_id(input_resource, f"training aggregate {resource} input")

    @staticmethod
    def _compile_pattern(pattern: Any, context: str) -> None:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"{context} contains an empty regex")
        try:
            re.compile(pattern, re.IGNORECASE | re.DOTALL)
        except re.error as exc:
            raise ValueError(f"{context} contains an invalid regex: {exc}") from exc

    @staticmethod
    def _validate_resource_id(resource: Any, context: str) -> None:
        if not isinstance(resource, str) or not SAFE_ID_RE.fullmatch(resource):
            raise ValueError(f"{context} contains an invalid resource id: {resource}")

    @classmethod
    def _validate_expression_refs(cls, expression: Any, detector_ids: set[str], context: str) -> None:
        if isinstance(expression, bool):
            return
        if isinstance(expression, str):
            if expression not in detector_ids:
                raise ValueError(f"{context} references unknown detector {expression}")
            return
        if not isinstance(expression, dict) or len(expression) != 1:
            raise ValueError(f"{context} has an invalid detector expression")
        operator, value = next(iter(expression.items()))
        if operator in {"all", "any"} and isinstance(value, list) and value:
            for item in value:
                cls._validate_expression_refs(item, detector_ids, context)
            return
        if operator == "not":
            cls._validate_expression_refs(value, detector_ids, context)
            return
        raise ValueError(f"{context} has an unsupported detector expression")

    @staticmethod
    def _validate_placeholders(template: str) -> None:
        for match in PLACEHOLDER_RE.finditer(template):
            key = match.group(1).strip()
            if key in {"player.name", "player.description", "role.task", "artifact.url"}:
                continue
            if key.startswith("resource.") and SAFE_ID_RE.fullmatch(key.removeprefix("resource.")):
                continue
            raise ValueError(f"unsupported training fallback placeholder: {key}")
