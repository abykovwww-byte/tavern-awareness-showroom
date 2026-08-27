"""Windowed RP narrator supervision with WorldPack-authored enforcement policy."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.services.context_budget import estimate_tokens
from app.services.rp_history import eligible_rp_turns
from app.services.service_model_client import ServiceModelClient, service_prompt_text
from app.services.service_models import service_model_choice, service_model_settings


RP_SUPERVISOR_SCHEMA_VERSION = "rp-gateway.rp-supervisor.v1"
RP_SUPERVISOR_RULE_IDS = (
    "world_resistance",
    "turn_return_variety",
    "consequence_pressure",
    "conflict_continuity",
    "world_agency",
    "scene_mobility",
)
RP_SUPERVISOR_WINDOW_TURNS = 50
RP_SUPERVISOR_CADENCE_TURNS = 8
RP_SUPERVISOR_MAX_ADVISORIES = 2
RP_SUPERVISOR_MAX_CONSECUTIVE = 3
RP_SUPERVISOR_RETENTION_DAYS = 30
RP_SUPERVISOR_OUTPUT_RESERVE_TOKENS = 2_000
RP_SUPERVISOR_ADVISORY_MAX_CHARS = 800
RP_SUPERVISOR_SENTINEL_DISAGREEMENT = 0.35

_CLOSING_PATTERNS = (
    ("looks_at_player", re.compile(r"(?is)(?:смотр\w+|взгляд\w*)[^.!?\n]{0,90}(?:на тебя|на вас)\s*[.!?…]*$")),
    ("waits_for_player", re.compile(r"(?is)(?:жд[её]т|ожидает)[^.!?\n]{0,90}(?:тво\w+|ваш\w+|ответ\w+|решени\w+|действи\w+)\s*[.!?…]*$")),
    ("what_next", re.compile(r"(?is)(?:что|как)\s+(?:ты|вы)\s+(?:дела\w+|ответ\w+|поступ\w+|реш\w+)[^.!?\n]{0,40}[?…]*$")),
    ("choice_prompt", re.compile(r"(?is)(?:выбор|решение|следующий шаг)\s+(?:за тобой|за вами|твой|ваш)[^.!?\n]{0,40}[.!?…]*$")),
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _number(value: Any, *, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{label} must be within 0..1")
    return parsed


def validate_rp_supervisor_contract(payload: Any) -> dict[str, Any]:
    """Validate the closed v1 contract without inventing baseline corridors."""

    if not isinstance(payload, dict):
        raise ValueError("RP supervisor contract must be an object")
    required = {
        "schema_version",
        "mode",
        "window_turns",
        "cadence_turns",
        "max_advisories",
        "max_consecutive",
        "confidence_threshold",
        "retention_days",
        "rules",
    }
    if set(payload) != required:
        raise ValueError("RP supervisor contract has an invalid envelope")
    mode = str(payload.get("mode") or "")
    if mode not in {"observe", "enforce"}:
        raise ValueError("RP supervisor mode must be observe or enforce")
    fixed_values = {
        "window_turns": RP_SUPERVISOR_WINDOW_TURNS,
        "cadence_turns": RP_SUPERVISOR_CADENCE_TURNS,
        "max_advisories": RP_SUPERVISOR_MAX_ADVISORIES,
        "max_consecutive": RP_SUPERVISOR_MAX_CONSECUTIVE,
        "retention_days": RP_SUPERVISOR_RETENTION_DAYS,
    }
    if payload.get("schema_version") != RP_SUPERVISOR_SCHEMA_VERSION:
        raise ValueError("unsupported RP supervisor schema")
    for field, expected in fixed_values.items():
        if payload.get(field) != expected:
            raise ValueError(f"RP supervisor {field} must equal {expected}")
    threshold = _number(payload.get("confidence_threshold"), label="confidence_threshold")
    rules = payload.get("rules")
    if not isinstance(rules, list) or len(rules) != len(RP_SUPERVISOR_RULE_IDS):
        raise ValueError("RP supervisor must declare all six rules")

    normalized_rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_rule in rules:
        if not isinstance(raw_rule, dict):
            raise ValueError("RP supervisor rule must be an object")
        allowed = {"id", "title", "rubric"}
        if mode == "enforce":
            allowed |= {"corridor", "advisory_below", "advisory_above"}
        if set(raw_rule) != allowed:
            raise ValueError("RP supervisor rule has an invalid shape")
        rule_id = str(raw_rule.get("id") or "")
        if rule_id not in RP_SUPERVISOR_RULE_IDS or rule_id in seen:
            raise ValueError(f"invalid or duplicate RP supervisor rule: {rule_id}")
        seen.add(rule_id)
        title = str(raw_rule.get("title") or "").strip()
        rubric = str(raw_rule.get("rubric") or "").strip()
        if not title or len(title) > 120 or not rubric or len(rubric) > 1_200:
            raise ValueError(f"invalid RP supervisor rule text: {rule_id}")
        normalized: dict[str, Any] = {"id": rule_id, "title": title, "rubric": rubric}
        if mode == "enforce":
            corridor = raw_rule.get("corridor")
            if not isinstance(corridor, dict) or set(corridor) != {"min", "max"}:
                raise ValueError(f"invalid RP supervisor corridor: {rule_id}")
            lower = _number(corridor.get("min"), label=f"{rule_id}.corridor.min")
            upper = _number(corridor.get("max"), label=f"{rule_id}.corridor.max")
            if lower >= upper:
                raise ValueError(f"RP supervisor corridor must have min < max: {rule_id}")
            normalized["corridor"] = {"min": lower, "max": upper}
            for field in ("advisory_below", "advisory_above"):
                advisory = str(raw_rule.get(field) or "").strip()
                if not advisory or len(advisory) > 240 or "\n" in advisory or "{" in advisory or "}" in advisory:
                    raise ValueError(f"invalid RP supervisor advisory: {rule_id}.{field}")
                normalized[field] = advisory
        normalized_rules.append(normalized)
    if seen != set(RP_SUPERVISOR_RULE_IDS):
        raise ValueError("RP supervisor rule set is incomplete")
    normalized_rules.sort(key=lambda item: RP_SUPERVISOR_RULE_IDS.index(item["id"]))
    return {
        "schema_version": RP_SUPERVISOR_SCHEMA_VERSION,
        "mode": mode,
        **fixed_values,
        "confidence_threshold": threshold,
        "rules": normalized_rules,
    }


def load_rp_supervisor_contract(
    manifest_path: str | Path,
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    files = manifest.get("files") if isinstance(manifest, dict) else None
    relative_path = files.get("rp_supervisor") if isinstance(files, dict) else None
    if relative_path is None:
        return None
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("WorldPack rp_supervisor path is invalid")
    root = Path(manifest_path).resolve().parent
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("WorldPack rp_supervisor path escapes the pack")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("cannot load WorldPack RP supervisor contract") from exc
    return validate_rp_supervisor_contract(payload)


def rp_supervisor_contract_hash(contract: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(validate_rp_supervisor_contract(contract)).encode("utf-8")).hexdigest()


def _window_payload(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "turn_id": int(turn["id"]),
            "player": str(turn.get("player_message") or ""),
            "narrator": str(turn.get("narrative_response") or ""),
        }
        for turn in turns
    ]


def rp_supervisor_window_hash(turns: list[dict[str, Any]]) -> str:
    return hashlib.sha256(_stable_json(_window_payload(turns)).encode("utf-8")).hexdigest()


def rp_supervisor_service_payload(
    contract: dict[str, Any],
    turns: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    normalized = validate_rp_supervisor_contract(contract)
    rules = [
        {"rule_id": rule["id"], "title": rule["title"], "rubric": rule["rubric"]}
        for rule in normalized["rules"]
    ]
    system = (
        "Ты ретроспективный наблюдатель поведения ведущего RP. Оцени целиком ровно переданное "
        "окно и только шесть заданных осей. Не пересказывай сюжет, не предлагай события, цели, "
        "персонажей или локации. score всегда 0..1 по шкале rubric. status=unknown, если в окне "
        "недостаточно материала. Для status=ok укажи только turn_id из окна, которые прямо "
        "подтверждают оценку. Верни строго JSON по схеме."
    )
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": _stable_json({"rules": rules, "window": _window_payload(turns)}),
            },
        ],
        "temperature": 0,
        "max_tokens": RP_SUPERVISOR_OUTPUT_RESERVE_TOKENS,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "rp_supervisor_evaluation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["rules"],
                    "properties": {
                        "rules": {
                            "type": "array",
                            "minItems": len(RP_SUPERVISOR_RULE_IDS),
                            "maxItems": len(RP_SUPERVISOR_RULE_IDS),
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": [
                                    "rule_id",
                                    "score",
                                    "confidence",
                                    "evidence_turn_ids",
                                    "status",
                                ],
                                "properties": {
                                    "rule_id": {"type": "string", "enum": list(RP_SUPERVISOR_RULE_IDS)},
                                    "score": {"type": "number", "minimum": 0, "maximum": 1},
                                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                                    "evidence_turn_ids": {
                                        "type": "array",
                                        "maxItems": RP_SUPERVISOR_WINDOW_TURNS,
                                        "uniqueItems": True,
                                        "items": {"type": "integer", "minimum": 1},
                                    },
                                    "status": {"type": "string", "enum": ["ok", "unknown"]},
                                },
                            },
                        }
                    },
                },
            },
        },
    }
    return payload, service_prompt_text(payload)


def _completion_content(data: dict[str, Any]) -> tuple[str, str | None, str | None]:
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ValueError("RP supervisor response must contain one choice")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("RP supervisor response content is missing")
    return (
        str(message["content"]),
        str(choices[0].get("finish_reason") or "") or None,
        str(data.get("model") or "") or None,
    )


def parse_rp_supervisor_response(
    data: dict[str, Any],
    *,
    window_turn_ids: set[int],
) -> tuple[list[dict[str, Any]], str | None]:
    content, finish_reason, response_model = _completion_content(data)
    if finish_reason == "length":
        raise ValueError("RP supervisor response was truncated")
    try:
        decoded = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        raise ValueError("RP supervisor response is not strict JSON") from exc
    if not isinstance(decoded, dict) or set(decoded) != {"rules"} or not isinstance(decoded["rules"], list):
        raise ValueError("RP supervisor response has an invalid envelope")
    if len(decoded["rules"]) != len(RP_SUPERVISOR_RULE_IDS):
        raise ValueError("RP supervisor response must contain six rules")
    parsed: dict[str, dict[str, Any]] = {}
    required = {"rule_id", "score", "confidence", "evidence_turn_ids", "status"}
    for raw in decoded["rules"]:
        if not isinstance(raw, dict) or set(raw) != required:
            raise ValueError("RP supervisor rule result has an invalid shape")
        rule_id = str(raw.get("rule_id") or "")
        if rule_id not in RP_SUPERVISOR_RULE_IDS or rule_id in parsed:
            raise ValueError(f"invalid or duplicate RP supervisor result: {rule_id}")
        status = str(raw.get("status") or "")
        if status not in {"ok", "unknown"}:
            raise ValueError(f"invalid RP supervisor status: {rule_id}")
        evidence = raw.get("evidence_turn_ids")
        if (
            not isinstance(evidence, list)
            or any(not isinstance(value, int) or isinstance(value, bool) for value in evidence)
            or len(set(evidence)) != len(evidence)
            or any(int(value) not in window_turn_ids for value in evidence)
        ):
            raise ValueError(f"invalid RP supervisor evidence: {rule_id}")
        parsed[rule_id] = {
            "rule_id": rule_id,
            "score": _number(raw.get("score"), label=f"{rule_id}.score"),
            "confidence": _number(raw.get("confidence"), label=f"{rule_id}.confidence"),
            "evidence_turn_ids": [int(value) for value in evidence],
            "status": status,
        }
    if set(parsed) != set(RP_SUPERVISOR_RULE_IDS):
        raise ValueError("RP supervisor result set is incomplete")
    return [parsed[rule_id] for rule_id in RP_SUPERVISOR_RULE_IDS], response_model


def turn_return_sentinel(turns: list[dict[str, Any]]) -> dict[str, Any]:
    """A narrow closing-form sentinel; it is a disagreement check, not an oracle."""

    signatures: list[str] = []
    for turn in turns[-16:]:
        closing = str(turn.get("narrative_response") or "").strip()[-280:]
        for name, pattern in _CLOSING_PATTERNS:
            if pattern.search(closing):
                signatures.append(name)
                break
    if len(signatures) < 3:
        return {"status": "unknown", "score": None, "sample_size": len(signatures)}
    counts = {name: signatures.count(name) for name in sorted(set(signatures))}
    dominant_name, dominant_count = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0]
    dominant_ratio = dominant_count / len(signatures)
    return {
        "status": "ok",
        "score": round(1.0 - dominant_ratio, 4),
        "sample_size": len(signatures),
        "dominant_pattern": dominant_name,
        "dominant_ratio": round(dominant_ratio, 4),
    }


def _direction(score: float, corridor: dict[str, float]) -> tuple[str, float]:
    lower = float(corridor["min"])
    upper = float(corridor["max"])
    if score < lower:
        return "below", lower - score
    if score > upper:
        return "above", score - upper
    return "inside", 0.0


def apply_rp_supervisor_policy(
    contract: dict[str, Any],
    model_results: list[dict[str, Any]],
    *,
    previous_results: list[dict[str, Any]] | None,
    sentinel: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    normalized = validate_rp_supervisor_contract(contract)
    rules = {rule["id"]: rule for rule in normalized["rules"]}
    previous = {
        str(result.get("rule_id") or ""): result
        for result in previous_results or []
        if isinstance(result, dict)
    }
    results: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    candidates: list[tuple[float, int, dict[str, Any]]] = []

    for index, raw in enumerate(model_results):
        rule_id = str(raw["rule_id"])
        rule = rules[rule_id]
        result = {
            **raw,
            "direction": None,
            "severity": 0.0,
            "advisory_active": False,
            "consecutive_reassertions": 0,
            "reassertion_exhausted": False,
            "suppressed_reason": None,
        }
        if rule_id == "turn_return_variety":
            result["sentinel"] = sentinel
            if (
                raw["status"] == "ok"
                and sentinel.get("status") == "ok"
                and abs(float(raw["score"]) - float(sentinel["score"]))
                > RP_SUPERVISOR_SENTINEL_DISAGREEMENT
            ):
                result["suppressed_reason"] = "sentinel_disagreement"
                flags.append(
                    {
                        "code": "turn_return_sentinel_disagreement",
                        "rule_id": rule_id,
                        "model_score": raw["score"],
                        "sentinel_score": sentinel["score"],
                    }
                )
        if normalized["mode"] == "observe":
            result["suppressed_reason"] = result["suppressed_reason"] or "observe_mode"
            results.append(result)
            continue
        if raw["status"] == "unknown":
            result["suppressed_reason"] = result["suppressed_reason"] or "unknown"
            results.append(result)
            continue
        direction, severity = _direction(float(raw["score"]), rule["corridor"])
        result["direction"] = direction
        result["severity"] = round(severity, 6)
        if direction == "inside":
            results.append(result)
            continue
        if result["suppressed_reason"] is None and float(raw["confidence"]) < normalized["confidence_threshold"]:
            result["suppressed_reason"] = "low_confidence"
        if result["suppressed_reason"] is None and not raw["evidence_turn_ids"]:
            result["suppressed_reason"] = "missing_evidence"
        prior = previous.get(rule_id) or {}
        same_direction = prior.get("direction") == direction
        if (
            result["suppressed_reason"] is None
            and same_direction
            and (
                prior.get("reassertion_exhausted") is True
                or int(prior.get("consecutive_reassertions") or 0) >= normalized["max_consecutive"]
            )
        ):
            result["suppressed_reason"] = "max_consecutive"
            result["reassertion_exhausted"] = True
            flags.append({"code": "max_consecutive_reassertions", "rule_id": rule_id, "direction": direction})
        if result["suppressed_reason"] is None:
            prior_count = (
                int(prior.get("consecutive_reassertions") or 0)
                if same_direction and prior.get("advisory_active") is True
                else 0
            )
            result["_next_reassertion_count"] = prior_count + 1
            candidates.append((severity, index, result))
        results.append(result)

    advisories: list[dict[str, Any]] = []
    selected_ids = {
        result["rule_id"]
        for _severity, _index, result in sorted(candidates, key=lambda item: (-item[0], item[1]))[
            : normalized["max_advisories"]
        ]
    }
    for result in results:
        next_count = int(result.pop("_next_reassertion_count", 0))
        if result["rule_id"] not in selected_ids:
            if next_count:
                result["suppressed_reason"] = "max_advisories"
            continue
        rule = rules[result["rule_id"]]
        direction = str(result["direction"])
        text = str(rule[f"advisory_{direction}"])
        result["advisory_active"] = True
        result["consecutive_reassertions"] = next_count
        advisories.append(
            {
                "rule_id": result["rule_id"],
                "direction": direction,
                "text": text,
                "score": result["score"],
                "confidence": result["confidence"],
            }
        )
    return results, advisories, flags


def rp_supervisor_advisory_block(advisories: list[dict[str, Any]]) -> str | None:
    texts = [str(item.get("text") or "").strip() for item in advisories[:RP_SUPERVISOR_MAX_ADVISORIES]]
    texts = [text for text in texts if text]
    if not texts:
        return None
    block = (
        "RP_SUPERVISOR_ADVISORY\n"
        "Низкоприоритетные наблюдения о манере ведения. Они не являются каноном, не задают "
        "цель, персонажа или место и уступают правилам мира, подтверждённым исправлениям и фактам истории.\n"
        + "\n".join(f"- {text}" for text in texts)
    )
    if len(block) > RP_SUPERVISOR_ADVISORY_MAX_CHARS:
        raise ValueError("RP supervisor advisory block exceeds its character limit")
    return block


class RPSupervisorService:
    def __init__(self, settings: Settings, store: Any, contract: dict[str, Any]):
        self.settings = settings
        self.store = store
        self.contract = validate_rp_supervisor_contract(contract)
        self.contract_hash = rp_supervisor_contract_hash(self.contract)

    def enabled(self) -> bool:
        return self.settings.scenario_type == "rp"

    def _eligible_through_request(self, request_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        turn = self.store.get_turn_by_request_id(str(request_id or ""))
        if turn is None or turn.get("excluded_from_memory"):
            return turn, []
        turns = self.store.turns_for_memory(to_turn_id=int(turn["id"]))
        return turn, eligible_rp_turns(turns)

    def should_enqueue(self, request_id: str) -> bool:
        if not self.enabled():
            return False
        _turn, eligible = self._eligible_through_request(request_id)
        count = len(eligible)
        return count >= self.contract["window_turns"] and count % self.contract["cadence_turns"] == 0

    async def process_turn(self, job: dict[str, Any]) -> dict[str, Any]:
        source_turn, eligible = self._eligible_through_request(str(job.get("request_id") or ""))
        if source_turn is None or not eligible:
            return {"status": "skipped", "reason": "source_turn_not_canonical"}
        story_turn_count = len(eligible)
        if (
            story_turn_count < self.contract["window_turns"]
            or story_turn_count % self.contract["cadence_turns"] != 0
        ):
            return {"status": "skipped", "reason": "retrospective_not_due"}
        window = eligible[-self.contract["window_turns"] :]
        window_hash = rp_supervisor_window_hash(window)
        base_record = {
            "request_id": str(source_turn.get("request_id") or "") or None,
            "source_turn_id": int(source_turn["id"]),
            "source_party_turn": int(source_turn.get("party_turn") or 0),
            "story_turn_count": story_turn_count,
            "window_start_turn_id": int(window[0]["id"]),
            "window_end_turn_id": int(window[-1]["id"]),
            "window_hash": window_hash,
            "contract_hash": self.contract_hash,
            "mode": self.contract["mode"],
            "retention_days": self.contract["retention_days"],
        }
        payload, prompt = rp_supervisor_service_payload(self.contract, window)
        choice = service_model_choice(self.settings)
        estimated_input_tokens = estimate_tokens(_stable_json(payload))
        context_tokens = int(choice.get("context_tokens") or 0)
        route_metadata = {
            "provider": str(choice.get("provider") or "retired"),
            "model": str(choice.get("model") or ""),
            "context_tokens": context_tokens,
            "estimated_input_tokens": estimated_input_tokens,
        }
        if not choice.get("available") or choice.get("provider") not in {"local", "openrouter"}:
            return self.store.save_rp_supervisor_evaluation(
                {
                    **base_record,
                    **route_metadata,
                    "status": "unchecked",
                    "status_reason": "service_model_unavailable",
                    "results": [],
                    "advisories": [],
                    "diagnostic_flags": [],
                    "latency_ms": 0.0,
                }
            )
        if estimated_input_tokens + RP_SUPERVISOR_OUTPUT_RESERVE_TOKENS > context_tokens:
            return self.store.save_rp_supervisor_evaluation(
                {
                    **base_record,
                    **route_metadata,
                    "status": "unchecked",
                    "status_reason": "context_capacity",
                    "results": [],
                    "advisories": [],
                    "diagnostic_flags": [],
                    "latency_ms": 0.0,
                }
            )

        started = time.perf_counter()
        try:
            runtime = service_model_settings(self.settings)
            completion = await ServiceModelClient(runtime).complete(
                role="rp_supervisor",
                provider=str(choice["provider"]),
                model=str(choice["model"]),
                party_id=self.store.campaign_id,
                turn_id=int(source_turn["id"]),
                request_id=str(source_turn.get("request_id") or "") or None,
                party_turn=int(source_turn.get("party_turn") or 0),
                attempt=int(job.get("attempts") or 1),
                prompt=prompt,
                payload=payload,
                trace=False,
            )
            model_results, response_model = parse_rp_supervisor_response(
                completion.data,
                window_turn_ids={int(turn["id"]) for turn in window},
            )
            previous = self.store.latest_rp_supervisor_evaluation(
                contract_hash=self.contract_hash,
                before_window_end_turn_id=int(window[-1]["id"]),
            )
            results, advisories, flags = apply_rp_supervisor_policy(
                self.contract,
                model_results,
                previous_results=(previous.get("results") if previous else None),
                sentinel=turn_return_sentinel(window),
            )
            return self.store.save_rp_supervisor_evaluation(
                {
                    **base_record,
                    **route_metadata,
                    "model": response_model or str(choice["model"]),
                    "status": "checked",
                    "status_reason": None,
                    "results": results,
                    "advisories": advisories,
                    "diagnostic_flags": flags,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )
        except Exception as exc:  # noqa: BLE001 - supervision is fail-open for gameplay
            return self.store.save_rp_supervisor_evaluation(
                {
                    **base_record,
                    **route_metadata,
                    "status": "error",
                    "status_reason": type(exc).__name__,
                    "results": [],
                    "advisories": [],
                    "diagnostic_flags": [],
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }
            )

    def prompt_advisory(self) -> str | None:
        if self.contract["mode"] != "enforce":
            return None
        latest = self.store.latest_rp_supervisor_evaluation(contract_hash=self.contract_hash)
        if latest is None or latest.get("status") != "checked":
            return None
        eligible_count = len(eligible_rp_turns(self.store.turns_for_memory()))
        age = eligible_count - int(latest.get("story_turn_count") or 0)
        if age < 0 or age >= self.contract["cadence_turns"]:
            return None
        return rp_supervisor_advisory_block(latest.get("advisories") or [])

    def status_payload(self) -> dict[str, Any]:
        eligible_count = len(eligible_rp_turns(self.store.turns_for_memory()))
        first_due = (
            (self.contract["window_turns"] + self.contract["cadence_turns"] - 1)
            // self.contract["cadence_turns"]
        ) * self.contract["cadence_turns"]
        latest = self.store.latest_rp_supervisor_evaluation(contract_hash=self.contract_hash)
        if eligible_count < first_due:
            next_due = first_due
        elif eligible_count % self.contract["cadence_turns"]:
            next_due = eligible_count + (
                self.contract["cadence_turns"] - eligible_count % self.contract["cadence_turns"]
            )
        elif latest and int(latest.get("story_turn_count") or 0) == eligible_count:
            next_due = eligible_count + self.contract["cadence_turns"]
        else:
            next_due = eligible_count
        choice = service_model_choice(self.settings)
        active_block = self.prompt_advisory()
        return {
            "enabled": True,
            "schema_version": self.contract["schema_version"],
            "mode": self.contract["mode"],
            "story_turn_count": eligible_count,
            "window_turns": self.contract["window_turns"],
            "cadence_turns": self.contract["cadence_turns"],
            "first_retrospective_story_turn": first_due,
            "next_retrospective_story_turn": next_due,
            "active_advisory": active_block is not None,
            "active_advisory_count": len(latest.get("advisories") or []) if active_block and latest else 0,
            "service_model": {
                "choice_id": str(choice.get("id") or ""),
                "title": str(choice.get("title") or ""),
                "provider": str(choice.get("provider") or ""),
                "model": str(choice.get("model") or ""),
                "context_tokens": int(choice.get("context_tokens") or 0),
                "available": bool(choice.get("available")),
            },
            "last_evaluation": latest,
        }
