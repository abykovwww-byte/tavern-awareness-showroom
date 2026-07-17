#!/usr/bin/env python3
"""Clear the last generated check before the next narrated turn."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def append_jsonl(path: Path, event: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--last-check", default="state/last-check.json")
    parser.add_argument("--checks-log", default="state/checks.log")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    last_check = Path(args.last_check)
    if not last_check.exists():
        print("No last check file exists.")
        return 0

    data = json.loads(last_check.read_text(encoding="utf-8"))
    check_id = data.get("check_id", "unknown")
    if not args.confirm:
        print(json.dumps({"would_clear_check_id": check_id, "last_check": str(last_check)}, indent=2))
        print("DRY RUN: add --confirm to clear the last generated check.")
        return 0

    last_check.unlink()
    append_jsonl(
        Path(args.checks_log),
        {
            "timestamp": now(),
            "event": "rollback_last_check",
            "check_id": check_id,
            "note": "Last generated outcome cleared before narration.",
        },
    )
    print(f"Cleared last check: {check_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
