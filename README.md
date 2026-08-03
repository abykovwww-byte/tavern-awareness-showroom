# RP Stack

RP Stack is the LAN-only roleplay and training application managed by this IaC
repository.

```text
Browser
  -> Light GUI on http://192.168.1.88:8010
  -> party-scoped /api proxy
  -> RP Gateway
  -> provider APIs or the optional local model runner
```

Gateway owns authentication, world packs, player characters, model profiles,
party state, history, memory chapters, deterministic checks, and training
runtime execution. Training subject logic, schedule, assessment and fallback
belong to the versioned WorldPack contract; Gateway interprets and snapshots it
without campaign-specific branches. The browser stores only its active session
and party preference.

**Service model / Служебная модель** is the single administrator-selected LLM
for the whole RP Stack. It serves long-term memory, world-state change drafts,
and character generation for every current and future party. Party narrator
models remain independent. User BYOK credentials are scoped to exactly one
party and are never used by the service model.

## Runtime paths

```text
/srv/apps/rp-stack
/srv/apps/rp-stack/worldpacks
/srv/apps/rp-stack/state
/srv/app-data/rp-stack/gateway/rp_gateway.db
/srv/backups/rp-stack
```

## Operations

```bash
cd /srv/apps/rp-stack
docker compose up -d --build --remove-orphans
docker compose ps
docker compose logs --tail=100 rp-gateway rp-light-gui
docker compose run --rm rp-gateway pytest
bash scripts/backup.sh
```

Provider keys are configured only in `/etc/ansible/local-overrides.yml` on the
server. Never store them in Git or enter them in the browser.

## Deployment

Commit and push IaC changes, then apply the server checkout through:

```bash
sudo systemctl start ansible-local-apply.service
```

Do not maintain long-lived manual edits under `/srv/apps/rp-stack`.
