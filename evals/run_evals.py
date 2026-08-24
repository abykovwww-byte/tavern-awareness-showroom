#!/usr/bin/env python3
"""Offline, provider-canary, and browser-evidence evals for RP Stack."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from acceptance.evaluator import evaluate_files


RP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[5]
ACCEPTANCE_ROOT = Path(__file__).resolve().parent / "acceptance"
ACCEPTANCE_MANIFEST = ACCEPTANCE_ROOT / "manifest.yml"
ACCEPTANCE_SAVED_RESPONSES = ACCEPTANCE_ROOT / "fixtures" / "saved-responses-passing.json"
SECRET_RE = re.compile(
    r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+=*|\bsk-[A-Za-z0-9_-]{8,}\b|"
    r"\b(api[_-]?key|authorization|cookie|password|secret|token)\b(\s*[:=]\s*)([^\s,;]+)"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def redact(text: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        value = match.group(0)
        if value.lower().startswith("bearer "):
            return "Bearer [REDACTED]"
        if value.lower().startswith("sk-"):
            return "[REDACTED_API_KEY]"
        return f"{match.group(2)}{match.group(3)}[REDACTED]"

    return SECRET_RE.sub(replacement, text)[:12_000]


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolve_node() -> str | None:
    override = os.environ.get("CODEX_NODE")
    if override and Path(override).is_file():
        return override
    discovered = shutil.which("node")
    if discovered:
        return discovered
    bundled = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "node"
        / "bin"
        / "node.exe"
    )
    return str(bundled) if bundled.is_file() else None


def run_command(name: str, command: list[str], cwd: Path) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return {
        "name": name,
        "passed": completed.returncode == 0,
        "exit_code": completed.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "output_tail": redact(completed.stdout[-8_000:]),
    }


def offline_eval() -> dict[str, Any]:
    python = sys.executable
    commands: list[tuple[str, list[str], Path]] = []
    for state_seed in sorted((RP_ROOT / "worldpacks").rglob("state-seed.json")):
        commands.append(
            (
                f"state-schema:{state_seed.relative_to(RP_ROOT)}",
                [python, "scripts/validate-state.py", "--state", str(state_seed), "--schema", "state/schema.json"],
                RP_ROOT,
            )
        )
    commands.extend(
        [
            (
            "training-runtime",
            [python, "scripts/validate-training-runtime.py", "--worldpacks", "worldpacks"],
            RP_ROOT,
            ),
            ("state-workflow", [python, "scripts/test-state-workflow.py"], RP_ROOT),
            ("check-workflow", [python, "scripts/test-check-workflow.py"], RP_ROOT),
            ("gateway-pytest", [python, "-m", "pytest", "-q"], RP_ROOT / "rp-gateway"),
        ]
    )

    node = resolve_node()
    if node:
        for app_path in (RP_ROOT / "rp-light-gui" / "app.js", RP_ROOT / "rp-showcase-gui" / "app.js"):
            commands.append((f"syntax:{app_path.parent.name}", [node, "--check", str(app_path)], REPO_ROOT))
        for test_path in sorted(RP_ROOT.rglob("*.test.js")):
            commands.append((f"js:{test_path.relative_to(RP_ROOT)}", [node, str(test_path)], REPO_ROOT))
    else:
        commands.append(("node-runtime", [python, "-c", "raise SystemExit('Node.js runtime not found')"], REPO_ROOT))

    semantic_acceptance = evaluate_files(ACCEPTANCE_MANIFEST, ACCEPTANCE_SAVED_RESPONSES)
    checks = [
        {
            "name": "semantic-acceptance",
            "passed": semantic_acceptance["passed"],
            "report": semantic_acceptance,
        },
        *[run_command(name, command, cwd) for name, command, cwd in commands],
    ]
    return {
        "schema_version": "rp-stack.eval-report.v1",
        "mode": "offline",
        "started_at": utc_now(),
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


class ApiClient:
    def __init__(self, base_url: str) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("base URL must be an absolute http:// or https:// URL")
        self.base_url = base_url.rstrip("/")
        self.cookie = os.environ.get("RP_STACK_SESSION_COOKIE", "").strip()
        self.bearer = os.environ.get("RP_STACK_ADMIN_BEARER", "").strip()
        if not self.cookie and not self.bearer:
            raise ValueError("set RP_STACK_SESSION_COOKIE or RP_STACK_ADMIN_BEARER for the provider canary")

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, data=body, method=method)
        request.add_header("Accept", "application/json")
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if self.cookie:
            request.add_header("Cookie", self.cookie)
        else:
            request.add_header("Authorization", f"Bearer {self.bearer}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            error_body = redact(exc.read(1_000).decode("utf-8", errors="replace"))
            raise RuntimeError(f"HTTP {exc.code} for {path}: {error_body}") from exc


def provider_canary(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_provider_run:
        raise ValueError("provider canary requires --confirm-provider-run")
    if args.turn_count < 1 or args.turn_count > 5:
        raise ValueError("provider canary turn count must be between 1 and 5")
    if len(args.player_prompt.strip()) < 1 or len(args.player_prompt) > 12_000:
        raise ValueError("player prompt must contain between 1 and 12000 characters")

    semantic_runs = []
    for response_path in args.semantic_responses:
        source = json.loads(response_path.read_text(encoding="utf-8")).get("source", {})
        if source.get("producer") != "provider-canary":
            raise ValueError(f"{response_path}: source.producer must be provider-canary")
        semantic_runs.append(evaluate_files(ACCEPTANCE_MANIFEST, response_path))

    client = ApiClient(args.base_url)
    party_id = urllib.parse.quote(args.source_party_id, safe="")
    source_history_before = client.request("GET", f"/api/parties/{party_id}/history")
    source_state_before = client.request("GET", f"/api/parties/{party_id}/state")
    history_hash_before = canonical_hash(source_history_before)
    state_hash_before = canonical_hash(source_state_before)

    create_payload = {
        "source_party_id": args.source_party_id,
        "player_prompt": args.player_prompt,
        "turn_count": args.turn_count,
        "player_model_profile_id": args.player_model_profile_id,
    }
    if args.rp_contract_revision is not None:
        create_payload["rp_contract_revision"] = args.rp_contract_revision
    created = client.request(
        "POST",
        "/api/admin/autotests",
        create_payload,
    )
    run_id = created["run"]["id"]
    branch_id = created["branch"]["id"]
    effective_rp_contract_revision = created["branch"].get("rp_contract_revision")
    revision_matched = (
        args.rp_contract_revision is None
        or effective_rp_contract_revision == args.rp_contract_revision
    )
    deadline = time.monotonic() + args.timeout_seconds
    run: dict[str, Any] = created["run"]
    timed_out = False

    while run.get("status") not in {"completed", "failed", "stopped"}:
        if time.monotonic() >= deadline:
            timed_out = True
            try:
                client.request("POST", f"/api/admin/autotests/{urllib.parse.quote(run_id, safe='')}/stop", {})
            finally:
                break
        time.sleep(args.poll_seconds)
        listing = client.request("GET", f"/api/admin/autotests?source_party_id={party_id}")
        run = next((item for item in listing.get("runs", []) if item.get("id") == run_id), run)

    source_history_after = client.request("GET", f"/api/parties/{party_id}/history")
    source_state_after = client.request("GET", f"/api/parties/{party_id}/state")
    history_hash_after = canonical_hash(source_history_after)
    state_hash_after = canonical_hash(source_state_after)
    source_unchanged = history_hash_before == history_hash_after and state_hash_before == state_hash_after
    selected_run = {
        key: run.get(key)
        for key in (
            "id",
            "status",
            "source_party_id",
            "branch_id",
            "player_model_profile_id",
            "requested_turns",
            "completed_turns",
            "fallback_turns",
            "error",
            "created_at",
            "finished_at",
        )
    }
    passed = (
        not timed_out
        and run.get("status") == "completed"
        and run.get("completed_turns") == args.turn_count
        and int(run.get("fallback_turns") or 0) == 0
        and source_unchanged
        and revision_matched
        and all(report["passed"] for report in semantic_runs)
    )
    metric_spread = {
        name: {
            "min": min(report["metrics"][name]["value"] for report in semantic_runs),
            "max": max(report["metrics"][name]["value"] for report in semantic_runs),
        }
        for name in semantic_runs[0]["metrics"]
    }
    return {
        "schema_version": "rp-stack.eval-report.v1",
        "mode": "provider-canary",
        "checked_at": utc_now(),
        "passed": passed,
        "timed_out": timed_out,
        "source_unchanged": source_unchanged,
        "source_history_hash_before": history_hash_before,
        "source_history_hash_after": history_hash_after,
        "source_state_hash_before": state_hash_before,
        "source_state_hash_after": state_hash_after,
        "requested_rp_contract_revision": args.rp_contract_revision,
        "effective_rp_contract_revision": effective_rp_contract_revision,
        "rp_contract_revision_matched": revision_matched,
        "branch_id": branch_id,
        "run": selected_run,
        "semantic_acceptance": {
            "repeat_count": len(semantic_runs),
            "runs": semantic_runs,
            "metric_spread": metric_spread,
        },
    }


def browser_report(path: Path) -> dict[str, Any]:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "status",
        "checked_at",
        "deployed_revision",
        "base_url",
        "authenticated_party_list",
        "party_authority_verified",
        "exactly_once_turn_verified",
        "provider_evidence_verified",
        "training_artifacts_verified",
        "showroom_isolation_verified",
        "browser_error_count",
    }
    missing = sorted(required - evidence.keys())
    revision_valid = bool(re.fullmatch(r"[0-9a-f]{40}", str(evidence.get("deployed_revision", ""))))
    booleans_valid = all(
        evidence.get(field) is True
        for field in (
            "authenticated_party_list",
            "party_authority_verified",
            "exactly_once_turn_verified",
            "provider_evidence_verified",
            "training_artifacts_verified",
            "showroom_isolation_verified",
        )
    )
    passed = (
        not missing
        and evidence.get("status") == "passed"
        and revision_valid
        and booleans_valid
        and evidence.get("browser_error_count") == 0
    )
    return {
        "schema_version": "rp-stack.eval-report.v1",
        "mode": "browser-smoke",
        "checked_at": utc_now(),
        "passed": passed,
        "missing_fields": missing,
        "revision_valid": revision_valid,
        "evidence": evidence,
    }


def write_report(report: dict[str, Any], output: Path | None) -> Path:
    destination = output
    if destination is None:
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = REPO_ROOT / "artifacts" / "evals" / f"{report['mode']}-{timestamp}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    offline = subparsers.add_parser("offline")
    offline.add_argument("--output", type=Path)

    semantic = subparsers.add_parser("semantic-acceptance")
    semantic.add_argument("--manifest", type=Path, default=ACCEPTANCE_MANIFEST)
    semantic.add_argument("--saved-responses", type=Path, default=ACCEPTANCE_SAVED_RESPONSES)
    semantic.add_argument("--output", type=Path)

    canary = subparsers.add_parser("provider-canary")
    canary.add_argument("--base-url", required=True)
    canary.add_argument("--source-party-id", required=True)
    canary.add_argument("--player-model-profile-id", required=True)
    canary.add_argument("--player-prompt", required=True)
    canary.add_argument("--turn-count", type=int, default=1)
    canary.add_argument("--rp-contract-revision", type=int, choices=range(0, 9))
    canary.add_argument("--timeout-seconds", type=int, choices=range(30, 901), default=300)
    canary.add_argument("--poll-seconds", type=float, default=2.0)
    canary.add_argument("--confirm-provider-run", action="store_true")
    canary.add_argument("--semantic-responses", type=Path, action="append", required=True)
    canary.add_argument("--output", type=Path)

    browser = subparsers.add_parser("browser-report")
    browser.add_argument("--evidence-file", type=Path, required=True)
    browser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.mode == "offline":
            report = offline_eval()
        elif args.mode == "semantic-acceptance":
            report = evaluate_files(args.manifest, args.saved_responses)
        elif args.mode == "provider-canary":
            report = provider_canary(args)
        else:
            report = browser_report(args.evidence_file)
        destination = write_report(report, args.output)
        print(json.dumps({"passed": report["passed"], "report": str(destination)}, ensure_ascii=False))
        return 0 if report["passed"] else 1
    except Exception as exc:  # noqa: BLE001 - command-line boundary with redaction
        print(json.dumps({"passed": False, "error": redact(str(exc))}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
