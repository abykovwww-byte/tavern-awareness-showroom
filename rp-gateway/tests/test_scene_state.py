from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.models.schemas import (
    ChatCompletionRequest,
    ChatMessage,
    PatchOperation,
    StatePatch,
)
from app.services.adjudicator import Adjudicator, SceneContinuityError
from app.services.narrative import response_text
from app.services.scene_state import (
    MAX_SCENE_DELTA_OPERATIONS,
    MAX_SCENE_EVIDENCE_CHARS,
    MAX_SCENE_ENTITY_ID_CHARS,
    MAX_SCENE_PRESENT_CHARACTERS,
    SCENE_BUNDLE_SCHEMA,
    materialize_scene_bundle,
    normalize_anchor_text,
)
from app.services.state_store import StateStore


def scene_state() -> dict[str, Any]:
    return {
        "meta": {
            "campaign_id": "merchant-t15",
            "schema_version": "1.0.0",
            "state_version": 1,
            "turn": 14,
            "last_updated": "1970-01-01T00:00:00Z",
        },
        "player": {
            "location": "yard",
            "status": "active",
            "reputation": {},
            "resources": {},
            "known_abilities": [],
            "constraints": [],
            "known_world_facts": [],
        },
        "characters": {
            "gorazd": {
                "name": "Горазд",
                "aliases": ["Горазда"],
                "location": "yard",
                "status": "alive",
                "loyalty": "zhdan-household",
            },
            "ratibor": {
                "name": "Ратибор",
                "aliases": ["Ратибора"],
                "location": "river",
                "status": "alive",
                "loyalty": "svyatoslav-retinue",
            },
            "milorad": {
                "name": "Милорад",
                "aliases": ["Милорада", "Милорадом"],
                "location": "market",
                "status": "alive",
                "loyalty": "podil-traders",
            },
        },
        "factions": {
            "zhdan-household": {"name": "дом Ждана"},
            "svyatoslav-retinue": {"name": "дружина Святослава"},
            "podil-traders": {"name": "подольские торговцы"},
        },
        "locations": {
            "yard": {"name": "двор", "aliases": ["во дворе"]},
            "river": {"name": "Почайна", "aliases": ["у реки"]},
            "market": {"name": "Торговая площадь", "aliases": ["рынок", "торг"]},
        },
        "resources": {},
        "relationships": {},
        "active_threads": [],
        "completed_threads": [],
        "world_constraints": [],
        "timeline": [],
        "last_turn": {
            "turn": 14,
            "player_message": "",
            "narrator_response": "",
            "state_patch_id": "",
        },
        "uncertain_facts": [],
        "scene_state": {
            "schema_version": "rp-gateway.scene-state.v1",
            "location_id": "yard",
            "present_character_ids": ["gorazd"],
            "stable_affiliations": {
                "gorazd": "zhdan-household",
                "milorad": "podil-traders",
                "ratibor": "svyatoslav-retinue",
            },
            "as_of_state_version": 1,
            "as_of_party_turn": 14,
            "stale": False,
            "stale_reason": None,
        },
    }


def provider_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "scene-bundle-response",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            }
        ],
    }


def scene_bundle(
    *,
    text: str = "Горазд остаётся во дворе.",
    claims: dict[str, Any] | None = None,
    delta: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "rp-gateway.rp-narrator-bundle.v1",
        "narrative_text": text,
        "scene_claims": claims
        if claims is not None
        else {"location_id": "yard", "present_character_ids": ["gorazd"]},
        "scene_delta": delta if delta is not None else [],
    }


def scene_allowance(**overrides: Any) -> dict[str, Any]:
    allowance: dict[str, Any] = {
        "current_location_id": "yard",
        "allowed_destination_ids": [],
        "allowed_arrival_ids": [],
        "allowed_departure_ids": [],
        "stable_affiliations": {
            "gorazd": "zhdan-household",
            "milorad": "podil-traders",
            "ratibor": "svyatoslav-retinue",
        },
        "character_aliases": {
            "gorazd": ["Горазд", "Горазда"],
            "milorad": ["Милорад", "Милорада", "Милорадом"],
            "ratibor": ["Ратибор", "Ратибора"],
        },
    }
    allowance.update(overrides)
    return {"scene_allowance": allowance}


def relationship_model() -> dict[str, Any]:
    return {
        "schema_version": "rp-relationships.v2",
        "characters": {
            "gorazd": {"aliases": ["Горазд", "Горазда"]},
            "milorad": {"aliases": ["Милорад", "Милорада", "Милорадом"]},
            "ratibor": {"aliases": ["Ратибор", "Ратибора"]},
        },
        "axes": {},
        "events": {},
        "character_weights": {},
        "roles": {},
        "wounds": {},
        "clocks": {},
        "trust_mapping": {"kind": "linear", "in": [-10, 10], "out": [-40, 40]},
        "plot": {"tell_required_every_turn": True, "discovery_chance_per_turn": 0.0},
    }


