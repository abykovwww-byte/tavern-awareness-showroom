from app.services.character_retrieval import compact_character, compact_relationship


def test_retrieval_does_not_surface_canonical_trust() -> None:
    character = compact_character("ivan", {"name": "Иван", "trust": 7, "fear": 2})
    relationship = compact_relationship(
        {"from": "player", "to": "ivan", "trust": 7, "suspicion": 1}
    )

    assert "trust" not in character
    assert "trust" not in relationship
    assert relationship["suspicion"] == 1
