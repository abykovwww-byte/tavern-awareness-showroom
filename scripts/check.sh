#!/usr/bin/env bash
set -euo pipefail

cd /srv/apps/rp-stack
docker compose ps
docker inspect --format='{{.State.Health.Status}}' rp-stack-gateway