def revision_seven_adjudicator(tmp_path: Path, campaign_id: str) -> tuple[Adjudicator, StateStore]:
    current = scene_state()
    current["meta"]["campaign_id"] = campaign_id
    state_path = tmp_path / f"{campaign_id}.json"
    state_path.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    store = StateStore(str(tmp_path / f"{campaign_id}.db"), campaign_id, str(state_path))
    adjudicator = Adjudicator(
        Settings(
            app_env="test",
            campaign_id=campaign_id,
            scenario_type="rp",
            rp_contract_version="rp-core.v2",
            rp_contract_revision=7,
            nvidia_api_base="mock://success",
            local_llm_enabled=False,
            post_turn_helpers_inline=False,
        ),
        store,
    )
    return adjudicator, store


def table_count(store: StateStore, table: str) -> int:
    with store.connect() as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE campaign_id = ?",  # noqa: S608 - fixed test tables
                (store.campaign_id,),
            ).fetchone()[0]
        )


def authoritative_counts(store: StateStore) -> dict[str, int]:
    return {
        table: table_count(store, table)
        for table in ("state_versions", "state_patches", "turns")
    }


def materialize(
    payload: dict[str, Any],
    *,
    action: str = "Я отвечаю Горазду.",
    current: dict[str, Any] | None = None,
    outcome: dict[str, Any] | None = None,
):
    return materialize_scene_bundle(
        provider_response(payload),
        current or scene_state(),
        latest_user_message=action,
        party_turn=15,
        authoritative_outcome=outcome or scene_allowance(),
    )


def test_minimal_bundle_shape_and_reliable_projection() -> None:
    result = materialize(scene_bundle())

    assert SCENE_BUNDLE_SCHEMA == "rp-gateway.rp-narrator-bundle.v1"
    assert result.valid is True
    assert result.text == "Горазд остаётся во дворе."
    assert result.claims == {"location_id": "yard", "present_character_ids": ["gorazd"]}
    assert result.applied_operations == []
    assert result.dropped_operations == []
    assert result.scene_state == {
        "schema_version": "rp-gateway.scene-state.v1",
        "location_id": "yard",
        "present_character_ids": ["gorazd"],
        "stable_affiliations": {
            "gorazd": "zhdan-household",
            "milorad": "podil-traders",
            "ratibor": "svyatoslav-retinue",
        },
        "as_of_state_version": 2,
        "as_of_party_turn": 15,
        "stale": False,
        "stale_reason": None,
    }


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({**scene_bundle(), "unexpected": True}, id="root-extra"),
        pytest.param(
            scene_bundle(
                claims={
                    "location_id": "yard",
                    "present_character_ids": ["gorazd"],
                    "confidence": 1.0,
                }
            ),
            id="claims-extra",
        ),
        pytest.param(
            scene_bundle(
                delta=[
                    {
                        "type": "move_player",
                        "location_id": "market",
                        "evidence": "Я иду на рынок.",
                        "path": "/player/location",
                    }
                ]
            ),
            id="operation-extra",
        ),
        pytest.param({**scene_bundle(), "narrative_text": ["not", "text"]}, id="narrative-type"),
        pytest.param(
            scene_bundle(claims={"location_id": "yard", "present_character_ids": "gorazd"}),
            id="present-character-type",
        ),
        pytest.param({**scene_bundle(), "scene_delta": {}}, id="delta-type"),
        pytest.param(
            scene_bundle(
                delta=[{"type": 1, "location_id": "market", "evidence": "Я иду на рынок."}]
            ),
            id="operation-type",
        ),
    ],
)
def test_bundle_is_strict_at_every_schema_level(payload: dict[str, Any]) -> None:
    result = materialize(payload, action="Я иду на рынок.")

    assert result.valid is False
    assert result.scene_state is None
    assert result.violations


