#!/usr/bin/env python3
"""Deterministic semantic acceptance evaluator for saved service responses."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


METRIC_NAMES = (
    "event_precision",
    "event_recall",
    "character_id_accuracy",
    "empty_scene_false_positive_rate",
    "positive_trust_recall",
    "correction_retention",
)
LOWER_IS_BETTER = {"empty_scene_false_positive_rate"}
SUPPORTED_MANIFEST_VERSION = 1
SUPPORTED_CORPUS_VERSION = 2


class AcceptanceError(ValueError):
    """Raised when the frozen oracle or saved responses violate their contract."""


def _strip_comment(value: str) -> str:
    quoted = False
    escaped = False
    for index, char in enumerate(value):
        if char == '"' and not escaped:
            quoted = not quoted
        if char == "#" and not quoted:
            return value[:index].rstrip()
        escaped = char == "\\" and not escaped
        if char != "\\":
            escaped = False
    return value.strip()


def _scalar(value: str) -> Any:
    value = _strip_comment(value).strip()
    if not value:
        return None
    if value.startswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise AcceptanceError(f"invalid quoted scalar: {value}") from exc
    if value in {"true", "false"}:
        return value == "true"
    if value in {"null", "~"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?(?:\d+\.\d*|\d*\.\d+)", value):
        return float(value)
    return value


class _InlineParser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0

    def parse(self) -> Any:
        value = self._value()
        self._space()
        if self.index != len(self.text):
            raise AcceptanceError(f"unexpected inline YAML at column {self.index + 1}")
        return value

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def _value(self) -> Any:
        self._space()
        if self.index >= len(self.text):
            raise AcceptanceError("missing inline YAML value")
        char = self.text[self.index]
        if char == "{":
            return self._mapping()
        if char == "[":
            return self._sequence()
        if char == '"':
            try:
                value, consumed = json.JSONDecoder().raw_decode(self.text[self.index :])
            except json.JSONDecodeError as exc:
                raise AcceptanceError("invalid quoted inline YAML value") from exc
            self.index += consumed
            return value
        start = self.index
        while self.index < len(self.text) and self.text[self.index] not in ",]}":
            self.index += 1
        return _scalar(self.text[start : self.index])

    def _mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        self.index += 1
        while True:
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "}":
                self.index += 1
                return result
            start = self.index
            while self.index < len(self.text) and self.text[self.index] != ":":
                self.index += 1
            if self.index >= len(self.text):
                raise AcceptanceError("inline mapping key has no value")
            key = self.text[start : self.index].strip()
            if not key or key in result:
                raise AcceptanceError(f"invalid or duplicate inline mapping key: {key!r}")
            self.index += 1
            result[key] = self._value()
            self._space()
            if self.index < len(self.text) and self.text[self.index] == ",":
                self.index += 1
                continue
            if self.index < len(self.text) and self.text[self.index] == "}":
                self.index += 1
                return result
            raise AcceptanceError("inline mapping is not comma-delimited")

    def _sequence(self) -> list[Any]:
        result: list[Any] = []
        self.index += 1
        while True:
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "]":
                self.index += 1
                return result
            result.append(self._value())
            self._space()
            if self.index < len(self.text) and self.text[self.index] == ",":
                self.index += 1
                continue
            if self.index < len(self.text) and self.text[self.index] == "]":
                self.index += 1
                return result
            raise AcceptanceError("inline sequence is not comma-delimited")


def _inline(value: str) -> Any:
    return _InlineParser(_strip_comment(value)).parse()


def _top_level_scalars(lines: list[str], stop_at: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for line in lines:
        if stop_at and line.startswith(f"{stop_at}:"):
            break
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*):\s*(.*)", line)
        if match and match.group(2).strip():
            result[match.group(1)] = _scalar(match.group(2))
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    manifest = _top_level_scalars(lines)
    thresholds: dict[str, float] = {}
    section: str | None = None
    for line in lines:
        if line and not line[0].isspace() and line.rstrip().endswith(":"):
            section = line.split(":", 1)[0]
            continue
        if section == "thresholds":
            match = re.fullmatch(r"\s{2}([A-Za-z_][A-Za-z0-9_]*):\s*(.*)", line)
            if match:
                value = _scalar(match.group(2))
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise AcceptanceError(f"threshold {match.group(1)} must be numeric")
                thresholds[match.group(1)] = float(value)

    missing = sorted(set(METRIC_NAMES) - thresholds.keys())
    extra = sorted(thresholds.keys() - set(METRIC_NAMES))
    if missing or extra:
        raise AcceptanceError(f"manifest thresholds mismatch: missing={missing}, extra={extra}")
    if manifest.get("version") != SUPPORTED_MANIFEST_VERSION:
        raise AcceptanceError(f"unsupported manifest version: {manifest.get('version')!r}")
    if manifest.get("labeled_by") != "user":
        raise AcceptanceError("manifest labeled_by must be user")
    if manifest.get("per_event_class") is not True:
        raise AcceptanceError("manifest per_event_class must be true")
    if not isinstance(manifest.get("corpus"), str) or not manifest["corpus"]:
        raise AcceptanceError("manifest corpus must be a relative path")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(manifest.get("corpus_hash", ""))):
        raise AcceptanceError("manifest corpus_hash must be sha256:<64 lowercase hex>")
    if any(value < 0 or value > 1 for value in thresholds.values()):
        raise AcceptanceError("manifest thresholds must be between 0 and 1")
    manifest["thresholds"] = thresholds
    return manifest


def _parse_label(block: list[str], example_id: str) -> dict[str, Any]:
    label_index = next((index for index, line in enumerate(block) if line.startswith("    label:")), None)
    if label_index is None:
        raise AcceptanceError(f"{example_id}: missing label")
    suffix = block[label_index].split(":", 1)[1].strip()
    if suffix:
        label = _inline(suffix)
    else:
        label: dict[str, Any] = {}
        index = label_index + 1
        while index < len(block):
            line = block[index]
            if not line.startswith("      ") or line.startswith("        "):
                break
            match = re.fullmatch(r"\s{6}([A-Za-z_][A-Za-z0-9_]*):\s*(.*)", line)
            if not match:
                raise AcceptanceError(f"{example_id}: malformed label line")
            key, value_text = match.groups()
            if value_text.strip():
                label[key] = _inline(value_text) if value_text.lstrip().startswith(("[", "{")) else _scalar(value_text)
                index += 1
                continue
            values: list[Any] = []
            index += 1
            while index < len(block) and block[index].startswith("        - "):
                values.append(_inline(block[index].split("-", 1)[1].strip()))
                index += 1
            label[key] = values
    if not isinstance(label, dict):
        raise AcceptanceError(f"{example_id}: label must be a mapping")
    _validate_label(label, example_id)
    return label


def _validate_event(event: Any, location: str) -> None:
    required = {"character", "event", "sign", "direction"}
    if not isinstance(event, dict) or set(event) != required:
        raise AcceptanceError(f"{location}: event fields must be {sorted(required)}")
    if not all(isinstance(event[key], str) and event[key] for key in required):
        raise AcceptanceError(f"{location}: event fields must be non-empty strings")
    if event["sign"] not in {"+", "-"}:
        raise AcceptanceError(f"{location}: event sign must be + or -")
    if event["direction"] not in {"on_npc", "from_npc"}:
        raise AcceptanceError(f"{location}: unsupported event direction")


def _validate_badge(badge: Any, location: str) -> None:
    required = {"character", "badge"}
    if not isinstance(badge, dict) or set(badge) != required:
        raise AcceptanceError(f"{location}: badge fields must be {sorted(required)}")
    if not all(isinstance(badge[key], str) and badge[key] for key in required):
        raise AcceptanceError(f"{location}: badge fields must be non-empty strings")


def _validate_label(label: dict[str, Any], example_id: str) -> None:
    required = {"scene_type", "expected_events", "expected_badges", "world_rule_ok", "correction"}
    if set(label) != required:
        raise AcceptanceError(f"{example_id}: label fields must be {sorted(required)}")
    if label["scene_type"] not in {"empty", "significant"}:
        raise AcceptanceError(f"{example_id}: invalid scene_type")
    if not isinstance(label["expected_events"], list) or not isinstance(label["expected_badges"], list):
        raise AcceptanceError(f"{example_id}: expected events and badges must be lists")
    for index, event in enumerate(label["expected_events"]):
        _validate_event(event, f"{example_id}.expected_events[{index}]")
    for index, badge in enumerate(label["expected_badges"]):
        _validate_badge(badge, f"{example_id}.expected_badges[{index}]")
    if not isinstance(label["world_rule_ok"], bool):
        raise AcceptanceError(f"{example_id}: world_rule_ok must be boolean")
    if label["correction"] not in {"none", "attempted", "not_retained"}:
        raise AcceptanceError(f"{example_id}: invalid correction label")
    if label["scene_type"] == "empty" and (label["expected_events"] or label["expected_badges"]):
        raise AcceptanceError(f"{example_id}: empty scene cannot contain expected relationship output")


def load_corpus(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header = _top_level_scalars(lines, stop_at="examples")
    if header.get("version") != SUPPORTED_CORPUS_VERSION:
        raise AcceptanceError(f"unsupported corpus version: {header.get('version')!r}")
    if header.get("labeled_by") != "user":
        raise AcceptanceError("corpus labeled_by must be user")

    starts = [index for index, line in enumerate(lines) if line.startswith("  - id:")]
    examples: list[dict[str, Any]] = []
    for position, start in enumerate(starts):
        block = lines[start : starts[position + 1] if position + 1 < len(starts) else len(lines)]
        example_id = str(_scalar(block[0].split(":", 1)[1]))
        values: dict[str, Any] = {"id": example_id}
        for line in block[1:]:
            match = re.fullmatch(r"\s{4}(party_turn|concern):\s*(.*)", line)
            if match:
                values[match.group(1)] = _scalar(match.group(2))
        values["label"] = _parse_label(block, example_id)
        if not isinstance(values.get("party_turn"), int) or values["party_turn"] < 1:
            raise AcceptanceError(f"{example_id}: party_turn must be a positive integer")
        if not isinstance(values.get("concern"), str) or not values["concern"]:
            raise AcceptanceError(f"{example_id}: concern must be a non-empty string")
        examples.append(values)
    ids = [example["id"] for example in examples]
    if not examples or len(ids) != len(set(ids)):
        raise AcceptanceError("corpus examples must be non-empty with unique ids")
    return {**header, "examples": examples, "normalized_sha256": hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()}


def _tautology_findings(saved: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    source = saved.get("source")
    if not isinstance(source, dict) or source.get("kind") != "saved_service_responses":
        findings.append("source.kind must be saved_service_responses")
    if not isinstance(source, dict) or source.get("expectations_generated") is not False:
        findings.append("source.expectations_generated must be explicitly false")
    producer = str(source.get("producer", "") if isinstance(source, dict) else "").lower()
    if any(marker in producer for marker in ("evaluator", "validator", "oracle", "label")):
        findings.append("saved outputs identify an oracle/evaluator as their producer")

    forbidden = {"label", "labels", "expected", "expectation", "expected_events", "expected_badges", "ground_truth", "thresholds"}

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if key.lower() in forbidden:
                    findings.append(f"generated expectation field present: {child_path}")
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(saved.get("responses", []), "responses")
    return findings


def load_saved_responses(path: Path, corpus_ids: set[str]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AcceptanceError(f"saved responses are not valid JSON: {exc}") from exc
    if not isinstance(saved, dict) or saved.get("schema_version") != "rp-stack.semantic-acceptance.saved-responses.v1":
        raise AcceptanceError("unsupported saved response schema_version")
    responses = saved.get("responses")
    if not isinstance(responses, list):
        raise AcceptanceError("saved responses must contain a responses list")
    by_id: dict[str, dict[str, Any]] = {}
    for index, response in enumerate(responses):
        if not isinstance(response, dict) or not isinstance(response.get("id"), str):
            raise AcceptanceError(f"responses[{index}] must have a string id")
        response_id = response["id"]
        if response_id in by_id:
            raise AcceptanceError(f"duplicate saved response id: {response_id}")
        events = response.get("events", [])
        badges = response.get("badges", [])
        if not isinstance(events, list) or not isinstance(badges, list):
            raise AcceptanceError(f"{response_id}: events and badges must be lists")
        for event_index, event in enumerate(events):
            _validate_event(event, f"{response_id}.events[{event_index}]")
        for badge_index, badge in enumerate(badges):
            _validate_badge(badge, f"{response_id}.badges[{badge_index}]")
        if "world_rule_ok" in response and not isinstance(response["world_rule_ok"], bool):
            raise AcceptanceError(f"{response_id}: world_rule_ok must be boolean")
        if "correction_retained" in response and not isinstance(response["correction_retained"], bool):
            raise AcceptanceError(f"{response_id}: correction_retained must be boolean")
        by_id[response_id] = response
    missing = sorted(corpus_ids - by_id.keys())
    extra = sorted(by_id.keys() - corpus_ids)
    if missing or extra:
        raise AcceptanceError(f"saved response ids mismatch: missing={missing}, extra={extra}")
    source_findings = _tautology_findings(saved)
    return by_id, {
        "passed": not source_findings,
        "status": "independent" if not source_findings else "generated-expectations-detected",
        "generated_expectations_detected": bool(source_findings),
        "findings": source_findings,
    }


def _event_key(event: dict[str, str]) -> tuple[str, str, str, str]:
    return event["character"], event["event"], event["sign"], event["direction"]


def _semantic_key(event: dict[str, str]) -> tuple[str, str, str]:
    return event["event"], event["sign"], event["direction"]


def _ratio(numerator: int, denominator: int, *, empty_value: float) -> float:
    return empty_value if denominator == 0 else numerator / denominator


def _event_metrics(expected: list[dict[str, str]], predicted: list[dict[str, str]]) -> dict[str, float]:
    expected_exact = Counter(_event_key(event) for event in expected)
    predicted_exact = Counter(_event_key(event) for event in predicted)
    exact_matches = sum((expected_exact & predicted_exact).values())
    expected_semantic = Counter(_semantic_key(event) for event in expected)
    predicted_semantic = Counter(_semantic_key(event) for event in predicted)
    semantic_matches = sum((expected_semantic & predicted_semantic).values())
    return {
        "event_precision": _ratio(exact_matches, len(predicted), empty_value=1.0 if not expected else 0.0),
        "event_recall": _ratio(exact_matches, len(expected), empty_value=1.0),
        "character_id_accuracy": _ratio(exact_matches, semantic_matches, empty_value=1.0 if not expected else 0.0),
    }


def _threshold_result(name: str, value: float, threshold: float) -> dict[str, Any]:
    passed = value <= threshold if name in LOWER_IS_BETTER else value >= threshold
    return {"value": round(value, 6), "threshold": threshold, "passed": passed}


def evaluate(manifest: dict[str, Any], corpus: dict[str, Any], responses: dict[str, dict[str, Any]], source: dict[str, Any]) -> dict[str, Any]:
    expected_events: list[dict[str, str]] = []
    predicted_events: list[dict[str, str]] = []
    empty_total = 0
    empty_false_positives = 0
    correction_total = 0
    correction_matches = 0
    positive_expected: list[dict[str, str]] = []
    positive_predicted: list[dict[str, str]] = []

    for example in corpus["examples"]:
        label = example["label"]
        response = responses[example["id"]]
        expected_events.extend(label["expected_events"])
        predicted_events.extend(response.get("events", []))
        if label["scene_type"] == "empty":
            empty_total += 1
            if response.get("events") or response.get("badges"):
                empty_false_positives += 1
        if label["correction"] != "none":
            correction_total += 1
            if response.get("correction_retained") is label["world_rule_ok"]:
                correction_matches += 1
        positive_expected.extend(event for event in label["expected_events"] if event["sign"] == "+")
        positive_predicted.extend(event for event in response.get("events", []) if event["sign"] == "+")

    values = _event_metrics(expected_events, predicted_events)
    positive_exact = sum(
        (Counter(_event_key(event) for event in positive_expected) & Counter(_event_key(event) for event in positive_predicted)).values()
    )
    values.update(
        {
            "empty_scene_false_positive_rate": _ratio(empty_false_positives, empty_total, empty_value=0.0),
            "positive_trust_recall": _ratio(positive_exact, len(positive_expected), empty_value=0.0),
            "correction_retention": _ratio(correction_matches, correction_total, empty_value=0.0),
        }
    )
    metrics = {
        name: _threshold_result(name, values[name], manifest["thresholds"][name])
        for name in METRIC_NAMES
    }

    event_classes = sorted({event["event"] for event in expected_events + predicted_events})
    per_event_class: dict[str, Any] = {}
    for event_class in event_classes:
        expected_class = [event for event in expected_events if event["event"] == event_class]
        predicted_class = [event for event in predicted_events if event["event"] == event_class]
        class_values = _event_metrics(expected_class, predicted_class)
        class_metrics = {
            name: _threshold_result(name, value, manifest["thresholds"][name])
            for name, value in class_values.items()
        }
        per_event_class[event_class] = {
            "expected_count": len(expected_class),
            "predicted_count": len(predicted_class),
            "metrics": class_metrics,
            "passed": all(metric["passed"] for metric in class_metrics.values()),
        }

    threshold_failures = [name for name, result in metrics.items() if not result["passed"]]
    class_failures = [name for name, result in per_event_class.items() if not result["passed"]]
    passed = source["passed"] and not threshold_failures and not class_failures
    return {
        "schema_version": "rp-stack.semantic-acceptance-report.v1",
        "mode": "semantic-acceptance-offline",
        "passed": passed,
        "oracle": {
            "manifest_version": manifest["version"],
            "corpus_version": corpus["version"],
            "labeled_by": corpus["labeled_by"],
            "example_count": len(corpus["examples"]),
            "corpus_hash": f"sha256:{corpus['normalized_sha256']}",
        },
        "source_independence": source,
        "metrics": metrics,
        "per_event_class": per_event_class,
        "failures": {
            "thresholds": threshold_failures,
            "event_classes": class_failures,
            "source_independence": source["findings"],
        },
    }


def evaluate_files(manifest_path: Path, saved_responses_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = load_manifest(manifest_path)
    corpus_path = (manifest_path.parent / manifest["corpus"]).resolve()
    try:
        corpus_path.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise AcceptanceError("manifest corpus path escapes the acceptance directory") from exc
    corpus = load_corpus(corpus_path)
    expected_hash = manifest["corpus_hash"]
    actual_hash = f"sha256:{corpus['normalized_sha256']}"
    if actual_hash != expected_hash:
        raise AcceptanceError(f"corpus hash mismatch: expected {expected_hash}, got {actual_hash}")
    if corpus["labeled_by"] != manifest["labeled_by"]:
        raise AcceptanceError("manifest and corpus labeled_by values differ")
    responses, source = load_saved_responses(saved_responses_path, {example["id"] for example in corpus["examples"]})
    return evaluate(manifest, corpus, responses, source)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--saved-responses", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_files(args.manifest, args.saved_responses)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1
    except Exception as exc:  # noqa: BLE001 - CLI reports a bounded contract error
        print(json.dumps({"passed": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
