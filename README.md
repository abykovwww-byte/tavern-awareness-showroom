# Tavern Awareness Showroom

This repository is the standalone training product extracted from Tavern RP
Stack. It contains the Showroom browser client, a training-only Gateway target,
the `awareness` and `awareness-one-day` WorldPacks, and the deterministic
training artifact/workspace runtime.

The intended runtime is independent:

```text
Browser
  -> Showroom (static UI and same-origin /api proxy)
  -> Awareness Gateway
  -> its own SQLite database, state files, covers and provider credentials
```

It must not call the RP Gateway or share RP Stack database, state, cover,
authentication, visitor-cookie, or provider-secret storage. Sharing the
optional local model process is allowed only through the explicit external
`rp-llm` network overlay.

## Bootstrap status

The bootstrap establishes provenance, a standalone Compose contract, safe
shadow defaults, training-only process/resource guards, a closed two-WorldPack
catalog and a training-focused CI gate. Light GUI, RP WorldPacks and RP evals
are removed. Some inherited internal Gateway modules and their coupled tests
remain until dependency-backed pruning proves that Showroom does not use them;
they are not an active scenario mode or browser surface.

Source history is recorded in [PROVENANCE.md](PROVENANCE.md).

## Configuration

Copy `.env.example` to an untracked `.env`, set `AWARENESS_ENV_FILE=.env`,
replace the bootstrap password, and configure only the provider credentials
this deployment needs. Keep the three writable host paths separate:

- `AWARENESS_GATEWAY_DATA_DIR`: SQLite and Gateway-owned data;
- `AWARENESS_STATE_DIR`: canonical state files;
- `AWARENESS_SHOWROOM_COVER_DIR`: uploaded Showroom covers.

`AWARENESS_BACKUP_DIR` is an operator-side backup destination and is not mounted
into either container. Shadow defaults bind only `127.0.0.1:18011`. Port `8011`
is reserved for the separately approved C1 production cutover.

Published scenario settings live in `configs/showroom/scenarios.json`. On
Gateway start they are validated against the two training WorldPacks, mapped to
exact model tuples and idempotently created or updated. Removing a catalog row
does not delete runtime data; unpublish it explicitly in the catalog first.
Every row declares either a relative source cover or `null`, so an out-of-band
cover upload cannot make two deployments render different cards after restart.

## Validate and start a shadow

```bash
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build
docker compose --env-file .env ps
```

To reuse an already managed local model endpoint:

```bash
docker compose -f compose.yml -f compose.local-llm.yml --env-file .env config --quiet
docker compose -f compose.yml -f compose.local-llm.yml --env-file .env up -d --build
```

The overlay expects an existing external network named by `LOCAL_LLM_NETWORK`
(default `rp-llm`) and does not create or own the model runner.

## Focused source checks

Use the explicit training-only Gateway gate from `.github/workflows/ci.yml`;
the inherited full pytest suite is intentionally not the repository gate until
the coupled RP modules and tests are pruned. Browser checks remain
dependency-free:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci.ps1
```

The script installs nothing and does not start Docker. It uses an active or
bundled Python/Node runtime and expects Gateway test dependencies to be already
available. The equivalent browser-only checks are:

```bash
node --check rp-showcase-gui/app.js
node --check rp-showcase-gui/structured-content.js
node --check rp-showcase-gui/message-time.js
node --check rp-showcase-gui/training-only.test.js
node --check ui-shared/training-artifacts.js
node rp-showcase-gui/request-policy.test.js
node rp-showcase-gui/structured-content.test.js
node rp-showcase-gui/message-time.test.js
node rp-showcase-gui/training-only.test.js
node ui-shared/training-artifacts.test.js
```

See [docs/operations.md](docs/operations.md) for shadow acceptance, backups,
cutover and rollback. A successful source check or image build is not live
runtime proof.