def test_bundle_numeric_bounds_are_explicit() -> None:
    assert MAX_SCENE_DELTA_OPERATIONS == 16
    assert MAX_SCENE_PRESENT_CHARACTERS == 64
    assert MAX_SCENE_ENTITY_ID_CHARS == 128
    assert MAX_SCENE_EVIDENCE_CHARS == 512

    overlong_id = "x" * (MAX_SCENE_ENTITY_ID_CHARS + 1)
    overlong_evidence = "x" * (MAX_SCENE_EVIDENCE_CHARS + 1)
    cases = [
        scene_bundle(
            delta=[
                {"type": "move_player", "location_id": "market", "evidence": "Я иду на рынок."}
                for _ in range(MAX_SCENE_DELTA_OPERATIONS + 1)
            ]
        ),
        scene_bundle(claims={"location_id": overlong_id, "present_character_ids": ["gorazd"]}),
        scene_bundle(
            delta=[{"type": "move_player", "location_id": "market", "evidence": overlong_evidence}]
        ),
    ]
    crowded = scene_state()
    crowded["characters"] = {
        f"npc-{index:02d}": {"name": f"NPC {index}", "location": "yard"}
        for index in range(MAX_SCENE_PRESENT_CHARACTERS + 1)
    }
    cases.append(
        scene_bundle(
            claims={
                "location_id": "yard",
                "present_character_ids": sorted(crowded["characters"]),
            }
        )
    )

    for index, payload in enumerate(cases[:3]):
        result = materialize(payload, action="Я иду на рынок.")
        assert result.valid is False, index
        assert result.scene_state is None, index
    crowded_result = materialize(cases[3], current=crowded)
    assert crowded_result.valid is False
    assert crowded_result.scene_state is None


@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"location_id": "unknown", "present_character_ids": ["gorazd"]}, id="location"),
        pytest.param({"location_id": "yard", "present_character_ids": ["unknown"]}, id="character"),
        pytest.param({"location_id": "yard", "present_character_ids": ["gorazd", "gorazd"]}, id="duplicate"),
        pytest.param({"location_id": "yard", "present_character_ids": ["milorad", "gorazd"]}, id="unsorted"),
    ],
)
def test_unknown_duplicate_or_unsorted_claim_ids_are_hard(claims: dict[str, Any]) -> None:
    result = materialize(scene_bundle(claims=claims))

    assert result.valid is False
    assert result.scene_state is None
    assert result.violations


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            {"type": "set_player_belief", "value": "Милорад честен", "evidence": "Я верю Милораду."},
            id="belief",
        ),
        pytest.param(
            {"type": "set_player_emotion", "value": "страх", "evidence": "Мне страшно."},
            id="emotion",
        ),
        pytest.param(
            {"type": "set_player_state", "field": "goal", "value": "уйти", "evidence": "Я хочу уйти."},
            id="arbitrary-state",
        ),
    ],
)
def test_player_belief_emotion_and_arbitrary_state_operations_are_forbidden(
    operation: dict[str, Any],
) -> None:
    result = materialize(scene_bundle(delta=[operation]))

    assert result.valid is False
    assert result.scene_state is None
    assert any("forbid" in violation.lower() or "type" in violation.lower() for violation in result.violations)


def test_normalized_literal_narrator_evidence_anchors_character_arrival() -> None:
    assert normalize_anchor_text("  МИЛОРАД—входит, во двор! ") == "милорад входит во двор"
    result = materialize(
        scene_bundle(
            text="За воротами шум. МИЛОРАД—входит, во двор!",
            claims={"location_id": "yard", "present_character_ids": ["gorazd", "milorad"]},
            delta=[
                {
                    "type": "character_arrive",
                    "character_id": "milorad",
                    "location_id": "yard",
                    "evidence": "Милорад входит во двор",
                }
            ],
        ),
        outcome=scene_allowance(allowed_arrival_ids=["milorad"]),
    )

    assert result.valid is True
    assert result.scene_state["present_character_ids"] == ["gorazd", "milorad"]
    assert result.scene_state["stale"] is False
    assert result.applied_operations == [
        {
            "type": "character_arrive",
            "character_id": "milorad",
            "location_id": "yard",
            "evidence": "Милорад входит во двор",
        }
    ]


@pytest.mark.parametrize(
    "action",
    [
        pytest.param("Я иду к большому торгу за рекой.", id="natural-unnormalized-destination"),
        pytest.param(
            "Я иду к ярмарочной слободе за холмом.",
            id="natural-destination-without-authored-alias",
        ),
        pytest.param("Я иду на рынок, просто купить хлеб.", id="natural-purpose-clause"),
        pytest.param("Я иду со двора на рынок.", id="origin-and-destination"),
    ],
)
def test_explicit_first_person_named_destination_can_select_known_location_id(action: str) -> None:
    result = materialize(
        scene_bundle(
            text="Ты выходишь со двора и оказываешься у торговых рядов.",
            claims={"location_id": "market", "present_character_ids": ["milorad"]},
            delta=[
                {
                    "type": "move_player",
                    "location_id": "market",
                    "evidence": action.rstrip("."),
                }
            ],
        ),
        action=action,
    )

    assert result.valid is True
    assert result.scene_state["location_id"] == "market"
    assert result.scene_state["present_character_ids"] == ["milorad"]
    assert result.applied_operations[0]["type"] == "move_player"


