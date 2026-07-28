#!/usr/bin/env python3
"""Render the current state as an authoritative prompt block."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", default="state/current.json")
    args = parser.parse_args()

    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    print("<AUTHORITATIVE_WORLD_STATE>")
    print(json.dumps(state, ensure_ascii=False, indent=2))
    print("</AUTHORITATIVE_WORLD_STATE>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
