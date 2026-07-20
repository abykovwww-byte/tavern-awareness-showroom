# Testing

## Iteration 1 Acceptance Checks

- `docker compose up -d` starts SillyTavern.
- UI is reachable from LAN at `http://192.168.1.88:8000`.
- Basic Auth is enabled.
- NVIDIA API is configured as a custom OpenAI-compatible backend.
- Model `z-ai/glm-5.2` responds in Russian.
- A 10-turn Russian RP session is completed.
- Chat/settings survive `docker compose restart sillytavern`.
- No API key appears in Git or logs.

## Useful Commands

```bash
cd /srv/apps/rp-stack
docker compose ps
docker compose logs --tail=200 sillytavern
docker inspect --format='{{.State.Health.Status}}' rp-stack-sillytavern
```

## Iteration 2 Checks

```bash
cd /srv/apps/rp-stack
python3 scripts/test-state-workflow.py
python3 scripts/validate-state.py
python3 scripts/render-state-block.py >/tmp/rp-state-block.txt
```

Covered negative cases:

- player declares a dead NPC alive;
- player uses unavailable resource;
- relationship/state change without reason;
- narrative-only fact leaves state unchanged unless patch is confirmed;
- invalid JSON is rejected;
- rejected patch dry-run does not modify state;
- corrected patch can be confirmed;
- state persists after reload;
- rollback restores a previous state as a new version.

## Iteration 3 Checks

```bash
cd /srv/apps/rp-stack
python3 scripts/test-check-workflow.py
python3 scripts/validate-state.py
```

Covered negative cases:

- high difficulty fails plausibly;
- long player prose does not add a hidden bonus;
- unavailable resources are blocked;
- hard constraints override critical success;
- duplicate check patches cannot be applied twice;
- rollback restores resource values;
- check logs do not contain API-key-looking markers.

## Iteration 4 Checks

```bash
cd /srv/apps/rp-stack
docker compose build rp-gateway
docker compose run --rm rp-gateway pytest
docker compose ps
docker inspect --format='{{.State.Health.Status}}' rp-stack-gateway
docker inspect --format='{{.State.Health.Status}}' rp-stack-sillytavern
```

Covered cases:

- OpenAI-compatible chat response;
- stream response shape;
- intent schema validation;
- hard constraints and resource checks;
- transactional SQLite state versioning;
- rollback as a new version;
- idempotency key prevents duplicate turns;
- validator detects hidden outcome compensation;
- one repair attempt;
- safe fallback after failed repair;
- provider timeout and 429 handling;
- 30-turn mock campaign.

## Iteration 5 Checks

```bash
cd /srv/apps/rp-stack
docker compose run --rm rp-gateway pytest
```

Covered cases:

- natural-language `/world` command returns a readable preview;
- preview does not mutate canonical state;
- pending world proposals can be listed;
- `apply latest` applies the pending patch transactionally;
- `discard latest` removes a pending proposal from the apply queue;
- `/world show` returns compact state status;
- existing 30-turn mock campaign still passes.

## Iteration 6 Checks

```bash
cd /srv/apps/rp-stack
docker compose build rp-gateway rp-light-gui
docker compose run --rm rp-gateway pytest
docker compose ps
docker compose exec rp-light-gui wget -qO- http://127.0.0.1/health
```

Covered cases:

- party API scans installed world packs;
- player character draft can be approved into a saved character;
- party creation initializes isolated state from `state-seed.json`;
- `POST /api/parties/{party_id}/messages` records turn history;
- party-scoped GM preview is not visible in another party;
- Light GUI is reachable from LAN at `http://192.168.1.88:8010`;
- gateway legacy `/v1/chat/completions` remains available for SillyTavern.
