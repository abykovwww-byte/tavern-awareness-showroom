from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest


RP_STACK_ROOT = Path(__file__).resolve().parents[2]
EVALS_ROOT = RP_STACK_ROOT / "evals"
if str(EVALS_ROOT) not in sys.path:
    sys.path.insert(0, str(EVALS_ROOT))

from acceptance.evaluator import AcceptanceError, evaluate_files  # noqa: E402


MANIFEST = EVALS_ROOT / "acceptance" / "manifest.yml"
CORPUS = EVALS_ROOT / "acceptance" / "corpus" / "starosta-bootstrap.yml"
PASSING_RESPONSES = EVALS_ROOT / "acceptance" / "fixtures" / "saved-responses-passing.json"


def _oracle_copy(tmp_path: Path) -> tuple[Path, Path, Path]:
    acceptance = tmp_path / "acceptance"
    corpus_dir = acceptance / "corpus"
    fixtures_dir = acceptance / "fixtures"
    corpus_dir.mkdir(parents=True)
    fixtures_dir.mkdir()
    manifest = shutil.copy2(MANIFEST, acceptance / "manifest.yml")
    corpus = shutil.copy2(CORPUS, corpus_dir / CORPUS.name)
    responses = shutil.copy2(PASSING_RESPONSES, fixtures_dir / PASSING_RESPONSES.name)
    return Path(manifest), Path(corpus), Path(responses)


def test_saved_response_fixture_passes_with_manifest_thresholds() -> None:
    report = evaluate_files(MANIFEST, PASSING_RESPONSES)

    assert report["passed"] is True
    assert report["oracle"] == {
        "manifest_version": 1,
        "corpus_version": 2,
        "labeled_by": "user",
        "example_count": 36,
        "corpus_hash": "sha256:1a534b3c00d8c41649e3fd5fc8bdd34b931aecbaf63d7338d49e61edaa51d67f",
    }
    assert report["metrics"]["event_precision"]["threshold"] == 0.85
    assert report["metrics"]["event_recall"]["threshold"] == 0.65
    assert report["metrics"]["character_id_accuracy"]["threshold"] == 0.90
    assert report["metrics"]["empty_scene_false_positive_rate"]["threshold"] == 0.05
    assert report["metrics"]["positive_trust_recall"]["threshold"] == 0.70
    assert report["metrics"]["correction_retention"]["threshold"] == 0.90


def test_corrupt_human_label_is_rejected_before_scoring(tmp_path: Path) -> None:
    manifest, corpus, responses = _oracle_copy(tmp_path)
    corpus.write_text(
        corpus.read_text(encoding="utf-8").replace("scene_type: empty", "scene_type: broken", 1),
        encoding="utf-8",
    )

    with pytest.raises(AcceptanceError, match="invalid scene_type"):
        evaluate_files(manifest, responses)


def test_all_empty_events_fail_recall(tmp_path: Path) -> None:
    manifest, _corpus, responses = _oracle_copy(tmp_path)
    saved = json.loads(responses.read_text(encoding="utf-8"))
    for response in saved["responses"]:
        response["events"] = []
        response["badges"] = []
    responses.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

    report = evaluate_files(manifest, responses)

    assert report["passed"] is False
    assert report["metrics"]["event_recall"]["value"] == 0.0
    assert report["metrics"]["event_recall"]["passed"] is False
    assert report["metrics"]["positive_trust_recall"]["passed"] is False


def test_report_contains_separate_per_event_class_metrics() -> None:
    report = evaluate_files(MANIFEST, PASSING_RESPONSES)

    assert {"trust_shown", "trust_gained", "physical_assault", "kept_promise"} <= set(
        report["per_event_class"]
    )
    for event_class in report["per_event_class"].values():
        assert set(event_class["metrics"]) == {
            "event_precision",
            "event_recall",
            "character_id_accuracy",
        }
        assert event_class["passed"] is True


def test_tautology_detector_flags_generated_expectations(tmp_path: Path) -> None:
    manifest, _corpus, responses = _oracle_copy(tmp_path)
    saved = json.loads(responses.read_text(encoding="utf-8"))
    saved["source"]["expectations_generated"] = True
    saved["responses"][0]["expected_events"] = []
    responses.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")

    report = evaluate_files(manifest, responses)

    independence = report["source_independence"]
    assert report["passed"] is False
    assert independence["status"] == "generated-expectations-detected"
    assert independence["generated_expectations_detected"] is True
    assert any("expected_events" in finding for finding in independence["findings"])
