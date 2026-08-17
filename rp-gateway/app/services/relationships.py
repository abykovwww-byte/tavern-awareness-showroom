"""Deterministic mechanics for the RP relationship pressure layer."""

from __future__ import annotations

from collections import deque
from decimal import Decimal, ROUND_HALF_UP
import hashlib
from typing import Any

from app.services.relationship_store import RelationshipStore
from app.services.state_store import StateStore


_DEFAULT_AXIS = "loyalty"
_DEFAULT_ROLE = "subordinate"
_BOUNDARY_EVENTS = frozenset({"crack", "ultimatum", "plot", "favour", "strike"})
_CASCADE_EVENTS = frozenset({"crack", "ultimatum", "favour", "strike"})
_FAVOURABLE_ULTIMATUM_RESOLUTIONS = frozenset(
    {"paid", "problem_removed", "resolved_favourably", "accepted"}
)


class RelationshipMechanics:
    """Apply WorldPack-owned relationship rules without consulting a model."""

    def __init__(
        self,
        state_store: StateStore,
        model: dict[str, Any],
        rp_contract_revision: int = 0,
    ):
        self.state_store = state_store
        self.model = model
        self.store = RelationshipStore(state_store, model)
        self.rp_contract_revision = rp_contract_revision

    def apply_events(self, *, turn_id: int, party_turn: int, events: list[dict]) -> list[dict]:
        """Persist extracted events and return boundary-event changes they cause."""
        if turn_id < 0:
            raise ValueError("turn_id must be non-negative")
        if party_turn < 0:
            raise ValueError("party_turn must be non-negative")
        if not isinstance(events, list):
            raise TypeError("events must be a list")

        self._ensure_seed_state()
        queued: deque[tuple[dict[str, Any], str]] = deque()
        for event in events:
            if not isinstance(event, dict):
                raise TypeError("each relationship event must be an object")
            queued.append((event, "extraction"))
        return self._apply_queue(turn_id=turn_id, party_turn=party_turn, queued=queued)

    def advance_turn(self, party_turn: int) -> list[dict]:
        """Advance clocks, make deterministic discovery rolls, and apply decay crossings."""
        if party_turn < 0:
            raise ValueError("party_turn must be non-negative")

        self._ensure_seed_state()
        changes: list[dict[str, Any]] = []
        self._apply_resolved_ultimatums(party_turn=party_turn, changes=changes)

        # Work on a snapshot: events opened below start advancing on a later turn.
        for event in self.store.active_events(party_turn):
            event_id = str(event["event_id"])
            due_turn = int(event["due_turn"])
            if not self._event_basis_active(event, party_turn):
                if self.store.resolve_event(
                    int(event["id"]),
                    status="resolved",
                    resolution="basis_gone",
                    resolved_turn=party_turn,
                ):
                    changes.append(self._event_change("resolved", event, resolution="basis_gone"))
                continue

            if event_id == "plot" and int(event["opened_turn"]) < party_turn:
                discovered = self._plot_discovered(event=event, party_turn=party_turn)
                if discovered:
                    resolution = "discovered_late" if due_turn <= party_turn else "discovered_early"
                    if self.store.resolve_event(
                        int(event["id"]),
                        status="resolved",
                        resolution=resolution,
                        resolved_turn=party_turn,
                    ):
                        changes.append(self._event_change("resolved", event, resolution=resolution))
                    continue

                if due_turn > party_turn:
                    continue
                if not self.store.resolve_event(
                    int(event["id"]),
                    status="expired",
                    resolution="not_discovered",
                    resolved_turn=party_turn,
                ):
                    continue
                changes.append(self._event_change("resolved", event, resolution="not_discovered"))
                strike = self._open_strike_from_plot(event=event, party_turn=party_turn)
                if strike is not None:
                    changes.append(strike)
                    changes.extend(self._cascade_from_payload(event=strike, party_turn=party_turn))
                continue

            if due_turn > party_turn:
                continue
            if event_id == "favour" and self.rp_contract_revision >= 4:
                # A due clock creates a narrator obligation; it is not proof that
                # the promised help appeared in the committed scene. The existing
                # evidence-checked relationship extractor resolves it separately.
                continue
            if self.store.resolve_event(
                int(event["id"]),
                status="expired",
                resolution="deadline_missed",
                resolved_turn=party_turn,
            ):
                changes.append(self._event_change("resolved", event, resolution="deadline_missed"))
                if event_id == "ultimatum":
                    self._set_ultimatum_band(event=event, party_turn=party_turn, changes=changes)

        # Expiring causes can move an axis upward even when extraction produced no event.
        seen: set[tuple[str, str]] = set()
        for row in self.store.pressure_rows(party_turn):
            key = (str(row["character_id"]), str(row["axis"]))
            if key in seen:
                continue
            seen.add(key)
            boundary = self._evaluate_axis(
                character_id=key[0],
                axis=key[1],
                party_turn=party_turn,
                trigger=None,
            )
            if boundary is not None:
                changes.append(boundary)

        return changes

    def resolve_delivered_favours(
        self,
        *,
        turn_id: int,
        party_turn: int,
        narrative_response: str,
    ) -> list[dict[str, Any]]:
        """Resolve due favours only after WorldPack-marked evidence in this scene."""
        if turn_id <= 0:
            raise ValueError("turn_id must be positive")
        if party_turn < 0:
            raise ValueError("party_turn must be non-negative")
        if self.rp_contract_revision < 4:
            return []

        self._ensure_seed_state()
        scene_text = " ".join(str(narrative_response or "").casefold().split())
        changes: list[dict[str, Any]] = []
        for event in self.store.active_events(party_turn):
            if (
                str(event["event_id"]) != "favour"
                or int(event["due_turn"]) > party_turn
                or not self._event_basis_active(event, party_turn)
            ):
                continue
            evidence_rows = self.store.cause_rows(
                str(event["character_id"]),
                str(event["axis"]),
                party_turn,
            )
            delivered = False
            for row in evidence_rows:
                event_rule = self.model.get("events", {}).get(str(row["event_id"]))
                resolves = event_rule.get("resolves") if isinstance(event_rule, dict) else None
                if (
                    int(row["turn_id"]) == turn_id
                    and int(row["weight"]) > 0
                    and str(row["source"]) == "extraction"
                    and isinstance(resolves, list)
                    and "favour" in resolves
                    and " ".join(str(row["evidence"] or "").casefold().split()) in scene_text
                ):
                    delivered = True
                    break
            if not delivered:
                continue
            if self.store.resolve_event(
                int(event["id"]),
                status="resolved",
                resolution="delivered",
                resolved_turn=party_turn,
                resolved_turn_id=turn_id,
            ):
                changes.append(self._event_change("resolved", event, resolution="delivered"))
        return changes

    def pressure_block(
        self,
        party_turn: int,
        character_names: dict[str, str],
        *,
        persist_seed_state: bool = True,
    ) -> str | None:
        """Return the deliberately non-numeric narrator pressure block."""
        if persist_seed_state:
            self._ensure_seed_state()
        rows = self.store.pressure_rows(party_turn)
        rendered: list[str] = []
        for row in rows:
            character_id = str(row["character_id"])
            name = character_names.get(character_id)
            if not name:
                # Falling back to a storage identifier would violate the prompt boundary.
                continue
            band = self._band(str(row["axis"]), str(row["band"]))
            if band is None:
                continue
            causes = [cause for cause in row.get("causes", []) if int(cause.get("weight", 0)) != 0]
            if not row.get("events") and not causes:
                continue
            pressures = [
                self._qualitative_pressure(
                    str(event["event_id"]),
                    party_turn=party_turn,
                    event=event,
                )
                for event in row.get("events", [])
            ]
            pressures = [pressure for pressure in pressures if pressure]
            cause_pressure = self._qualitative_cause_pressure(causes)
            if cause_pressure:
                pressures.insert(0, cause_pressure)
            suffix = f" {' '.join(pressures)}" if pressures else ""
            rendered.append(f"- {name} — {band['label']}.{suffix}")

        if not rendered:
            return None
        return "RELATIONSHIP_PRESSURE\n" + "\n".join(rendered)

    @staticmethod
    def resolved_event_block(changes: list[dict[str, Any]], character_names: dict[str, str]) -> str | None:
        rendered: list[str] = []
        for change in changes:
            if change.get("action") != "resolved":
                continue
            name = character_names.get(str(change.get("character_id") or ""))
            if not name:
                continue
            event_id = str(change.get("event_id") or "")
            resolution = str(change.get("resolution") or "")
            instruction = {
                ("favour", "delivered"): "покажи в текущей сцене конкретную добровольную услугу или поддержку этого персонажа",
                ("crack", "deadline_missed"): "покажи заметное охлаждение и закрытие прежнего окна для разговора",
                ("ultimatum", "deadline_missed"): "покажи исполнение объявленного разрыва или отказа",
                ("strike", "deadline_missed"): "покажи открытое последствие накопленного конфликта",
                ("plot", "not_discovered"): "покажи последствие нераскрытой скрытой линии, не раскрывая служебные детали",
                ("plot", "discovered_early"): "покажи обнаружение скрытой линии через наблюдаемую сцену",
                ("plot", "discovered_late"): "покажи запоздалое обнаружение и уже возникшую цену",
            }.get((event_id, resolution))
            if instruction:
                rendered.append(f"- {name}: {instruction}.")
        if not rendered:
            return None
        return (
            "RELATIONSHIP_EVENT_RESOLUTION\n"
            "Эти последствия уже определены Gateway. Реализуй их как наблюдаемое поведение в этой сцене; "
            "не показывай внутренние IDs, часы или числовые значения.\n"
            + "\n".join(rendered)
        )

    def due_event_block(self, party_turn: int, character_names: dict[str, str]) -> str | None:
        """Describe due revision-4 consequences without committing their resolution."""
        due_changes = [
            self._event_change("resolved", event, resolution="delivered")
            for event in self.store.active_events(party_turn)
            if str(event["event_id"]) == "favour"
            and int(event["due_turn"]) <= party_turn
            and self._event_basis_active(event, party_turn)
        ]
        return self.resolved_event_block(due_changes, character_names)

    def _ensure_seed_state(self) -> None:
        """Materialize the WorldPack trust seed once in the derived relationship tables."""
        self.store.backfill_active_event_deadlines(self.model.get("clocks", {}))
        state = self.state_store.get_state()
        characters = state.get("characters")
        if not isinstance(characters, dict):
            return
        axis = str(self.model.get("trust_mapping", {}).get("axis", _DEFAULT_AXIS))
        if axis not in self.model.get("axes", {}):
            axis = _DEFAULT_AXIS
        for character_id, character in characters.items():
            if not isinstance(character_id, str) or not isinstance(character, dict):
                continue
            mapped_trust = self._map_trust(self._seed_trust(state, character_id, character))
            added = self.store.add_cause(
                character_id=character_id,
                axis=axis,
                event_id="seed_trust",
                weight=mapped_trust,
                turn_id=0,
                party_turn=0,
                expires_turn=None,
                evidence="WorldPack trust seed",
                source="seed",
            )
            if self.store.axis_state(character_id, axis) is None:
                neutral = (
                    self._band(axis, "neutral")
                    if self.rp_contract_revision >= 4
                    else self._band_for_value(axis, mapped_trust)
                ) or self._band_for_value(axis, 0)
                self.store.set_axis_state(
                    character_id=character_id,
                    axis=axis,
                    band=str(neutral["id"]),
                    band_since_turn=-1 if self.rp_contract_revision >= 4 else 0,
                )
            if added and self.rp_contract_revision >= 4:
                boundary = self._evaluate_axis(
                    character_id=character_id,
                    axis=axis,
                    party_turn=0,
                    trigger={
                        "character_id": character_id,
                        "event_id": "seed_trust",
                        "evidence": "WorldPack trust seed",
                        "source": "seed",
                        "turn_id": 0,
                    },
                )
                if boundary is None:
                    current = self.store.axis_state(character_id, axis)
                    if current is not None and int(current["band_since_turn"]) < 0:
                        self.store.set_axis_state(
                            character_id=character_id,
                            axis=axis,
                            band=str(current["band"]),
                            band_since_turn=0,
                        )

    def _seed_trust(self, state: dict[str, Any], character_id: str, character: dict[str, Any]) -> int:
        value = character.get("trust")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        relationships = state.get("relationships")
        if isinstance(relationships, dict):
            for relationship in relationships.values():
                if not isinstance(relationship, dict):
                    continue
                endpoints = {relationship.get("from"), relationship.get("to")}
                if "player" in endpoints and character_id in endpoints:
                    value = relationship.get("trust")
                    if isinstance(value, int) and not isinstance(value, bool):
                        return value
        return 0

    def _map_trust(self, trust: int) -> int:
        mapping = self.model.get("trust_mapping")
        if not isinstance(mapping, dict) or mapping.get("kind") != "linear":
            raise ValueError("relationship trust_mapping must be linear")
        input_range = mapping.get("in")
        output_range = mapping.get("out")
        if (
            not isinstance(input_range, list)
            or len(input_range) != 2
            or not isinstance(output_range, list)
            or len(output_range) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in input_range + output_range)
            or input_range[0] >= input_range[1]
        ):
            raise ValueError("relationship trust_mapping ranges are invalid")
        bounded = max(input_range[0], min(input_range[1], int(trust)))
        ratio = Decimal(bounded - input_range[0]) / Decimal(input_range[1] - input_range[0])
        mapped = Decimal(output_range[0]) + ratio * Decimal(output_range[1] - output_range[0])
        return int(mapped.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def _event_basis_active(self, event: dict[str, Any], party_turn: int) -> bool:
        event_id = str(event["event_id"])
        if event_id not in _BOUNDARY_EVENTS:
            return True
        character_id = str(event["character_id"])
        axis = str(event["axis"])
        value = self.store.value(character_id, axis, party_turn)
        if event_id == "ultimatum":
            rupture = self._band(axis, "rupture")
            if rupture is None:
                return False
            threshold = int(rupture["max"]) - int(self.model["axes"][axis].get("band_deadband", 0))
            return value <= threshold
        axis_rule = self.model["axes"][axis]
        deadband = int(axis_rule.get("band_deadband", 0))
        if event_id == "crack":
            band = self._band(axis, "estranged")
            return band is not None and value <= int(band["max"]) - deadband
        if event_id == "favour":
            band = self._band(axis, "favourable")
            return band is not None and value >= int(band["min"]) + deadband
        band = self._band(axis, "enmity")
        return band is not None and value <= int(band["max"]) - deadband

    def _apply_queue(
        self,
        *,
        turn_id: int,
        party_turn: int,
        queued: deque[tuple[dict[str, Any], str]],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        scheduled = {
            (str(event.get("character_id", "")), str(event.get("event_id", "")), source)
            for event, source in queued
        }

        while queued:
            event, source = queued.popleft()
            character_id = self._required_text(event, "character_id")
            event_id = self._required_text(event, "event_id")
            evidence = self._required_text(event, "evidence")
            event_rule = self.model.get("events", {}).get(event_id)
            if not isinstance(event_rule, dict):
                raise ValueError(f"unknown relationship event: {event_id}")
            axis = str(event_rule.get("axis", _DEFAULT_AXIS))
            if axis not in self.model.get("axes", {}):
                raise ValueError(f"unknown relationship axis: {axis}")

            weight = self._event_weight(
                character_id=character_id,
                event_id=event_id,
                event_rule=event_rule,
                source=source,
                party_turn=party_turn,
            )
            if weight == 0:
                continue
            decay_turns = event_rule.get("decay_turns")
            expires_turn = party_turn + int(decay_turns) if decay_turns is not None else None
            added = self.store.add_cause(
                character_id=character_id,
                axis=axis,
                event_id=event_id,
                weight=weight,
                turn_id=turn_id,
                party_turn=party_turn,
                expires_turn=expires_turn,
                evidence=evidence,
                source=source,
            )
            if not added:
                continue

            self._ensure_role_badge(character_id=character_id, party_turn=party_turn)
            wound = event_rule.get("wound")
            if isinstance(wound, str) and wound:
                self.store.set_badge(
                    character_id=character_id,
                    badge_kind="wound",
                    badge_id=wound,
                    party_turn=party_turn,
                )

            trigger = {
                "character_id": character_id,
                "event_id": event_id,
                "evidence": evidence,
                "source": source,
                "turn_id": turn_id,
            }
            boundary = self._evaluate_axis(
                character_id=character_id,
                axis=axis,
                party_turn=party_turn,
                trigger=trigger,
            )
            if boundary is None:
                continue
            changes.append(boundary)
            if str(boundary.get("event_id")) not in _CASCADE_EVENTS:
                continue
            for witness_id in self._witnesses(character_id):
                key = (witness_id, event_id, "cascade")
                if key in scheduled:
                    continue
                scheduled.add(key)
                queued.append(
                    (
                        {
                            "character_id": witness_id,
                            "event_id": event_id,
                            "evidence": evidence,
                        },
                        "cascade",
                    )
                )

        return changes

    def _event_weight(
        self,
        *,
        character_id: str,
        event_id: str,
        event_rule: dict[str, Any],
        source: str,
        party_turn: int,
    ) -> int:
        base_weight = int(event_rule["weight"])
        active_wounds = self.store.active_badges(character_id, "wound")
        if base_weight > 0 and source in {"extraction", "cascade"}:
            if any(
                bool(self.model.get("wounds", {}).get(str(row["badge_id"]), {}).get("blocks_natural_growth"))
                for row in active_wounds
            ):
                return 0

        multiplier = Decimal("1")
        character_rule = self.model.get("character_weights", {}).get(character_id, {})
        multiplier *= Decimal(str(character_rule.get("multipliers", {}).get(event_id, 1)))
        if base_weight < 0:
            for row in active_wounds:
                wound_rule = self.model.get("wounds", {}).get(str(row["badge_id"]), {})
                multiplier *= Decimal(str(wound_rule.get("negative_multiplier", 1)))

        scaled = int((Decimal(base_weight) * multiplier).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        axis = str(event_rule.get("axis", _DEFAULT_AXIS))
        cap = int(self.model["axes"][axis]["per_turn_cap"])
        already_applied = sum(
            int(row["weight"])
            for row in self.store.cause_rows(character_id, axis, party_turn)
            if int(row["party_turn"]) == party_turn
        )
        capped_total = max(-cap, min(cap, already_applied + scaled))
        return capped_total - already_applied

    def _evaluate_axis(
        self,
        *,
        character_id: str,
        axis: str,
        party_turn: int,
        trigger: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        axis_rule = self.model["axes"][axis]
        bands = list(axis_rule["bands"])
        current_state = self.store.axis_state(character_id, axis)
        if current_state is None:
            current_id = self._band_for_value(axis, 0)["id"]
            self.store.set_axis_state(
                character_id=character_id,
                axis=axis,
                band=current_id,
                band_since_turn=party_turn - 1,
            )
        else:
            if int(current_state["band_since_turn"]) == party_turn:
                return None
            current_id = str(current_state["band"])

        current_index = self._band_index(bands, current_id)
        value = self.store.value(character_id, axis, party_turn)
        natural_index = self._band_index(bands, str(self._band_for_value(axis, value)["id"]))
        if natural_index == current_index:
            return None

        direction = 1 if natural_index > current_index else -1
        next_index = current_index + direction
        next_band = bands[next_index]
        next_minimum, next_maximum = self._band_bounds(axis, next_index)
        deadband = int(axis_rule.get("band_deadband", 0))
        if direction < 0:
            threshold = next_maximum - deadband
            crossed = value <= threshold
        else:
            threshold = next_minimum + deadband
            crossed = value >= threshold
        if not crossed:
            return None

        boundary_event = next_band.get("opens")
        band_on = str(next_band.get("band_on", "cross"))
        if not boundary_event:
            self.store.set_axis_state(
                character_id=character_id,
                axis=axis,
                band=str(next_band["id"]),
                band_since_turn=party_turn,
            )
            return None

        payload = self._boundary_payload(
            character_id=character_id,
            boundary_event=str(boundary_event),
            trigger=trigger,
        )
        event_to_open = str(boundary_event)
        if event_to_open == "plot" and not payload.get("accomplice_id"):
            # A character with no listener cannot conspire; the specified outcome is departure.
            event_to_open = "strike"
            payload.update({"strike_form": "flight", "reason": "no_accomplice"})
        event_row_id = self.store.open_event(
            character_id=character_id,
            axis=axis,
            event_id=event_to_open,
            opened_turn=party_turn,
            due_turn=self._due_turn(event_to_open, party_turn),
            payload=payload,
        )
        if band_on == "cross":
            self.store.set_axis_state(
                character_id=character_id,
                axis=axis,
                band=str(next_band["id"]),
                band_since_turn=party_turn,
            )
        if event_row_id is None:
            return None
        return {
            "action": "opened",
            "id": event_row_id,
            "character_id": character_id,
            "axis": axis,
            "event_id": event_to_open,
            "opened_turn": party_turn,
            "due_turn": self._due_turn(event_to_open, party_turn),
            "payload": payload,
        }

    def _boundary_payload(
        self,
        *,
        character_id: str,
        boundary_event: str,
        trigger: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        witnesses = self._witnesses(character_id)
        if boundary_event == "crack":
            payload["accomplice_id"] = witnesses[0] if witnesses else None
        elif boundary_event == "ultimatum":
            payload["demand"] = self._ultimatum_demand(character_id)
        elif boundary_event == "plot":
            payload.update(self._plot_payload(character_id))
        elif boundary_event == "strike":
            payload.update(self._strike_payload(character_id))
        if trigger is not None:
            payload["trigger_event_id"] = trigger["event_id"]
            payload["trigger_evidence"] = trigger["evidence"]
            payload["trigger_turn_id"] = trigger["turn_id"]
        return payload

    def _plot_payload(self, character_id: str) -> dict[str, Any]:
        crack_rows = self.store.event_rows(character_id, "crack")
        accomplice_id = None
        if crack_rows:
            accomplice_id = crack_rows[-1].get("payload", {}).get("accomplice_id")
        payload = self._strike_payload(character_id)
        payload.update({"accomplice_id": accomplice_id, "target_id": "player"})
        return payload

    def _strike_payload(self, character_id: str) -> dict[str, Any]:
        role_id = self._role_id(character_id)
        role_rule = self.model.get("roles", {}).get(role_id, {})
        payload: dict[str, Any] = {"strike_form": role_rule.get("strike_form", "sabotage")}
        for badge in self.store.active_badges(character_id, "wound"):
            wound_rule = self.model.get("wounds", {}).get(str(badge["badge_id"]), {})
            if "strike_visibility" in wound_rule:
                payload["visibility"] = wound_rule["strike_visibility"]
        return payload

    def _ultimatum_demand(self, character_id: str) -> str:
        for badge in reversed(self.store.active_badges(character_id, "wound")):
            wound_rule = self.model.get("wounds", {}).get(str(badge["badge_id"]), {})
            demand = wound_rule.get("ultimatum_demand")
            if isinstance(demand, str) and demand:
                return demand
        return "restitution"

    def _ensure_role_badge(self, *, character_id: str, party_turn: int) -> None:
        role_id = str(self.model.get("character_weights", {}).get(character_id, {}).get("role", _DEFAULT_ROLE))
        self.store.set_badge(
            character_id=character_id,
            badge_kind="role",
            badge_id=role_id,
            party_turn=party_turn,
        )

    def _role_id(self, character_id: str) -> str:
        roles = self.store.active_badges(character_id, "role")
        if roles:
            return str(roles[-1]["badge_id"])
        return str(self.model.get("character_weights", {}).get(character_id, {}).get("role", _DEFAULT_ROLE))

    def _witnesses(self, character_id: str) -> list[str]:
        state = self.state_store.get_state()
        known_characters = set(state.get("characters", {}))
        witnesses: set[str] = set()
        for relationship in state.get("relationships", {}).values():
            if not isinstance(relationship, dict):
                continue
            source = relationship.get("from")
            target = relationship.get("to")
            if source == character_id and target in known_characters:
                witnesses.add(str(target))
            elif target == character_id and source in known_characters:
                witnesses.add(str(source))
        witnesses.discard(character_id)
        return sorted(witnesses)

    def _cascade_from_payload(self, *, event: dict[str, Any], party_turn: int) -> list[dict[str, Any]]:
        payload = event.get("payload") or {}
        trigger_event_id = payload.get("trigger_event_id")
        trigger_evidence = payload.get("trigger_evidence")
        trigger_turn_id = payload.get("trigger_turn_id")
        if (
            not isinstance(trigger_event_id, str)
            or not isinstance(trigger_evidence, str)
            or isinstance(trigger_turn_id, bool)
            or not isinstance(trigger_turn_id, int)
        ):
            return []
        queued: deque[tuple[dict[str, Any], str]] = deque()
        for witness_id in self._witnesses(str(event["character_id"])):
            queued.append(
                (
                    {
                        "character_id": witness_id,
                        "event_id": trigger_event_id,
                        "evidence": trigger_evidence,
                    },
                    "cascade",
                )
            )
        return self._apply_queue(turn_id=trigger_turn_id, party_turn=party_turn, queued=queued)

    def _open_strike_from_plot(self, *, event: dict[str, Any], party_turn: int) -> dict[str, Any] | None:
        payload = dict(event.get("payload") or {})
        payload.update(self._strike_payload(str(event["character_id"])))
        event_row_id = self.store.open_event(
            character_id=str(event["character_id"]),
            axis=str(event["axis"]),
            event_id="strike",
            opened_turn=party_turn,
            due_turn=self._due_turn("strike", party_turn),
            payload=payload,
        )
        if event_row_id is None:
            return None
        return {
            "action": "opened",
            "id": event_row_id,
            "character_id": str(event["character_id"]),
            "axis": str(event["axis"]),
            "event_id": "strike",
            "opened_turn": party_turn,
            "due_turn": self._due_turn("strike", party_turn),
            "payload": payload,
        }

    def _apply_resolved_ultimatums(self, *, party_turn: int, changes: list[dict[str, Any]]) -> None:
        for event in self.store.event_rows(event_id="ultimatum"):
            resolved_turn = event.get("resolved_turn")
            resolution = event.get("resolution")
            if resolved_turn is None or int(resolved_turn) > party_turn:
                continue
            if resolution in _FAVOURABLE_ULTIMATUM_RESOLUTIONS:
                continue
            self._set_ultimatum_band(event=event, party_turn=int(resolved_turn), changes=changes)

    def _set_ultimatum_band(
        self,
        *,
        event: dict[str, Any],
        party_turn: int,
        changes: list[dict[str, Any]],
    ) -> None:
        character_id = str(event["character_id"])
        axis = str(event["axis"])
        state = self.store.axis_state(character_id, axis)
        if state is None or str(state["band"]) != "estranged":
            return
        rupture = self._band(axis, "rupture")
        if rupture is None:
            return
        threshold = int(rupture["max"]) - int(self.model["axes"][axis].get("band_deadband", 0))
        if self.store.value(character_id, axis, party_turn) > threshold:
            return
        if self.store.set_axis_state(
            character_id=character_id,
            axis=axis,
            band="rupture",
            band_since_turn=party_turn,
        ):
            changes.append(
                {
                    "action": "band_changed",
                    "character_id": character_id,
                    "axis": axis,
                    "band": "rupture",
                    "party_turn": party_turn,
                }
            )

    def _plot_discovered(self, *, event: dict[str, Any], party_turn: int) -> bool:
        chance = Decimal(str(self.model.get("plot", {}).get("discovery_chance_per_turn", 0)))
        if chance <= 0:
            return False
        if chance >= 1:
            return True
        identity = f"{self.state_store.campaign_id}:{event['id']}:{event['opened_turn']}:{party_turn}:plot"
        digest = hashlib.sha256(identity.encode("utf-8")).digest()
        roll = Decimal(int.from_bytes(digest[:8], "big")) / Decimal(2**64)
        return roll < chance

    def _due_turn(self, event_id: str, opened_turn: int) -> int:
        clock = self.model.get("clocks", {}).get(event_id)
        if isinstance(clock, bool) or not isinstance(clock, int) or clock <= 0:
            raise ValueError(f"relationship clock is missing or invalid: {event_id}")
        return opened_turn + clock

    def _band_for_value(self, axis: str, value: int) -> dict[str, Any]:
        bands = list(self.model["axes"][axis]["bands"])
        for index, band in enumerate(bands):
            minimum, maximum = self._band_bounds(axis, index)
            if minimum <= value <= maximum:
                return band
        raise ValueError(f"relationship value {value} is outside axis {axis}")

    def _band_bounds(self, axis: str, index: int) -> tuple[int, int]:
        axis_rule = self.model["axes"][axis]
        bands = list(axis_rule["bands"])
        band = bands[index]
        if "min" in band:
            minimum = int(band["min"])
        elif index == 0:
            minimum = int(axis_rule["min"])
        else:
            previous = bands[index - 1]
            minimum = int(previous["max"]) + 1

        if "max" in band:
            maximum = int(band["max"])
        elif index == len(bands) - 1:
            maximum = int(axis_rule["max"])
        else:
            following = bands[index + 1]
            maximum = int(following["min"]) - 1
        return minimum, maximum

    def _band(self, axis: str, band_id: str) -> dict[str, Any] | None:
        return next(
            (band for band in self.model.get("axes", {}).get(axis, {}).get("bands", []) if band.get("id") == band_id),
            None,
        )

    @staticmethod
    def _band_index(bands: list[dict[str, Any]], band_id: str) -> int:
        for index, band in enumerate(bands):
            if band.get("id") == band_id:
                return index
        raise ValueError(f"unknown relationship band: {band_id}")

    @staticmethod
    def _required_text(event: dict[str, Any], field: str) -> str:
        value = event.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"relationship event requires non-empty {field}")
        return value.strip()

    @staticmethod
    def _event_change(action: str, event: dict[str, Any], **extra: Any) -> dict[str, Any]:
        result = {
            "action": action,
            "id": int(event["id"]),
            "character_id": str(event["character_id"]),
            "axis": str(event["axis"]),
            "event_id": str(event["event_id"]),
        }
        result.update(extra)
        return result

    @staticmethod
    def _qualitative_cause_pressure(causes: list[dict[str, Any]]) -> str:
        seed_weight = sum(int(cause.get("weight", 0)) for cause in causes if cause.get("source") == "seed")
        recent_weight = sum(int(cause.get("weight", 0)) for cause in causes if cause.get("source") != "seed")
        parts: list[str] = []
        if seed_weight > 0:
            parts.append("Исходное доверие уже влияет на выбор и реакцию персонажа.")
        elif seed_weight < 0:
            parts.append("Исходное недоверие уже влияет на выбор и реакцию персонажа.")
        if recent_weight > 0:
            parts.append("Недавние поступки укрепили доверие; прояви это в текущей сцене.")
        elif recent_weight < 0:
            parts.append("Недавние поступки ослабили доверие; прояви это в текущей сцене.")
        return " ".join(parts)

    @staticmethod
    def _qualitative_pressure(event_id: str, *, party_turn: int, event: dict[str, Any]) -> str:
        opened_turn = int(event.get("opened_turn", party_turn))
        phase = max(0, party_turn - opened_turn) % 3
        if event.get("due_turn") is not None and int(event["due_turn"]) - party_turn <= 1:
            timing = "Срок близок, и напряжение требует ответа в текущей сцене."
        else:
            timing = (
                "Линия только обозначилась и ищет первый жест."
                if phase == 0
                else "Напряжение удерживается, но уже меняет поведение участников."
                if phase == 1
                else "Невысказанное давление возвращается в сцену через детали и паузы."
            )
        return {
            "crack": f"Появился скрытый канал для разговора. {timing}",
            "ultimatum": f"Требование ждёт ответа и может привести к разрыву. {timing}",
            "plot": f"Скрытая линия развивается; оставь в сцене один ненавязчивый след. {timing}",
            "strike": f"Последствие готовится проявиться открыто. {timing}",
            "favour": f"Персонаж сам создаёт возможность для сближения. {timing}",
        }.get(event_id, timing)