def test_first_person_move_can_remain_at_the_new_destination() -> None:
    action = "Я иду на рынок и остаюсь там."
    result = materialize(
        scene_bundle(
            text="Ты приходишь на рынок и остаёшься там.",
            claims={"location_id": "market", "present_character_ids": ["milorad"]},
            delta=[
                {"type": "move_player", "location_id": "market", "evidence": action.rstrip(".")}
            ],
        ),
        action=action,
    )

    assert result.valid is True
    assert result.scene_state["location_id"] == "market"


@pytest.mark.parametrize(
    "action",
    [
        pytest.param("Ратибор идёт на рынок.", id="third-person"),
        pytest.param("Я не иду на рынок.", id="negated"),
        pytest.param("Нет, я лишь упомянул рынок и остаюсь во дворе.", id="correction"),
        pytest.param("На рынке сегодня людно.", id="mention"),
        pytest.param("Я иду.", id="no-destination"),
        pytest.param("Я иду медленно.", id="adverb-is-not-destination"),
        pytest.param(
            "Я иду поговорить с Гораздом на эту тему.",
            id="later-preposition-is-not-destination",
        ),
        pytest.param(
            "Я иду на рынок, но остаюсь во дворе.",
            id="explicit-remain-cancels-movement",
        ),
        pytest.param(
            "Я иду медленно. На рынке сегодня дождь.",
            id="later-sentence-location-mention-is-not-destination",
        ),
    ],
)
def test_player_destination_allowance_rejects_non_first_person_or_missing_departure(
    action: str,
) -> None:
    result = materialize(
        scene_bundle(
            text="Ты оказываешься на рынке.",
            claims={"location_id": "market", "present_character_ids": ["milorad"]},
            delta=[
                {"type": "move_player", "location_id": "market", "evidence": action.rstrip(".")}
            ],
        ),
        action=action,
    )

    assert result.valid is False
    assert result.scene_state is None
    assert result.violations


def test_outcome_target_alone_never_authorizes_scene_transition() -> None:
    outcome = scene_allowance()
    outcome["target"] = "market"
    result = materialize(
        scene_bundle(
            text="Ты оказываешься на рынке.",
            claims={"location_id": "market", "present_character_ids": ["milorad"]},
            delta=[
                {"type": "move_player", "location_id": "market", "evidence": "Я осматриваюсь"}
            ],
        ),
        action="Я осматриваюсь.",
        outcome=outcome,
    )

    assert result.valid is False
    assert result.scene_state is None


def test_first_person_destination_allowance_never_authorizes_npc_arrival() -> None:
    result = materialize(
        scene_bundle(
            text="Милорад входит во двор.",
            claims={"location_id": "yard", "present_character_ids": ["gorazd", "milorad"]},
            delta=[
                {
                    "type": "character_arrive",
                    "character_id": "milorad",
                    "location_id": "yard",
                    "evidence": "Милорад входит во двор",
                }
            ],
        ),
        action="Я иду к Милораду на рынок.",
    )

    assert result.valid is False
    assert result.scene_state is None


@pytest.mark.parametrize(
    ("action", "payload"),
    [
        pytest.param(
            "Милорад не входит, а Горазд приходит.",
            scene_bundle(
                text="Милорад входит во двор.",
                claims={"location_id": "yard", "present_character_ids": ["gorazd", "milorad"]},
                delta=[
                    {
                        "type": "character_arrive",
                        "character_id": "milorad",
                        "location_id": "yard",
                        "evidence": "Милорад входит во двор",
                    }
                ],
            ),
            id="arrival-verb-belongs-to-another-character",
        ),
        pytest.param(
            "Горазд остаётся. Милорад уходит на рынок.",
            scene_bundle(
                text="Горазд уходит на рынок.",
                claims={"location_id": "yard", "present_character_ids": []},
                delta=[
                    {
                        "type": "character_depart",
                        "character_id": "gorazd",
                        "location_id": "market",
                        "evidence": "Горазд уходит на рынок",
                    }
                ],
            ),
            id="departure-verb-belongs-to-another-character",
        ),
    ],
)
def test_transition_allowance_binds_character_and_verb_in_the_same_clause(
    action: str,
    payload: dict[str, Any],
) -> None:
    result = materialize(payload, action=action)

    assert result.valid is False
    assert result.scene_state is None


def test_absent_character_cannot_depart_even_with_departure_allowance() -> None:
    result = materialize(
        scene_bundle(
            text="Ратибор уходит со двора.",
            delta=[
                {
                    "type": "character_depart",
                    "character_id": "ratibor",
                    "location_id": "river",
                    "evidence": "Ратибор уходит со двора",
                }
            ],
        ),
        outcome=scene_allowance(allowed_departure_ids=["ratibor"]),
    )

    assert result.valid is False
    assert result.scene_state is None
    assert any("absent" in violation.lower() or "outside" in violation.lower() for violation in result.violations)


