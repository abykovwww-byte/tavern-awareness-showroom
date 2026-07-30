# RP Stack Architecture

## Runtime flow

```text
Browser
  -> rp-light-gui (static UI and /api reverse proxy)
  -> rp-gateway (FastAPI authority)
  -> provider APIs or rp-local-llm
```

Light GUI depends only on Gateway. Gateway owns the active binding between a
world pack, player character, model profile, canonical state, turn history, and
memory chapters. All persistent party data is scoped by party and stored in
SQLite plus isolated state files.

The canonical term **service model / служебная модель** means the one global
LLM selected by an administrator for long-term memory, world-state changes,
and character generation across all current and future parties. It is not a
narrator model. BYOK credentials are user-owned and party-scoped; service-model
requests use only stack-managed credentials.

World-pack prompts and `world-info/index.md` provide immutable context.
`state-seed.json` initializes each party. Canonical state and
`AUTHORITATIVE_OUTCOME` override prose memory.

## Deployment

Ansible copies the committed source tree to `/srv/apps/rp-stack`, renders the
Compose and environment files, and runs Compose with orphan cleanup. Mutable
Gateway data remains under `/srv/app-data/rp-stack/gateway`.
