from __future__ import annotations

import pytest

from app.services.relationship_attribution import RelationshipExtractionRejected, resolve_mention


ALIASES = {"frol": ["Фрол", "Фролу"], "luka": ["Лука"]}


def test_resolve_mention_uses_normalized_exact_alias_and_verbatim_evidence() -> None:
    assert resolve_mention(
        "  фролу ",
        evidence="Фролу позвали к воротам.",
        turn_text="Игрок оглянулся.\nФролу позвали к воротам.",
        aliases=ALIASES,
    ) == "frol"


@pytest.mark.parametrize(
    ("mention", "evidence", "turn_text", "code"),
    [
        ("Фрол", "Фрол вышел.", "Игрок молчит.\nЛука вышел.", "evidence_not_verbatim"),
        ("Фрол", "Он вышел.", "Игрок молчит.\nОн вышел.", "mention_not_in_evidence"),
        ("Иван", "Иван вошёл.", "Игрок молчит.\nИван вошёл.", "unresolved_mention"),
    ],
)
def test_resolve_mention_rejects_non_deterministic_inputs(
    mention: str,
    evidence: str,
    turn_text: str,
    code: str,
) -> None:
    with pytest.raises(RelationshipExtractionRejected) as exc_info:
        resolve_mention(mention, evidence=evidence, turn_text=turn_text, aliases=ALIASES)
    assert exc_info.value.code == code
    if code == "unresolved_mention":
        assert exc_info.value.mention == "Иван"


def test_resolve_mention_rejects_ambiguous_alias() -> None:
    with pytest.raises(RelationshipExtractionRejected) as exc_info:
        resolve_mention(
            "Фрол",
            evidence="Фрол остановился.",
            turn_text="Фрол остановился.\n",
            aliases={"first": ["Фрол"], "second": ["фрол"]},
        )
    assert exc_info.value.code == "ambiguous_mention"
    assert exc_info.value.mention == "Фрол"
