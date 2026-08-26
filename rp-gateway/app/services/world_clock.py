"""Revision-10 authored world clock and deterministic global events."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.services.service_model_client import ServiceModelClient, service_prompt_text
from app.services.service_models import local_service_model_settings


WORLD_CLOCK_SCHEMA_VERSION = "rp-gateway.world-clock.v1"
WORLD_CLOCK_PROMPT_MAX_CHARS = 800
WORLD_CLOCK_SERVICE_PROMPT_MAX_CHARS = 4_000
WORLD_CLOCK_SERVICE_OUTPUT_MAX_TOKENS = 50
WORLD_CLOCK_MAX_AUTHORED_STEP_SECONDS = 31 * 24 * 60 * 60
WORLD_CLOCK_ID_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
WORLD_CLOCK_DURATION_RE = re.compile(
    r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?"
)
WORLD_CLOCK_NOON_STILL_FUTURE_RE = re.compile(
    r"(?:\bдо\s+полудня\s+(?:ещ[её]\s+)?остал\w*\b|"
    r"\bостал\w*(?:\s+\w+){0,5}\s+до\s+полудня\b|"
    r"\bполдень\s+(?:ещ[её]\s+)?не\s+(?:наступ\w*|приш[её]л)\b|"
    r"\b(?:time\s+(?:is\s+)?left|time\s+remains)\s+until\s+noon\b|"
    r"\bnoon\s+(?:has\s+not|hasn't)\s+(?:come|arrived)\b)",
    re.IGNORECASE,
)
WORLD_CLOCK_DEADLINE_RE = re.compile(r"\b(?:срок\w*|дедлайн\w*|deadline\w*)\b", re.IGNORECASE)
WORLD_CLOCK_CLOSED_DEADLINE_RE = re.compile(
    r"(?:\bист[её]к\w*\b|\bзакрыт\w*\b|\bбольше\s+не\s+(?:счита\w*\s+)?открыт\w*\b|"
    r"\bexpired\b|\bclosed\b|\bno\s+longer\s+open\b)",
    re.IGNORECASE,
)
WORLD_CLOCK_REOPENED_DEADLINE_RE = re.compile(
    r"(?:\b(?:срок\w*|дедлайн\w*)\b(?:\s+\w+){0,7}\s+(?:ещ[её]\s+не\s+ист[её]к\w*|"
    r"вс[её]\s+ещ[её]\s+открыт\w*|оста[её]тся\s+открыт\w*)\b|"
    r"\bостал\w*(?:\s+\w+){0,5}\s+до\s+(?:срока|дедлайна)\b|"
    r"\bdeadline\b(?:\s+\w+){0,7}\s+(?:has\s+not\s+expired|is\s+still\s+open)\b|"
    r"\b(?:time\s+(?:is\s+)?left|time\s+remains)(?:\s+\w+){0,5}\s+"
    r"(?:before|until)\s+the\s+deadline\b)",
    re.IGNORECASE,
)
WORLD_CLOCK_PREDICATE_ROOTS = (
    "/player/resources/",
    "/characters/",
    "/factions/",
    "/resources/",
    "/active_threads/",
    "/completed_threads/",
    "/world_constraints/",
)


class WorldClockBusy(RuntimeError):
    """A main gameplay request owns the state; background time must defer."""


def parse_world_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("world clock date must not be blank")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid world clock date: {text}") from exc
    if parsed.tzinfo is None:
        raise ValueError("world clock date must include a timezone")
    return parsed.astimezone(timezone.utc)


def format_world_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_elapsed_seconds(value: Any) -> int:
    text = str(value or "").strip()
    match = WORLD_CLOCK_DURATION_RE.fullmatch(text)
    if (
        match is None
        or not any(match.groupdict().values())
        or (
            "T" in text
            and not any(match.group(name) for name in ("hours", "minutes", "seconds"))
        )
    ):
        raise ValueError(f"invalid world clock elapsed duration: {text or '<blank>'}")
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    if hours >= 24 or minutes >= 60 or seconds >= 60:
        raise ValueError(f"non-canonical world clock elapsed duration: {text}")
    return (((days * 24) + hours) * 60 + minutes) * 60 + seconds


def format_elapsed(seconds: int) -> str:
    remaining = max(int(seconds), 0)
    days, remaining = divmod(remaining, 24 * 60 * 60)
    hours, remaining = divmod(remaining, 60 * 60)
    minutes, seconds = divmod(remaining, 60)
    date = f"P{days}D" if days else "P"
    time_bits = "".join(
        part
        for part in (
            f"{hours}H" if hours else "",
            f"{minutes}M" if minutes else "",
            f"{seconds}S" if seconds or (not days and not hours and not minutes) else "",
        )
    )
    return f"{date}T{time_bits}" if time_bits else date


def _require_keys(value: dict[str, Any], required: set[str], optional: set[str], label: str) -> None:
    actual = set(value)
    if not required.issubset(actual) or actual - required - optional:
        raise ValueError(f"invalid {label} keys")


def validate_world_clock_contract(
    payload: Any,
    *,
    lore_card_keys: set[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("world-clock.json must contain one object")
    _require_keys(
        payload,
        {"schema_version", "initial_date", "step_unit", "max_step", "markers", "events"},
        set(),
        "world clock contract",
    )
    if payload.get("schema_version") != WORLD_CLOCK_SCHEMA_VERSION:
        raise ValueError("unsupported world clock schema")
    if payload.get("step_unit") != "iso8601_duration":
        raise ValueError("world clock step_unit must be iso8601_duration")
    parse_world_datetime(payload.get("initial_date"))
    max_step_seconds = parse_elapsed_seconds(payload.get("max_step"))
    if not 0 < max_step_seconds <= WORLD_CLOCK_MAX_AUTHORED_STEP_SECONDS:
        raise ValueError("world clock max_step must be between PT1S and P31D")

    markers = payload.get("markers")
    events = payload.get("events")
    if not isinstance(markers, list) or len(markers) > 64:
        raise ValueError("world clock markers must be a list with at most 64 items")
    if not isinstance(events, list) or not events or len(events) > 128:
        raise ValueError("world clock events must contain between 1 and 128 items")

    marker_ids: set[str] = set()
    for marker in markers:
        if not isinstance(marker, dict):
            raise ValueError("world clock marker must be an object")
        _require_keys(marker, {"id", "label"}, {"predicate"}, "world clock marker")
        marker_id = str(marker.get("id") or "").strip()
        label = str(marker.get("label") or "").strip()
        if not WORLD_CLOCK_ID_RE.fullmatch(marker_id) or marker_id in marker_ids:
            raise ValueError(f"invalid or duplicate world clock marker id: {marker_id}")
        if not label or len(label) > 160:
            raise ValueError(f"invalid world clock marker label: {marker_id}")
        marker_ids.add(marker_id)
        predicate = marker.get("predicate")
        if predicate is None:
            continue
        if not isinstance(predicate, dict):
            raise ValueError(f"invalid world clock marker predicate: {marker_id}")
        _require_keys(predicate, {"type", "path", "value"}, set(), "world clock marker predicate")
        path = str(predicate.get("path") or "")
        if predicate.get("type") != "state_equals" or not path.startswith(WORLD_CLOCK_PREDICATE_ROOTS):
            raise ValueError(f"unsupported world clock marker predicate: {marker_id}")
        if isinstance(predicate.get("value"), (dict, list)):
            raise ValueError(f"world clock marker predicate value must be scalar: {marker_id}")

    event_ids: set[str] = set()
    world_fact_ids: set[str] = set()
    total_world_fact_chars = 0
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("world clock event must be an object")
        _require_keys(
            event,
            {"id", "condition", "summary", "superseded_by", "consequences"},
            set(),
            "world clock event",
        )
        event_id = str(event.get("id") or "").strip()
        if not WORLD_CLOCK_ID_RE.fullmatch(event_id) or event_id in event_ids:
            raise ValueError(f"invalid or duplicate world clock event id: {event_id}")
        event_ids.add(event_id)
        summary = str(event.get("summary") or "").strip()
        if not summary or len(summary) > 240:
            raise ValueError(f"invalid world clock event summary: {event_id}")
        superseded_by = event.get("superseded_by")
        if (
            not isinstance(superseded_by, list)
            or not superseded_by
            or len(superseded_by) > 8
            or len(set(superseded_by)) != len(superseded_by)
            or any(marker_id not in marker_ids for marker_id in superseded_by)
        ):
            raise ValueError(f"world clock event requires valid superseded_by markers: {event_id}")
        condition = event.get("condition")
        if not isinstance(condition, dict):
            raise ValueError(f"invalid world clock condition: {event_id}")
        condition_type = condition.get("type")
        if condition_type == "date_gte":
            _require_keys(condition, {"type", "date"}, set(), "date_gte condition")
            parse_world_datetime(condition.get("date"))
        elif condition_type == "after_event":
            _require_keys(condition, {"type", "event_id"}, set(), "after_event condition")
        elif condition_type == "after_confirmed":
            _require_keys(condition, {"type", "marker_id"}, set(), "after_confirmed condition")
            if condition.get("marker_id") not in marker_ids:
                raise ValueError(f"unknown world clock marker in condition: {event_id}")
        else:
            raise ValueError(f"unsupported world clock condition: {event_id}")

        consequences = event.get("consequences")
        if not isinstance(consequences, list) or not consequences or len(consequences) > 8:
            raise ValueError(f"world clock event consequences must contain 1..8 items: {event_id}")
        for consequence in consequences:
            if not isinstance(consequence, dict):
                raise ValueError(f"invalid world clock consequence: {event_id}")
            consequence_type = consequence.get("type")
            if consequence_type == "world_fact":
                _require_keys(consequence, {"type", "id", "text"}, set(), "world_fact consequence")
                fact_id = str(consequence.get("id") or "").strip()
                fact_text = str(consequence.get("text") or "").strip()
                if not WORLD_CLOCK_ID_RE.fullmatch(fact_id) or fact_id in world_fact_ids:
                    raise ValueError(f"invalid or duplicate world clock fact id: {fact_id}")
                if not fact_text or len(fact_text) > 180:
                    raise ValueError(f"invalid world clock fact text: {fact_id}")
                world_fact_ids.add(fact_id)
                total_world_fact_chars += len(fact_text)
            elif consequence_type == "lore_card":
                _require_keys(consequence, {"type", "key", "enabled"}, set(), "lore_card consequence")
                card_key = str(consequence.get("key") or "").strip()
                if not WORLD_CLOCK_ID_RE.fullmatch(card_key) or not isinstance(consequence.get("enabled"), bool):
                    raise ValueError(f"invalid world clock lore-card consequence: {event_id}")
                if lore_card_keys is not None and card_key not in lore_card_keys:
                    raise ValueError(f"world clock references unknown authored lore card: {card_key}")
            else:
                raise ValueError(f"unsupported world clock consequence type: {consequence_type}")

    if total_world_fact_chars > 400:
        raise ValueError("world clock durable fact text exceeds the 400-character prompt reserve")
    for event in events:
        condition = event["condition"]
        if condition["type"] == "after_event":
            dependency = str(condition.get("event_id") or "")
            if dependency not in event_ids or dependency == event["id"]:
                raise ValueError(f"invalid world clock after_event reference: {event['id']}")
    _validate_after_event_cycles(events)
    return copy.deepcopy(payload)


def _validate_after_event_cycles(events: list[dict[str, Any]]) -> None:
    dependencies = {
        str(event["id"]): str(event["condition"]["event_id"])
        for event in events
        if event["condition"]["type"] == "after_event"
    }
    for event_id in dependencies:
        seen: set[str] = set()
        current = event_id
        while current in dependencies:
            if current in seen:
                raise ValueError(f"world clock after_event cycle includes: {event_id}")
            seen.add(current)
            current = dependencies[current]


def load_world_clock_contract(
    manifest_path: str | Path,
    manifest: dict[str, Any],
    *,
    lore_card_keys: set[str] | None = None,
) -> dict[str, Any] | None:
    files = manifest.get("files") if isinstance(manifest, dict) else None
    relative_path = files.get("world_clock") if isinstance(files, dict) else None
    if relative_path is None:
        return None
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("WorldPack world_clock path is invalid")
    root = Path(manifest_path).resolve().parent
    target = (root / relative_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("WorldPack world_clock path escapes the pack")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("cannot load WorldPack world clock") from exc
    return validate_world_clock_contract(payload, lore_card_keys=lore_card_keys)


def initial_world_clock_state(contract: dict[str, Any]) -> dict[str, Any]:
    validate_world_clock_contract(contract)
    return {
        "schema_version": WORLD_CLOCK_SCHEMA_VERSION,
        "date": format_world_datetime(parse_world_datetime(contract["initial_date"])),
        "step_unit": "iso8601_duration",
        "max_step": format_elapsed(parse_elapsed_seconds(contract["max_step"])),
        "processed_party_turn": 0,
        "confirmed_marker_ids": [],
        "fired_event_ids": [],
        "event_statuses": {},
        "world_facts": [],
        "pending_announcements": [],
        "last_elapsed": None,
    }


def _json_pointer_value(state: dict[str, Any], path: str) -> Any:
    current: Any = state
    for raw_part in path.lstrip("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return object()
    return current


def _confirmed_markers(state: dict[str, Any], contract: dict[str, Any]) -> set[str]:
    clock = state.get("world_clock") if isinstance(state.get("world_clock"), dict) else {}
    confirmed = {str(value) for value in clock.get("confirmed_marker_ids", [])}
    for marker in contract["markers"]:
        predicate = marker.get("predicate")
        if not isinstance(predicate, dict):
            continue
        actual = _json_pointer_value(state, str(predicate["path"]))
        if actual == predicate.get("value"):
            confirmed.add(str(marker["id"]))
    return confirmed


def _event_condition_met(
    event: dict[str, Any],
    *,
    current_date: datetime,
    fired_event_ids: set[str],
    confirmed_marker_ids: set[str],
) -> bool:
    condition = event["condition"]
    condition_type = condition["type"]
    if condition_type == "date_gte":
        return current_date >= parse_world_datetime(condition["date"])
    if condition_type == "after_event":
        return str(condition["event_id"]) in fired_event_ids
    return str(condition["marker_id"]) in confirmed_marker_ids


def _evaluate_events(
    state: dict[str, Any],
    contract: dict[str, Any],
    *,
    party_turn: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clock = state["world_clock"]
    current_date = parse_world_datetime(clock["date"])
    confirmed = _confirmed_markers(state, contract)
    clock["confirmed_marker_ids"] = sorted(confirmed)
    fired = {str(value) for value in clock.get("fired_event_ids", [])}
    statuses = clock.setdefault("event_statuses", {})
    pending = clock.setdefault("pending_announcements", [])
    facts = clock.setdefault("world_facts", [])
    facts_by_id = {str(item.get("id")): item for item in facts if isinstance(item, dict)}
    lore_updates: dict[str, dict[str, Any]] = {}
    occurred: list[dict[str, Any]] = []

    changed = True
    while changed:
        changed = False
        for event in contract["events"]:
            event_id = str(event["id"])
            if event_id in statuses:
                continue
            if any(str(marker_id) in confirmed for marker_id in event["superseded_by"]):
                statuses[event_id] = {
                    "status": "superseded",
                    "party_turn": int(party_turn),
                    "date": clock["date"],
                }
                changed = True
                continue
            if not _event_condition_met(
                event,
                current_date=current_date,
                fired_event_ids=fired,
                confirmed_marker_ids=confirmed,
            ):
                continue
            statuses[event_id] = {
                "status": "fired",
                "party_turn": int(party_turn),
                "date": clock["date"],
                "announced_party_turn": None,
            }
            fired.add(event_id)
            announcement = {
                "id": event_id,
                "text": str(event["summary"]),
                "date": clock["date"],
                "source_party_turn": int(party_turn),
            }
            pending.append(announcement)
            occurred.append(announcement)
            for consequence in event["consequences"]:
                if consequence["type"] == "world_fact":
                    fact_id = str(consequence["id"])
                    expected = {
                        "id": fact_id,
                        "text": str(consequence["text"]),
                        "source_event_id": event_id,
                        "date": clock["date"],
                        "party_turn": int(party_turn),
                    }
                    existing = facts_by_id.get(fact_id)
                    if existing is not None and existing != expected:
                        raise ValueError(f"world clock fact conflict: {fact_id}")
                    if existing is None:
                        facts.append(expected)
                        facts_by_id[fact_id] = expected
                else:
                    lore_updates[str(consequence["key"])] = {
                        "key": str(consequence["key"]),
                        "enabled": bool(consequence["enabled"]),
                        "source_event_id": event_id,
                    }
            changed = True
    clock["fired_event_ids"] = [
        str(event["id"]) for event in contract["events"] if str(event["id"]) in fired
    ]
    return list(lore_updates.values()), occurred


def advance_world_clock_state(
    state: dict[str, Any],
    contract: dict[str, Any],
    *,
    party_turn: int,
    elapsed: str,
    reason: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], bool]:
    validate_world_clock_contract(contract)
    if not isinstance(state.get("world_clock"), dict):
        raise ValueError("world clock is not enabled in canonical state")
    current_processed = int(state["world_clock"].get("processed_party_turn") or 0)
    if current_processed >= int(party_turn):
        return copy.deepcopy(state), [], [], True
    candidate = copy.deepcopy(state)
    clock = candidate["world_clock"]
    elapsed_seconds = parse_elapsed_seconds(elapsed)
    max_step_seconds = parse_elapsed_seconds(clock.get("max_step") or contract["max_step"])
    applied_seconds = min(elapsed_seconds, max_step_seconds)
    clock["date"] = format_world_datetime(
        parse_world_datetime(clock["date"]) + timedelta(seconds=applied_seconds)
    )
    clock["processed_party_turn"] = int(party_turn)
    clock["last_elapsed"] = {
        "party_turn": int(party_turn),
        "elapsed": format_elapsed(applied_seconds),
        "reason": str(reason),
    }
    lore_updates, occurred = _evaluate_events(candidate, contract, party_turn=int(party_turn))
    return candidate, lore_updates, occurred, False


def confirm_world_clock_marker_state(
    state: dict[str, Any],
    contract: dict[str, Any],
    *,
    marker_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], bool]:
    validate_world_clock_contract(contract)
    declared = {str(marker["id"]) for marker in contract["markers"]}
    if marker_id not in declared:
        raise ValueError(f"unknown world clock marker: {marker_id}")
    if not isinstance(state.get("world_clock"), dict):
        raise ValueError("world clock is not enabled in canonical state")
    current = {str(value) for value in state["world_clock"].get("confirmed_marker_ids", [])}
    if marker_id in current:
        return copy.deepcopy(state), [], [], True
    candidate = copy.deepcopy(state)
    current.add(marker_id)
    candidate["world_clock"]["confirmed_marker_ids"] = sorted(current)
    party_turn = int(candidate.get("meta", {}).get("turn") or 0)
    lore_updates, occurred = _evaluate_events(candidate, contract, party_turn=party_turn)
    return candidate, lore_updates, occurred, False


def mark_world_clock_events_announced(
    state: dict[str, Any],
    event_ids: list[str],
    *,
    party_turn: int,
) -> dict[str, Any]:
    normalized = {str(event_id) for event_id in event_ids if str(event_id)}
    if not normalized or not isinstance(state.get("world_clock"), dict):
        return state
    clock = state["world_clock"]
    pending = clock.get("pending_announcements")
    if not isinstance(pending, list):
        return state
    announced = {
        str(item.get("id"))
        for item in pending
        if isinstance(item, dict) and str(item.get("id")) in normalized
    }
    if not announced:
        return state
    clock["pending_announcements"] = [
        item
        for item in pending
        if not (isinstance(item, dict) and str(item.get("id")) in announced)
    ]
    statuses = clock.get("event_statuses")
    if isinstance(statuses, dict):
        for event_id in announced:
            status = statuses.get(event_id)
            if isinstance(status, dict):
                status["announced_party_turn"] = int(party_turn)
    return state


def world_clock_prompt_projection(
    state: dict[str, Any],
    contract: dict[str, Any],
    *,
    max_chars: int = WORLD_CLOCK_PROMPT_MAX_CHARS,
) -> dict[str, Any] | None:
    if not isinstance(state.get("world_clock"), dict):
        return None
    clock = state["world_clock"]
    pending = [item for item in clock.get("pending_announcements", []) if isinstance(item, dict)]
    statuses = clock.get("event_statuses") if isinstance(clock.get("event_statuses"), dict) else {}
    scheduled = [event for event in contract["events"] if str(event["id"]) not in statuses]
    scheduled.sort(
        key=lambda event: (
            0 if event["condition"]["type"] == "date_gte" else 1,
            str(event["condition"].get("date") or ""),
            next(index for index, candidate in enumerate(contract["events"]) if candidate["id"] == event["id"]),
        )
    )
    horizon = scheduled[:1]
    lines = [
        "СОБЫТИЯ МИРА — текущий канон; не возвращай время назад и не описывай произошедшее как будущее.",
        f"Текущая игровая дата: {clock.get('date', '')}",
    ]
    occurred_projection: list[dict[str, Any]] = []
    if pending:
        lines.append("В мире произошло:")
        horizon_reserve = (
            len("\nБлижайший горизонт:\n- ") + len(str(horizon[0]["summary"]))
            if horizon
            else 0
        )
        for item in pending:
            line = f"- {str(item.get('text') or '').strip()}"
            candidate = "\n".join([*lines, line])
            if len(candidate) + horizon_reserve > max_chars:
                break
            lines.append(line)
            occurred_projection.append(
                {
                    "id": str(item.get("id") or ""),
                    "text": str(item.get("text") or ""),
                    "date": str(item.get("date") or clock.get("date") or ""),
                }
            )
        if not occurred_projection:
            lines.pop()
    facts = [item for item in clock.get("world_facts", []) if isinstance(item, dict)]
    if facts:
        fact_lines = ["Действующие факты мира:", *[f"- {str(item.get('text') or '')}" for item in facts]]
        if len("\n".join([*lines, *fact_lines])) <= max_chars:
            lines.extend(fact_lines)
    horizon_projection: list[dict[str, Any]] = []
    if horizon:
        event = horizon[0]
        condition = event["condition"]
        suffix = f" ({condition['date']})" if condition["type"] == "date_gte" else ""
        horizon_lines = ["Ближайший горизонт:", f"- {event['summary']}{suffix}"]
        if len("\n".join([*lines, *horizon_lines])) <= max_chars:
            lines.extend(horizon_lines)
            horizon_projection.append(
                {
                    "id": str(event["id"]),
                    "text": str(event["summary"]),
                    "date": str(condition.get("date") or "") or None,
                }
            )
    rendered = "\n".join(lines)
    if len(rendered) > max_chars:
        raise ValueError("world clock prompt projection exceeds 800 characters")
    return {
        "block": rendered,
        "event_ids": [item["id"] for item in occurred_projection],
        "metadata": {
            "schema_version": "rp-gateway.world-clock-events.v1",
            "date": str(clock.get("date") or ""),
            "occurred": occurred_projection,
            "horizon": horizon_projection,
        },
    }


def world_clock_narrative_violations(text: str, state: dict[str, Any]) -> list[str]:
    """Reject only explicit reversals of the authoritative clock projection."""
    clock = state.get("world_clock")
    if not isinstance(clock, dict) or not isinstance(text, str) or not text.strip():
        return []
    try:
        current_date = parse_world_datetime(clock.get("date"))
    except ValueError:
        return []

    violations: list[str] = []
    if current_date.hour >= 12 and WORLD_CLOCK_NOON_STILL_FUTURE_RE.search(text):
        violations.append(
            "Narrative contradicts authoritative world clock date "
            f"{format_world_datetime(current_date)}: it treats the current day's noon as future."
        )

    facts = [item for item in clock.get("world_facts", []) if isinstance(item, dict)]
    closed_deadline_is_canonical = any(
        WORLD_CLOCK_DEADLINE_RE.search(str(item.get("text") or ""))
        and WORLD_CLOCK_CLOSED_DEADLINE_RE.search(str(item.get("text") or ""))
        for item in facts
    )
    if closed_deadline_is_canonical and WORLD_CLOCK_REOPENED_DEADLINE_RE.search(text):
        violations.append(
            "Narrative contradicts an authoritative world clock fact: it reopens an expired deadline."
        )
    return violations


def _response_text(data: dict[str, Any]) -> tuple[str, str | None]:
    choices = data.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ValueError("world clock service response must contain one choice")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise ValueError("world clock service response content is missing")
    return str(message["content"]), str(choices[0].get("finish_reason") or "") or None


def world_clock_service_payload(turn_text: str) -> tuple[dict[str, Any], str]:
    system = (
        "Оцени только сколько игрового времени прошло внутри последнего записанного хода. "
        "Не придумывай события мира и не оценивай будущие действия. Верни ровно JSON "
        "{\"elapsed\":\"PT2H\"}. Допустим только неотрицательный ISO-8601 duration "
        "из дней, часов, минут и секунд."
    )
    text = str(turn_text)
    while True:
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "max_tokens": WORLD_CLOCK_SERVICE_OUTPUT_MAX_TOKENS,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "world_clock_elapsed",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["elapsed"],
                        "properties": {"elapsed": {"type": "string", "maxLength": 32}},
                    },
                },
            },
        }
        prompt = service_prompt_text(payload)
        if len(prompt) <= WORLD_CLOCK_SERVICE_PROMPT_MAX_CHARS:
            return payload, prompt
        overflow = len(prompt) - WORLD_CLOCK_SERVICE_PROMPT_MAX_CHARS
        text = text[: max(len(text) - overflow - 8, 0)]


class WorldClockService:
    def __init__(self, settings: Settings, store: Any, contract: dict[str, Any]):
        self.settings = settings
        self.store = store
        self.contract = validate_world_clock_contract(contract)

    def enabled(self, state: dict[str, Any] | None = None) -> bool:
        current = state if state is not None else self.store.get_state()
        return (
            self.settings.scenario_type == "rp"
            and self.settings.rp_contract_revision >= 10
            and isinstance(current.get("world_clock"), dict)
        )

    def prompt_projection(self, state: dict[str, Any]) -> dict[str, Any] | None:
        if not self.enabled(state):
            return None
        return world_clock_prompt_projection(state, self.contract)

    async def process_turn(self, job: dict[str, Any]) -> dict[str, Any]:
        turn = self.store.get_turn_by_request_id(str(job.get("request_id") or ""))
        if turn is None:
            raise ValueError("world clock source turn not found")
        turn_party_turn = int(turn.get("party_turn") or 0)
        job_party_turn = int(job.get("party_turn") or 0)
        if turn_party_turn < 1 or turn_party_turn != job_party_turn:
            raise ValueError("world clock job does not match its source party turn")
        if turn.get("excluded_from_memory"):
            return {
                "applied": False,
                "reason": "noncanonical_turn",
                "party_turn": turn_party_turn,
            }
        if str(turn.get("turn_kind") or "narrative") != "narrative":
            return self.apply_noop(job, reason="non_narrative_turn")
        turn_text = json.dumps(
            {
                "player": str(turn.get("player_message") or ""),
                "narrator": str(turn.get("narrative_response") or ""),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        payload, prompt = world_clock_service_payload(turn_text)
        runtime = local_service_model_settings(self.settings)
        completion = await ServiceModelClient(runtime).complete(
            role="world_clock_elapsed",
            provider="local",
            model=runtime.narrative_model,
            party_id=self.store.campaign_id,
            turn_id=int(turn["id"]),
            request_id=str(turn.get("request_id") or "") or None,
            party_turn=turn_party_turn,
            attempt=int(job.get("attempts") or 1),
            prompt=prompt,
            payload=payload,
        )
        content, finish_reason = _response_text(completion.data)
        if finish_reason == "length":
            raise ValueError("world clock service response was truncated")
        try:
            decoded = json.loads(content.strip())
        except json.JSONDecodeError as exc:
            raise ValueError("world clock service response is not strict JSON") from exc
        if not isinstance(decoded, dict) or set(decoded) != {"elapsed"}:
            raise ValueError("world clock service response must contain only elapsed")
        elapsed = str(decoded.get("elapsed") or "")
        parse_elapsed_seconds(elapsed)
        return self.store.apply_world_clock_tick(
            self.contract,
            party_turn=turn_party_turn,
            elapsed=elapsed,
            reason="service_model",
            request_id=str(turn.get("request_id") or "") or None,
        )

    def apply_noop(self, job: dict[str, Any], *, reason: str = "service_unavailable") -> dict[str, Any]:
        turn = self.store.get_turn_by_request_id(str(job.get("request_id") or ""))
        if turn is None:
            raise ValueError("world clock source turn not found")
        turn_party_turn = int(turn.get("party_turn") or 0)
        if turn_party_turn < 1 or turn_party_turn != int(job.get("party_turn") or 0):
            raise ValueError("world clock job does not match its source party turn")
        return self.store.apply_world_clock_tick(
            self.contract,
            party_turn=turn_party_turn,
            elapsed="PT0S",
            reason=reason,
            request_id=str(turn.get("request_id") or "") or None,
        )
