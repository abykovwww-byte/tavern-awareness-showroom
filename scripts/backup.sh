#!/usr/bin/env bash
set -euo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/srv/backups/rp-stack"
TARGET="${BACKUP_DIR}/rp-stack-${STAMP}.tar.gz"

mkdir -p "${BACKUP_DIR}"
tar -czf "${TARGET}" -C / srv/app-data/rp-stack srv/apps/rp-stack/state
chmod 600 "${TARGET}"
echo "${TARGET}"
