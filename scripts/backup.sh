#!/usr/bin/env bash
set -euo pipefail

umask 077

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
APP_DIR="${AWARENESS_APP_DIR:-/srv/apps/awareness-showroom}"
DATA_ROOT="${AWARENESS_DATA_ROOT:-/srv/app-data/awareness-showroom}"
GATEWAY_DATA_DIR="${AWARENESS_GATEWAY_DATA_DIR:-${DATA_ROOT}/gateway}"
STATE_DIR="${AWARENESS_STATE_DIR:-${DATA_ROOT}/state}"
COVER_DIR="${AWARENESS_SHOWROOM_COVER_DIR:-${DATA_ROOT}/showroom-covers}"
BACKUP_DIR="${AWARENESS_BACKUP_DIR:-/srv/backups/awareness-showroom}"
TARGET="${BACKUP_DIR}/awareness-showroom-${STAMP}.tar.gz"
TARGET_TMP="${TARGET}.tmp"
SNAPSHOT_NAME=".awareness_gateway-${STAMP}.backup.db"
SNAPSHOT_HOST_PATH="${GATEWAY_DATA_DIR}/${SNAPSHOT_NAME}"

mkdir -p "${BACKUP_DIR}"
for required_dir in "${APP_DIR}" "${GATEWAY_DATA_DIR}" "${STATE_DIR}" "${COVER_DIR}"; do
    if [[ ! -d "${required_dir}" ]]; then
        echo "Required directory does not exist: ${required_dir}" >&2
        exit 1
    fi
done
if [[ -e "${TARGET}" ]]; then
    echo "Backup already exists: ${TARGET}" >&2
    exit 1
fi
SOURCE_REVISION="$(git -C "${APP_DIR}" rev-parse --verify HEAD)"
if [[ ! "${SOURCE_REVISION}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Cannot determine the deployed source revision in ${APP_DIR}" >&2
    exit 1
fi

STAGING_DIR="$(mktemp -d "${BACKUP_DIR}/.awareness-showroom-${STAMP}.XXXXXX")"
cleanup() {
    rm -f -- "${SNAPSHOT_HOST_PATH}" "${TARGET_TMP}"
    rm -rf -- "${STAGING_DIR}"
}
trap cleanup EXIT

cd "${APP_DIR}"
docker compose exec -T awareness-gateway python - "${SNAPSHOT_NAME}" <<'PY'
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

prefix = "sqlite:///"
database_url = os.environ.get("DATABASE_URL", "")
if not database_url.startswith(prefix):
    raise SystemExit("DATABASE_URL must use sqlite:/// for backup")

source_path = database_url[len(prefix):]
if source_path != "/data/awareness_gateway.db":
    raise SystemExit(f"Unexpected SQLite path: {source_path}")

snapshot_path = Path("/data") / sys.argv[1]
snapshot_path.unlink(missing_ok=True)
with closing(sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)) as source:
    with closing(sqlite3.connect(snapshot_path)) as destination:
        source.backup(destination)
        result = destination.execute("PRAGMA quick_check").fetchone()
        if result != ("ok",):
            raise SystemExit(f"SQLite quick_check failed: {result}")
PY

mkdir -p \
    "${STAGING_DIR}/data/gateway" \
    "${STAGING_DIR}/data/state" \
    "${STAGING_DIR}/data/showroom-covers"

tar \
    --exclude="./awareness_gateway.db" \
    --exclude="./awareness_gateway.db-shm" \
    --exclude="./awareness_gateway.db-wal" \
    --exclude="./showroom-covers" \
    --exclude="./${SNAPSHOT_NAME}" \
    -C "${GATEWAY_DATA_DIR}" -cf - . \
    | tar -C "${STAGING_DIR}/data/gateway" -xf -
mv "${SNAPSHOT_HOST_PATH}" "${STAGING_DIR}/data/gateway/awareness_gateway.db"
cp -a "${STATE_DIR}/." "${STAGING_DIR}/data/state/"
cp -a "${COVER_DIR}/." "${STAGING_DIR}/data/showroom-covers/"

printf 'created_at=%s\nproject=awareness-showroom\nsource_revision=%s\nsqlite_backup=consistent\n' \
    "${STAMP}" "${SOURCE_REVISION}" > "${STAGING_DIR}/manifest.txt"
tar -czf "${TARGET_TMP}" -C "${STAGING_DIR}" .
mv "${TARGET_TMP}" "${TARGET}"
chmod 600 "${TARGET}"
echo "${TARGET}"
