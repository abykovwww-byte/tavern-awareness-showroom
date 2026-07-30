from __future__ import annotations

import pytest

from app.services.showroom import ShowroomStore


def portal_manifest(character_count: int = 2) -> dict[str, object]:
    characters: list[dict[str, str]] = [
        {
            "id": "manager",
            "display_name": "Анна Петрова",
            "type": "dynamic",
            "position_template": "Руководитель сотрудника · {employee_position}",
            "city": "Москва",
            "phone": "+7 495 000-00-01",
            "email": "petrova@example.test",
        },
        {
            "id": "security",
            "display_name": "Сергей Литвинов",
            "type": "static",
            "position": "Специалист ДИБ",
            "messenger": "litvinov",
        },
    ]
    while len(characters) < character_count:
        index = len(characters)
        characters.append(
            {
                "id": f"static-{index}",
                "display_name": f"Сотрудник {index}",
                "type": "static",
                "position": "Сотрудник",
            }
        )
    return {"corporate_portal": {"title": "Корпоративный портал", "characters": characters}}


def showroom_store_without_db() -> ShowroomStore:
    return ShowroomStore.__new__(ShowroomStore)


def test_training_portal_materializes_dynamic_positions_without_changing_static_cards():
    portal = showroom_store_without_db().materialize_portal(
        portal_manifest(),
        employee_name="Ирина",
        employee_position="аналитик информационной безопасности",
    )

    assert portal is not None
    assert portal["title"] == "Корпоративный портал"
    assert len(portal["characters"]) == 2
    assert portal["characters"][0]["position"] == (
        "Руководитель сотрудника · аналитик информационной безопасности"
    )
    assert portal["characters"][0]["source_type"] == "dynamic"
    assert portal["characters"][1]["position"] == "Специалист ДИБ"
    assert portal["characters"][1]["source_type"] == "static"


def test_training_portal_requires_employee_position_for_dynamic_cards():
    with pytest.raises(ValueError, match="employee_position is required"):
        showroom_store_without_db().materialize_portal(
            portal_manifest(),
            employee_name="Ирина",
            employee_position="",
        )


def test_training_portal_rejects_more_than_five_characters():
    with pytest.raises(ValueError, match="at most 5 characters"):
        showroom_store_without_db().portal_from_manifest(portal_manifest(6), strict=True)


def test_world_without_training_portal_keeps_existing_showroom_behavior():
    store = showroom_store_without_db()
    assert store.portal_from_manifest({"scenario_types": {"supported": ["rp"]}}, strict=True) is None
    assert store.materialize_portal({}, employee_name="Mira", employee_position="") is None


def test_training_world_owns_showroom_result_path():
    result = showroom_store_without_db().result_from_manifest(
        {
            "scenario_types": {"recommended": "training", "supported": ["training"]},
            "showroom_result": {
                "metric": "state_path",
                "state_path": "player.resources.total-score",
            },
        },
        strict=True,
        fallback_metric="turn_count",
        fallback_state_path="meta.turn",
    )

    assert result == {
        "metric": "state_path",
        "state_path": "player.resources.total-score",
    }


def test_training_world_requires_showroom_result():
    with pytest.raises(ValueError, match="requires showroom_result"):
        showroom_store_without_db().result_from_manifest(
            {"scenario_types": {"recommended": "training", "supported": ["training"]}},
            strict=True,
        )


def test_non_training_world_preserves_legacy_showroom_result():
    result = showroom_store_without_db().result_from_manifest(
        {"scenario_types": {"recommended": "rp", "supported": ["rp"]}},
        strict=True,
        fallback_metric="turn_count",
        fallback_state_path="meta.turn",
    )

    assert result == {"metric": "turn_count", "state_path": "meta.turn"}