@pytest.mark.parametrize(
    ("destination", "expected_valid"),
    [
        pytest.param("river", True, id="different-known-destination"),
        pytest.param("yard", False, id="current-scene-is-not-a-departure-destination"),
    ],
)
def test_character_departure_requires_a_destination_outside_current_scene(
    destination: str,
    expected_valid: bool,
) -> None:
    result = materialize(
        scene_bundle(
            text="Горазд уходит со двора к реке.",
            claims={"location_id": "yard", "present_character_ids": []},
            delta=[
                {
                    "type": "character_depart",
                    "character_id": "gorazd",
                    "location_id": destination,
                    "evidence": "Горазд уходит со двора к реке",
                }
            ],
        ),
        outcome=scene_allowance(allowed_departure_ids=["gorazd"]),
    )

    assert result.valid is expected_valid
    assert (result.scene_state is not None) is expected_valid


def test_stable_affiliation_prose_conflict_is_hard_with_empty_delta() -> None:
    result = materialize(
        scene_bundle(text="Горазд теперь приказчик Милорада и служит ему, а не дому Ждана."),
        outcome=scene_allowance(),
    )

    assert result.valid is False
    assert result.scene_state is None
    assert any("stable affiliation" in violation.lower() for violation in result.violations)


def test_single_stable_character_cannot_switch_to_an_unassigned_known_faction() -> None:
    current = scene_state()
    current["characters"] = {"gorazd": current["characters"]["gorazd"]}
    current["scene_state"]["stable_affiliations"] = {"gorazd": "zhdan-household"}
    current["factions"]["podil-traders"]["aliases"] = ["подольским торговцам"]
    result = materialize(
        scene_bundle(text="Горазд теперь служит подольским торговцам."),
        current=current,
        outcome=scene_allowance(
            stable_affiliations={"gorazd": "zhdan-household"},
            character_aliases={"gorazd": ["Горазд", "Горазда"]},
        ),
    )

    assert result.valid is False
    assert result.scene_state is None
    assert any("stable affiliation" in violation.lower() for violation in result.violations)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(
            "Горазд служит дому Ждана вместе с Милорадом.",
            id="correct-affiliation-near-another-character",
        ),
        pytest.param(
            "Милорад сказал: «Горазд служит мне».",
            id="embedded-character-quote-is-not-canon",
        ),
        pytest.param(
            "Неверно, будто Горазд служит Милораду.",
            id="explicit-correction-is-not-a-new-role",
        ),
        pytest.param(
            "Горазд теперь хранитель старой клятвы.",
            id="unknown-free-prose-remains-outside-finite-gate",
        ),
    ],
)
def test_stable_affiliation_guard_avoids_false_positive_prose(text: str) -> None:
    result = materialize(scene_bundle(text=text), outcome=scene_allowance())

    assert result.valid is True
    assert result.violations == []


def test_merchant_t15_claim_reset_is_rejected_before_commit_projection() -> None:
    result = materialize(
        scene_bundle(
            text="Ратибор и Милорад снова стоят рядом.",
            claims={"location_id": "yard", "present_character_ids": ["milorad", "ratibor"]},
        ),
        action="Нет, пожар уже потушен и Ратибор спасён.",
    )

    assert result.valid is False
    assert result.scene_state is None
    assert any("present_character_ids" in violation for violation in result.violations)


def test_authorized_unanchored_operation_soft_drops_with_full_value_and_evidence() -> None:
    result = materialize(
        scene_bundle(
            text="Горазд остаётся во дворе.",
            delta=[
                {
                    "type": "move_player",
                    "location_id": "market",
                    "evidence": "Я иду на рынок",
                }
            ],
        ),
        action="Я иду на рынок.",
    )

    assert result.valid is True
    assert result.applied_operations == []
    assert len(result.dropped_operations) == 1
    dropped = result.dropped_operations[0]
    assert {key: dropped[key] for key in ("type", "location_id", "evidence")} == {
        "type": "move_player",
        "location_id": "market",
        "evidence": "Я иду на рынок",
    }
    assert isinstance(dropped["reason"], str) and dropped["reason"].startswith("unanchored")
    assert result.scene_state["location_id"] == "yard"
    assert result.scene_state["as_of_state_version"] == 1
    assert result.scene_state["as_of_party_turn"] == 14
    assert result.scene_state["stale"] is True
    assert result.scene_state["stale_reason"].startswith("unanchored")


