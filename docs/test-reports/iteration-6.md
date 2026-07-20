# Iteration 6 - Light GUI MVP

Date: 2026-07-20

## Scope

Adds a LAN-only `rp-light-gui` service and party-scoped gateway API.

## Implemented

- `PartyStore` SQLite registry for world packs, player characters, model profiles and parties.
- World pack scan from `WORLD_PACKS_PATH`.
- Per-party state files under `PARTY_STATE_ROOT`.
- Party-scoped endpoints for state, history, messages, checks, world preview/apply/discard and rollback.
- Static Light GUI with party list, new party wizard, chat, state summary and GM controls.
- Docker Compose service `rp-light-gui` bound to the LAN host on port `8010`.
- Gateway keeps legacy `/v1/chat/completions` for SillyTavern.

## Expected Runtime Commands

```bash
cd /srv/apps/rp-stack
docker compose build rp-gateway rp-light-gui
docker compose run --rm rp-gateway pytest
docker compose up -d
docker compose ps
docker compose exec rp-light-gui wget -qO- http://127.0.0.1/health
```

## Manual Browser Checks

- Open `http://192.168.1.88:8010`.
- Confirm gateway indicator is green.
- Create a party from an installed world pack.
- Send a normal chat message or `/check` command.
- Use GM Preview, then Apply or Discard.
- Confirm SillyTavern remains available at `http://192.168.1.88:8000`.

## Known Limits

- Character draft is deterministic in the MVP; LLM-assisted character creation is a later enhancement.
- Party creation does not yet apply optional character starting-state patches.
- The browser does not receive raw provider API keys. Real model calls require gateway-side `NVIDIA_API_KEY` or an approved Authorization-bearing client flow.
