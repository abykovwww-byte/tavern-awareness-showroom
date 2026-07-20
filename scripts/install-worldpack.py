#!/usr/bin/env python3
"""Install a world pack into the running RP stack.

This is intentionally server-side. The SillyTavern browser import dialog can
only see files on the player's browser host, so remote/server deployments must
copy lorebooks into SillyTavern's runtime data directory instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORLDS_DIR = Path("/srv/app-data/rp-stack/data/default-user/worlds")
DEFAULT_GATEWAY_DB = Path("/srv/app-data/rp-stack/gateway/rp_gateway.db")
DEFAULT_STATE_PATH = ROOT / "state" / "current.json"
DEFAULT_SCHEMA_PATH = ROOT / "state" / "schema.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def validate_state(seed_path: Path, schema_path: Path) -> None:
    command = [
        sys.executable,
        str(ROOT / "scripts" / "validate-state.py"),
        "--state",
        str(seed_path),
        "--schema",
        str(schema_path),
    ]
    result = subprocess.run(command, cwd=ROOT, text=True)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def find_lorebook(pack_dir: Path, explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_absolute():
            path = pack_dir / path
        if not path.exists():
            raise FileNotFoundError(f"Lorebook JSON not found: {path}")
        return path

    candidates = sorted((pack_dir / "sillytavern").glob("*.json"))
    if not candidates:
        raise FileNotFoundError(f"No SillyTavern lorebook JSON found under {pack_dir / 'sillytavern'}")
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ValueError(f"Multiple lorebook JSON files found ({names}); pass --lorebook")
    return candidates[0]


def backup_file(path: Path, backup_dir: Path, label: str, timestamp: str) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{label}-{timestamp}{path.suffix or '.bak'}"
    shutil.copy2(path, target)
    return target


def backup_sqlite(db_path: Path, timestamp: str) -> Path | None:
    if not db_path.exists():
        return None
    target = db_path.with_name(f"{db_path.name}.pre-worldpack-{timestamp}.bak")
    source = sqlite3.connect(str(db_path))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    return target


def install_lorebook(lorebook_path: Path, worlds_dir: Path, overwrite: bool, dry_run: bool) -> Path:
    load_json(lorebook_path)
    target = worlds_dir / lorebook_path.name
    if target.exists() and not overwrite:
        print(f"LOREBOOK_EXISTS: {target} (kept; pass --overwrite-lorebook to replace)")
        return target
    print(f"LOREBOOK_INSTALL: {lorebook_path} -> {target}")
    if not dry_run:
        worlds_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lorebook_path, target)
    return target


def install_state(
    seed_path: Path,
    state_path: Path,
    db_path: Path,
    campaign_id: str,
    reason: str,
    dry_run: bool,
) -> None:
    state = load_json(seed_path)
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    iso_timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    print(f"STATE_FILE_INSTALL: {seed_path} -> {state_path}")
    if db_path.exists():
        print(f"STATE_DB_INSTALL: {db_path} campaign={campaign_id!r}")
    else:
        print(f"STATE_DB_MISSING: {db_path} (gateway will bootstrap from state file on first DB creation)")

    if dry_run:
        return

    backup_file(state_path, state_path.parent / "history", "pre-worldpack-current", timestamp)
    backup_sqlite(db_path, timestamp)

    if db_path.exists():
        connection = sqlite3.connect(str(db_path))
        try:
            connection.execute(
                "INSERT OR IGNORE INTO campaigns(id, created_at) VALUES(?, ?)",
                (campaign_id, int(time.time())),
            )
            row = connection.execute(
                "SELECT MAX(version) FROM state_versions WHERE campaign_id = ?",
                (campaign_id,),
            ).fetchone()
            next_version = int(row[0] or 0) + 1
            state.setdefault("meta", {})
            state["meta"]["state_version"] = next_version
            state["meta"]["last_updated"] = iso_timestamp
            state.setdefault("last_turn", {})
            state["last_turn"]["turn"] = int(state.get("meta", {}).get("turn", 0))
            state["last_turn"]["state_patch_id"] = f"worldpack:{seed_path.parent.name}"
            connection.execute(
                """
                INSERT INTO state_versions(campaign_id, version, state_json, created_at, reason)
                VALUES(?, ?, ?, ?, ?)
                """,
                (campaign_id, next_version, json.dumps(state, ensure_ascii=False), int(time.time()), reason),
            )
            connection.commit()
        finally:
            connection.close()

    write_json(state_path, state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug", help="World pack slug under worldpacks/.")
    parser.add_argument("--confirm", action="store_true", help="Actually install files. Without this, only preview.")
    parser.add_argument("--skip-state", action="store_true", help="Install only the SillyTavern lorebook.")
    parser.add_argument("--skip-lorebook", action="store_true", help="Install only the gateway state.")
    parser.add_argument("--overwrite-lorebook", action="store_true", help="Replace an existing SillyTavern lorebook file.")
    parser.add_argument("--campaign-id", default="default", help="Gateway SQLite campaign id. Defaults to the stack value.")
    parser.add_argument("--reason", default="worldpack_install", help="Reason stored in gateway state history.")
    parser.add_argument("--worlds-dir", type=Path, default=DEFAULT_WORLDS_DIR)
    parser.add_argument("--gateway-db", type=Path, default=DEFAULT_GATEWAY_DB)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH)
    parser.add_argument("--lorebook", help="Lorebook path relative to the pack dir, or absolute.")
    args = parser.parse_args()

    pack_dir = ROOT / "worldpacks" / args.slug
    if not pack_dir.exists():
        raise FileNotFoundError(f"World pack not found: {pack_dir}")

    seed_path = pack_dir / "state-seed.json"
    if not seed_path.exists():
        raise FileNotFoundError(f"State seed not found: {seed_path}")

    dry_run = not args.confirm
    print("DRY_RUN" if dry_run else "CONFIRMED_INSTALL")

    if not args.skip_state:
        validate_state(seed_path, args.schema)
        install_state(seed_path, args.state, args.gateway_db, args.campaign_id, args.reason, dry_run)

    if not args.skip_lorebook:
        lorebook_path = find_lorebook(pack_dir, args.lorebook)
        install_lorebook(lorebook_path, args.worlds_dir, args.overwrite_lorebook, dry_run)

    if dry_run:
        print("No changes written. Re-run with --confirm to install.")
    else:
        print("Installed. Reload SillyTavern and refresh/select the lorebook in World Info.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
