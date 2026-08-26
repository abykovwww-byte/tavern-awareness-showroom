from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest


RP_STACK_ROOT = Path(__file__).resolve().parents[2]
EVALS_ROOT = RP_STACK_ROOT / "evals"
RUNNER_PATH = EVALS_ROOT / "run_evals.py"


def load_runner(tmp_path: Path) -> Any:
    module_path = tmp_path / "repo" / "roles" / "apps" / "files" / "rp-stack" / "evals" / "run_evals.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_bytes(RUNNER_PATH.read_bytes())
    spec = importlib.util.spec_from_file_location("rp_stack_run_evals", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(EVALS_ROOT))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(EVALS_ROOT))
    return module


class FakeApiClient:
    def __init__(self, effective_revision: int, fallback_turns: int = 0) -> None:
        self.effective_revision = effective_revision
        self.fallback_turns = fallback_turns
        self.create_payload: dict[str, Any] | None = None

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        if method == "GET" and path.endswith("/history"):
            return {"turns": []}
        if method == "GET" and path.endswith("/state"):
            return {"state": {"meta": {"turn": 0}}}
        if method == "POST" and path == "/api/admin/autotests":
            self.create_payload = payload
            return {
                "branch": {"id": "branch-canary", "rp_contract_revision": self.effective_revision},
                "run": {
                    "id": "run-canary",
                    "status": "completed",
                    "completed_turns": 1,
                    "fallback_turns": self.fallback_turns,
                },
            }
        raise AssertionError(f"unexpected request: {method} {path}")


def canary_args(response_path: Path, revision: int | None) -> argparse.Namespace:
    return argparse.Namespace(
        base_url="http://example.test",
        source_party_id="party-source",
        player_model_profile_id="player-model",
        player_prompt="Take the next in-world action only.",
        turn_count=1,
        rp_contract_revision=revision,
        timeout_seconds=30,
        poll_seconds=0,
        confirm_provider_run=True,
        semantic_responses=[response_path],
    )


@pytest.mark.parametrize(
    (
        "requested_revision",
        "effective_revision",
        "fallback_turns",
        "expected_pass",
        "field_present",
    ),
    [
        (8, 8, 0, True, True),
        (7, 7, 0, True, True),
        (7, 6, 0, False, True),
        (7, 7, 1, False, True),
        (0, 0, 0, True, True),
        (None, 6, 0, True, False),
    ],
)
def test_provider_canary_passes_and_verifies_candidate_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested_revision: int | None,
    effective_revision: int,
    fallback_turns: int,
    expected_pass: bool,
    field_present: bool,
) -> None:
    runner = load_runner(tmp_path)
    response_path = tmp_path / "provider-response.json"
    response_path.write_text(
        json.dumps({"source": {"producer": "provider-canary"}}),
        encoding="utf-8",
    )
    fake_client = FakeApiClient(effective_revision, fallback_turns)
    monkeypatch.setattr(runner, "ApiClient", lambda _base_url: fake_client)
    monkeypatch.setattr(
        runner,
        "evaluate_files",
        lambda _manifest, _response: {"passed": True, "metrics": {"continuity": {"value": 1.0}}},
    )

    report = runner.provider_canary(canary_args(response_path, requested_revision))

    assert report["passed"] is expected_pass
    assert report["requested_rp_contract_revision"] == requested_revision
    assert report["effective_rp_contract_revision"] == effective_revision
    assert report["rp_contract_revision_matched"] is (
        requested_revision is None or effective_revision == requested_revision
    )
    assert fake_client.create_payload is not None
    assert ("rp_contract_revision" in fake_client.create_payload) is field_present
    if field_present:
        assert fake_client.create_payload["rp_contract_revision"] == requested_revision


def test_provider_canary_cli_accepts_revision_eleven_and_rejects_twelve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = load_runner(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_evals.py",
            "provider-canary",
            "--base-url",
            "http://example.test",
            "--source-party-id",
            "party-source",
            "--player-model-profile-id",
            "player-model",
            "--player-prompt",
            "continue",
            "--rp-contract-revision",
            "11",
            "--semantic-responses",
            "response.json",
        ],
    )

    assert runner.parse_args().rp_contract_revision == 11
    sys.argv[11] = "12"
    with pytest.raises(SystemExit):
        runner.parse_args()