def test_well_typed_but_nonliteral_evidence_is_soft_drop_not_hard_failure() -> None:
    result = materialize(
        scene_bundle(
            text="Горазд остаётся во дворе.",
            delta=[
                {
                    "type": "move_player",
                    "location_id": "market",
                    "evidence": "Этой фразы нет ни в одном тексте",
                }
            ],
        ),
        action="Я иду на рынок.",
    )

    assert result.valid is True
    assert result.applied_operations == []
    assert result.dropped_operations[0]["reason"] == "unanchored_evidence"
    assert result.dropped_operations[0]["evidence"] == "Этой фразы нет ни в одном тексте"
    assert result.scene_state["stale"] is True


@pytest.mark.parametrize(
    ("payload", "action", "outcome"),
    [
        pytest.param(
            scene_bundle(
                text="Ты оказываешься на рынке.",
                claims={"location_id": "market", "present_character_ids": ["milorad"]},
                delta=[
                    {"type": "move_player", "location_id": "market", "evidence": "я"}
                ],
            ),
            "Я иду на рынок.",
            scene_allowance(),
            id="move-evidence-must-name-effect",
        ),
        pytest.param(
            scene_bundle(
                text="Сегодня Милорад входит во двор.",
                claims={"location_id": "yard", "present_character_ids": ["gorazd", "milorad"]},
                delta=[
                    {
                        "type": "character_arrive",
                        "character_id": "milorad",
                        "location_id": "yard",
                        "evidence": "сегодня",
                    }
                ],
            ),
            "Я отвечаю Горазду.",
            scene_allowance(allowed_arrival_ids=["milorad"]),
            id="arrival-evidence-must-name-character-and-transition",
        ),
    ],
)
def test_literal_but_effect_free_evidence_is_soft_dropped(
    payload: dict[str, Any],
    action: str,
    outcome: dict[str, Any],
) -> None:
    result = materialize(payload, action=action, outcome=outcome)

    assert result.valid is True
    assert result.applied_operations == []
    assert result.dropped_operations[0]["reason"] == "unanchored_evidence"
    assert result.scene_state["stale"] is True


def test_unanchored_operation_commits_once_without_repair_and_is_durably_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "unanchored-no-repair")
    raw = provider_response(
        scene_bundle(
            delta=[
                {
                    "type": "move_player",
                    "location_id": "market",
                    "evidence": "Я иду на рынок",
                }
            ]
        )
    )
    calls = 0

    async def unanchored_response(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return raw

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "complete", unanchored_response)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)

    result = asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Я иду на рынок.")],
            ),
            authorization=None,
            idempotency_key="unanchored-no-repair",
            request_id="req-unanchored-no-repair",
        )
    )

    assert calls == 1
    assert response_text(result) == "Горазд остаётся во дворе."
    current = store.get_state()
    assert current["meta"]["turn"] == 15
    assert current["scene_state"]["stale"] is True
    assert current["scene_state"]["as_of_party_turn"] == 14
    with store.connect() as connection:
        row = connection.execute(
            "SELECT metadata_json FROM turns WHERE campaign_id = ?",
            (store.campaign_id,),
        ).fetchone()
        audit_rows = connection.execute(
            "SELECT event_json FROM audit_events "
            "WHERE campaign_id = ? AND request_id = ? "
            "AND event_type = 'scene_delta_operations_dropped'",
            (store.campaign_id, "req-unanchored-no-repair"),
        ).fetchall()
    assert row is not None
    metadata = json.loads(row["metadata_json"])
    assert metadata["applied_scene_delta"] == []
    assert len(metadata["dropped_scene_delta"]) == 1
    dropped = metadata["dropped_scene_delta"][0]
    assert {key: dropped[key] for key in ("type", "location_id", "evidence")} == {
        "type": "move_player",
        "location_id": "market",
        "evidence": "Я иду на рынок",
    }
    assert isinstance(dropped["reason"], str) and dropped["reason"].startswith("unanchored")
    assert metadata["scene_state_after"]["stale"] is True
    assert len(audit_rows) == 1
    assert json.loads(audit_rows[0]["event_json"])["dropped_scene_delta"] == metadata[
        "dropped_scene_delta"
    ]


