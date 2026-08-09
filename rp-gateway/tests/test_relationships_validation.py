from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest


RP_STACK_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = RP_STACK_ROOT / "scripts" / "validate-relationships.py"
MODEL_PATH = RP_STACK_ROOT / "worldpacks" / "mechanist-new-world" / "relationships" / "model.json"


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_relationships", VALIDATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_model() -> dict:
    return json.loads(MODEL_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            lambda model: model["character_weights"]["enri-emmot"].__setitem__("role", "unknown-role"),
            "references unknown role: unknown-role",
        ),
        (
            lambda model: model["events"]["insult_public"].__setitem__("wound", "unknown-wound"),
            "references unknown wound: unknown-wound",
        ),
        (
            lambda model: model["axes"]["loyalty"]["bands"][1].__setitem__("max", -80),
            "overlaps a previous band",
        ),
        (
            lambda model: model["events"]["defended_publicly"].__setitem__("weight", 16),
            "weight must be between -30 and 15",
        ),
    ],
    ids=["unknown-role", "unknown-wound", "overlapping-boundaries", "out-of-range-weight"],
)
def test_validate_model_rejects_frozen_contract_violations(mutate, expected: str) -> None:
    """Each case proves one named preflight guard rejects its mutation with a useful error."""
    validator = load_validator()
    model = copy.deepcopy(valid_model())
    mutate(model)

    errors = validator.validate_model(model, MODEL_PATH)

    assert any(expected in item for item in errors), errors


def test_checked_in_relationship_model_passes_preflight() -> None:
    """Proves the shipped first-slice model satisfies the same validator under test."""
    validator = load_validator()

    assert validator.validate_model(valid_model(), MODEL_PATH) == []
