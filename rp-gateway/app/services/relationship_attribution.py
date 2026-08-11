"""Deterministic character attribution for RP relationship events."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


REJECTION_CODES = frozenset(
    {
        "malformed_response",
        "missing_evidence",
        "evidence_not_verbatim",
        "mention_missing",
        "mention_not_in_evidence",
        "unresolved_mention",
        "ambiguous_mention",
        "unknown_event_id",
        "numeric_field_present",
        "too_many_events",
        "character_id_present",
    }
)


class RelationshipExtractionRejected(ValueError):
    """A relationship extraction response rejected by the frozen contract."""

    def __init__(self, code: str, *, mention: str | None = None):
        if code not in REJECTION_CODES:
            raise ValueError(f"unsupported relationship extraction rejection code: {code}")
        self.code = code
        self.mention = mention
        super().__init__(code)


def normalize_text(value: str) -> str:
    """Normalize only Unicode compatibility forms, case, and whitespace."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def resolve_mention(
    mention: str,
    *,
    evidence: str,
    turn_text: str,
    aliases: dict[str, list[str]],
) -> str:
    """Resolve one model mention against the WorldPack's exact alias table."""
    original_mention = mention if isinstance(mention, str) else None
    if not isinstance(mention, str) or not mention.strip():
        raise RelationshipExtractionRejected("mention_missing", mention=original_mention)
    if not isinstance(evidence, str) or not evidence.strip():
        raise RelationshipExtractionRejected("missing_evidence", mention=mention)

    normalized_evidence = normalize_text(evidence)
    normalized_turn = normalize_text(turn_text) if isinstance(turn_text, str) else ""
    normalized_mention = normalize_text(mention)
    if not normalized_evidence or normalized_evidence not in normalized_turn:
        raise RelationshipExtractionRejected("evidence_not_verbatim", mention=mention)
    if not normalized_mention or normalized_mention not in normalized_evidence:
        raise RelationshipExtractionRejected("mention_not_in_evidence", mention=mention)

    matches: list[str] = []
    for character_id, forms in aliases.items():
        if not isinstance(character_id, str) or not isinstance(forms, list):
            continue
        if any(isinstance(form, str) and normalize_text(form) == normalized_mention for form in forms):
            matches.append(character_id)
    if not matches:
        raise RelationshipExtractionRejected("unresolved_mention", mention=mention)
    if len(matches) > 1:
        raise RelationshipExtractionRejected("ambiguous_mention", mention=mention)
    return matches[0]


def normalized_aliases(model: dict[str, Any]) -> dict[str, list[str]]:
    """Return the declared alias table without inventing state-derived aliases."""
    characters = model.get("characters")
    if not isinstance(characters, dict):
        return {}
    result: dict[str, list[str]] = {}
    for character_id, config in characters.items():
        if not isinstance(character_id, str) or not isinstance(config, dict):
            continue
        forms = config.get("aliases")
        if isinstance(forms, list):
            result[character_id] = [form for form in forms if isinstance(form, str)]
    return result
