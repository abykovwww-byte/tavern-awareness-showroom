from app.services.character_retrieval import (
    compact_character,
    compact_relationship,
    relationship_scene_character_ids,
)


def test_retrieval_does_not_surface_canonical_trust() -> None:
    character = compact_character("ivan", {"name": "Иван", "trust": 7, "fear": 2})
    relationship = compact_relationship(
        {"from": "player", "to": "ivan", "trust": 7, "suspicion": 1}
    )

    assert "trust" not in character
    assert "trust" not in relationship
    assert relationship["suspicion"] == 1


def test_relationship_scene_scope_uses_three_presence_signals() -> None:
    state = {
        "player": {"location": "market"},
        "characters": {
            "same-location": {"display_name": "Рядом", "location": "market"},
            "explicit-alias": {"display_name": "Вдали", "location": "harbour"},
            "outcome-target": {"display_name": "Цель", "location": "court"},
            "relationship-only": {"display_name": "Лишний", "location": "tower"},
        },
        "active_threads": [],
        "relationships": {
            "player-relationship-only": {
                "from": "player",
                "to": "relationship-only",
                "trust": 5,
            }
        },
    }

    selected = relationship_scene_character_ids(
        state,
        "Мария, ответь мне.",
        outcome_target="outcome-target",
        character_aliases={
            "same-location": ["Рядом"],
            "explicit-alias": ["Мария"],
            "outcome-target": ["Цель"],
            "relationship-only": ["Лишний"],
        },
    )

    assert selected == {
        "same-location",
        "explicit-alias",
        "outcome-target",
    }


def test_relationship_scene_scope_does_not_select_relationship_data_alone() -> None:
    state = {
        "player": {"location": "market"},
        "characters": {
            "present": {"display_name": "Рядом", "location": "market"},
            "remote": {"display_name": "Вдали", "location": "harbour"},
        },
        "active_threads": [],
        "relationships": {
            "player-remote": {"from": "player", "to": "remote", "trust": 9}
        },
        # These private-looking values model durable relationship projections.
        # The selector must not inspect them or turn them into scene presence.
        "relationship_causes": [{"character_id": "remote", "weight": 20}],
        "narrative_events": [
            {"character_id": "remote", "event_id": "favour", "due_turn": 1}
        ],
    }

    selected = relationship_scene_character_ids(
        state,
        "Я осматриваю прилавок.",
        character_aliases={"present": ["Рядом"], "remote": ["Вдали"]},
    )

    assert selected == {"present"}


def test_relationship_scene_scope_excludes_active_thread_only_character() -> None:
    state = {
        "player": {"location": "market"},
        "characters": {
            "present": {"display_name": "Рядом", "location": "market"},
            "thread-only": {"display_name": "Дальний", "location": "harbour"},
        },
        "active_threads": [
            {
                "id": "broad-campaign-thread",
                "description": "Долгая линия со многими персонажами",
                "state": "active",
                "participants": ["thread-only"],
                "deadlines": [],
                "success_conditions": [],
                "failure_conditions": [],
                "confirmed_consequences": [],
            }
        ],
    }

    selected = relationship_scene_character_ids(
        state,
        "Я осматриваю прилавок.",
        character_aliases={"present": ["Рядом"], "thread-only": ["Дальний"]},
    )

    assert selected == {"present"}


def test_relationship_scene_scope_applies_deterministic_top_six() -> None:
    character_ids = [f"npc-{letter}" for letter in "hgfedcba"]
    state = {
        "player": {"location": "market"},
        "characters": {
            character_id: {"display_name": character_id, "location": "market"}
            for character_id in character_ids
        },
        "active_threads": [
            {
                "id": "crowded-thread",
                "description": "Много участников",
                "state": "active",
                "participants": ["npc-h"],
                "deadlines": [],
                "success_conditions": [],
                "failure_conditions": [],
                "confirmed_consequences": [],
            }
        ],
    }

    first = relationship_scene_character_ids(
        state,
        "Я жду.",
        outcome_target="npc-g",
    )
    second = relationship_scene_character_ids(
        state,
        "Я жду.",
        outcome_target="npc-g",
    )

    assert first == second == {f"npc-{letter}" for letter in "abcdgh"}