@pytest.mark.parametrize(
    ("action", "payload", "expected_player_location", "character_id", "expected_character_location"),
    [
        pytest.param(
            "Я иду на рынок.",
            scene_bundle(
                text="Ты идёшь на рынок.",
                claims={"location_id": "market", "present_character_ids": ["milorad"]},
                delta=[
                    {
                        "type": "move_player",
                        "location_id": "market",
                        "evidence": "Я иду на рынок",
                    }
                ],
            ),
            "market",
            "gorazd",
            "yard",
            id="move-player",
        ),
        pytest.param(
            "Милорад входит во двор.",
            scene_bundle(
                text="Милорад входит во двор.",
                claims={"location_id": "yard", "present_character_ids": ["gorazd", "milorad"]},
                delta=[
                    {
                        "type": "character_arrive",
                        "character_id": "milorad",
                        "location_id": "yard",
                        "evidence": "Милорад входит во двор",
                    }
                ],
            ),
            "yard",
            "milorad",
            "yard",
            id="character-arrive",
        ),
        pytest.param(
            "Горазд уходит на рынок.",
            scene_bundle(
                text="Горазд уходит на рынок.",
                claims={"location_id": "yard", "present_character_ids": []},
                delta=[
                    {
                        "type": "character_depart",
                        "character_id": "gorazd",
                        "location_id": "market",
                        "evidence": "Горазд уходит на рынок",
                    }
                ],
            ),
            "yard",
            "gorazd",
            "market",
            id="character-depart",
        ),
    ],
)
def test_atomic_scene_commit_keeps_legacy_locations_consistent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    payload: dict[str, Any],
    expected_player_location: str,
    character_id: str,
    expected_character_location: str,
) -> None:
    adjudicator, store = revision_seven_adjudicator(
        tmp_path,
        f"canonical-location-{payload['scene_delta'][0]['type']}",
    )

    async def provider(*args: object, **kwargs: object) -> dict[str, Any]:
        return provider_response(deepcopy(payload))

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "complete", provider)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)

    asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content=action)],
            ),
            authorization=None,
            idempotency_key=f"canonical-location-{payload['scene_delta'][0]['type']}",
            request_id=f"req-canonical-location-{payload['scene_delta'][0]['type']}",
        )
    )

    current = store.get_state()
    assert current["player"]["location"] == expected_player_location
    assert current["characters"][character_id]["location"] == expected_character_location
    assert current["scene_state"]["location_id"] == expected_player_location
    expected_present = sorted(
        character
        for character, details in current["characters"].items()
        if details.get("location") == expected_player_location
    )
    assert current["scene_state"]["present_character_ids"] == expected_present


@pytest.mark.parametrize(
    ("patch_operations", "claims"),
    [
        pytest.param(
            [
                PatchOperation(
                    op="replace",
                    path="/player/location",
                    value="market",
                    reason="world command moves the player",
                    turn=15,
                )
            ],
            {"location_id": "market", "present_character_ids": ["milorad"]},
            id="player-location-change",
        ),
        pytest.param(
            [
                PatchOperation(
                    op="remove",
                    path="/characters/gorazd",
                    reason="world command removes a previously present character",
                    turn=15,
                )
            ],
            {"location_id": "yard", "present_character_ids": []},
            id="present-character-removed",
        ),
        pytest.param(
            [
                PatchOperation(
                    op="remove",
                    path="/locations/yard",
                    reason="world command removes the previous location",
                    turn=15,
                ),
                PatchOperation(
                    op="replace",
                    path="/player/location",
                    value="market",
                    reason="world command establishes the current location",
                    turn=15,
                ),
            ],
            {"location_id": "market", "present_character_ids": ["milorad"]},
            id="current-location-removed",
        ),
    ],
)
def test_stale_world_projection_can_reanchor_from_authoritative_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch_operations: list[PatchOperation],
    claims: dict[str, Any],
) -> None:
    campaign_id = f"world-reanchor-{claims['location_id']}-{len(patch_operations)}"
    adjudicator, store = revision_seven_adjudicator(tmp_path, campaign_id)
    changed = store.apply_state_patch(
        StatePatch(
            turn=15,
            check_id=f"{campaign_id}:world-change",
            patch=patch_operations,
        ),
        reason="api_patch_apply",
    )
    assert changed["scene_state"]["stale"] is True
    assert changed["scene_state"]["as_of_party_turn"] == 14

    calls = 0
    captured_prompts: list[list[dict[str, str]]] = []
    original_messages = adjudicator.narrative.narrative_messages

    def capture_messages(*args: object, **kwargs: object) -> list[dict[str, str]]:
        messages = original_messages(*args, **kwargs)
        captured_prompts.append(messages)
        return messages

    async def provider(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return provider_response(
            scene_bundle(
                text="Сцена вновь привязана к текущему состоянию мира.",
                claims=claims,
            )
        )

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "narrative_messages", capture_messages)
    monkeypatch.setattr(adjudicator.narrative, "complete", provider)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Я осматриваюсь и продолжаю сцену.")],
            ),
            authorization=None,
            idempotency_key=f"{campaign_id}:reanchor",
            request_id=f"req-{campaign_id}:reanchor",
        )
    )

    current = store.get_state()
    assert calls == 1
    assert current["scene_state"]["stale"] is False
    assert current["scene_state"]["location_id"] == claims["location_id"]
    assert current["scene_state"]["present_character_ids"] == claims[
        "present_character_ids"
    ]
    assert captured_prompts
    scene_contract = next(
        message["content"]
        for message in captured_prompts[-1]
        if message["role"] == "system"
        and message["content"].startswith("SCENE_STATE_CONTRACT")
    )
    assert "LAST_RELIABLE_SCENE_STATE" in scene_contract
    reanchor_blocks = [
        message["content"]
        for message in captured_prompts[-1]
        if message["role"] == "system"
        and message["content"].startswith("SCENE_REANCHOR_BASELINE\n")
    ]
    assert len(reanchor_blocks) == 1
    assert json.loads(reanchor_blocks[0].split("\n", 1)[1]) == claims


