# Testing

## Static validation

```bash
python3 scripts/validate-state.py
python3 scripts/test-state-workflow.py
python3 scripts/test-check-workflow.py
```

Validate every world-pack JSON file and each `state-seed.json` against
`state/schema.json`.

## Gateway suite

```bash
cd /srv/apps/rp-stack
docker compose build rp-gateway rp-light-gui
docker compose run --rm rp-gateway pytest
```

The suite covers world-pack discovery, party isolation, state versioning,
deterministic checks, training progression, memory chapters, provider errors,
authentication, and Light GUI APIs.

## Runtime acceptance

```bash
docker compose ps
docker inspect --format='{{.State.Health.Status}}' rp-stack-gateway
docker inspect --format='{{.State.Health.Status}}' rp-stack-light-gui
curl -fsS http://192.168.1.88:8010/health
curl -fsS http://192.168.1.88:8010/api/worldpacks
```

Confirm that only the expected services exist in the Compose project and that
party state, history, and memory remain isolated across two test parties.
