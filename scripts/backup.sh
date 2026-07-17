#!/usr/bin/env bash
set -euo pipefail

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="/srv/backups/rp-stack"
SOURCE_DIR="/srv/app-data/rp-stack"
TARGET="${BACKUP_DIR}/rp-stack-${STAMP}.tar.gz"

mkdir -p "${BACKUP_DIR}"
tar -czf "${TARGET}" -C / srv/app-data/rp-stack
chmod 600 "${TARGET}"
echo "${TARGET}"