def test_world_affiliation_change_reanchors_without_restoring_stale_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "world-affiliation-reanchor")
    changed = store.apply_state_patch(
        StatePatch(
            turn=15,
            check_id="world-affiliation-reanchor:change",
            patch=[
                PatchOperation(
                    op="replace",
                    path="/characters/gorazd/loyalty",
                    value="podil-traders",
                    reason="world command changes the canonical affiliation",
                    turn=15,
                )
            ],
        ),
        reason="api_patch_apply",
    )
    assert changed["scene_state"]["stale"] is True
    assert changed["scene_state"]["stable_affiliations"]["gorazd"] == "zhdan-household"

    async def provider(*args: object, **kwargs: object) -> dict[str, Any]:
        return provider_response(
            scene_bundle(
                text="Горазд теперь служит подольским торговцам.",
                claims={"location_id": "yard", "present_character_ids": ["gorazd"]},
            )
        )

    async def skip_post_turn_helpers(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(adjudicator.narrative, "complete", provider)
    monkeypatch.setattr(adjudicator, "after_turn_recorded", skip_post_turn_helpers)
    asyncio.run(
        adjudicator.handle_chat(
            ChatCompletionRequest(
                model="mock-narrator",
                messages=[ChatMessage(role="user", content="Я продолжаю разговор.")],
            ),
            authorization=None,
            idempotency_key="world-affiliation-reanchor",
            request_id="req-world-affiliation-reanchor",
        )
    )

    current = store.get_state()
    assert current["scene_state"]["stale"] is False
    assert current["scene_state"]["stable_affiliations"]["gorazd"] == "podil-traders"


@pytest.mark.parametrize(
    "invalid_payload",
    [
        pytest.param(
            scene_bundle(claims={"location_id": "yard", "present_character_ids": ["unknown"]}),
            id="unknown-id",
        ),
        pytest.param(
            scene_bundle(
                delta=[
                    {
                        "type": "set_player_emotion",
                        "value": "страх",
                        "evidence": "Мне страшно.",
                    }
                ]
            ),
            id="forbidden-operation",
        ),
        pytest.param(
            scene_bundle(
                text="Милорад входит во двор.",
                claims={"location_id": "yard", "present_character_ids": ["gorazd", "milorad"]},
                delta=[
                    {
                        "type": "character_arrive",
                        "character_id": "milorad",
                        "location_id": "yard",
                        "evidence": "Милорад входит во двор",
                    }
                ],
            ),
            id="unauthorized-transition",
        ),
        pytest.param(
            scene_bundle(
                claims={"location_id": "yard", "present_character_ids": ["milorad", "ratibor"]}
            ),
            id="claims-mismatch",
        ),
    ],
)
def test_second_hard_bundle_violation_never_commits_or_uses_safe_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid_payload: dict[str, Any],
) -> None:
    adjudicator, store = revision_seven_adjudicator(tmp_path, "hard-scene-mismatch")
    calls = 0

    async def repeated_invalid(*args: object, **kwargs: object) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return provider_response(deepcopy(invalid_payload))

    monkeypatch.setattr(adjudicator.narrative, "complete", repeated_invalid)
    before = authoritative_counts(store)

    with pytest.raises(SceneContinuityError):
        asyncio.run(
            adjudicator.handle_chat(
                ChatCompletionRequest(
                    model="mock-narrator",
                    messages=[ChatMessage(role="user", content="Я отвечаю Горазду.")],
                ),
                authorization=None,
                idempotency_key="hard-scene-mismatch",
                request_id="req-hard-scene-mismatch",
            )
        )

    assert calls == 2
    assert authoritative_counts(store) == before
    assert store.get_state()["meta"]["turn"] == 14
    saved_request = store.get_turn_request("req-hard-scene-mismatch")
    assert saved_request is not None
    assert saved_request["status"] == "failed"
    with store.connect() as connection:
        fallback_count = connection.execute(
            "SELECT COUNT(*) FROM audit_events "
            "WHERE campaign_id = ? AND request_id = ? AND event_type = 'llm_safe_fallback'",
            (store.campaign_id, "req-hard-scene-mismatch"),
        ).fetchone()[0]
    assert fallback_count == 0
