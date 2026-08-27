#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${AWARENESS_APP_DIR:-/srv/apps/awareness-showroom}"
SHOWROOM_URL="${AWARENESS_SHOWROOM_CHECK_URL:-}"

cd "${APP_DIR}"
docker compose ps

for service in awareness-gateway showroom; do
    container_id="$(docker compose ps -q "${service}")"
    if [[ -z "${container_id}" ]]; then
        echo "Service is not running: ${service}" >&2
        exit 1
    fi
    health="$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "${container_id}")"
    if [[ "${health}" != "healthy" ]]; then
        echo "Service is not healthy: ${service} (${health})" >&2
        exit 1
    fi
done

if [[ -z "${SHOWROOM_URL}" ]]; then
    published_address="$(docker compose port showroom 80 | head -n 1)"
    if [[ -z "${published_address}" ]]; then
        echo "Showroom has no published port" >&2
        exit 1
    fi
    published_port="${published_address##*:}"
    published_host="${published_address%:${published_port}}"
    if [[ "${published_host}" == "0.0.0.0" ]]; then
        published_host="127.0.0.1"
    elif [[ "${published_host}" == "::" || "${published_host}" == "[::]" ]]; then
        published_host="[::1]"
    fi
    SHOWROOM_URL="http://${published_host}:${published_port}/health"
fi

python3 - "${SHOWROOM_URL}" <<'PY'
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url, timeout=5) as response:
    if response.status != 200:
        raise SystemExit(f"Unexpected status from {url}: {response.status}")
PY

echo "Awareness Showroom is healthy at ${SHOWROOM_URL}"
