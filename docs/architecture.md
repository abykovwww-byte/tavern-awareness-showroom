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

For deterministic training, Gateway is a domain-neutral interpreter. A
WorldPack `training_runtime` owns its program, assessment and fallbacks;
Gateway validates and snapshots that combined contract per party, evaluates
generic detectors/effects, supplies only the active sanitized turn to the
narrator and applies canonical state changes. Subject-specific schedules,
regexes, scoring weights and answer keys do not belong in Gateway.

Interactive sites and the department workspace are separate optional
capabilities. Their Showroom run flags are immutable and independent from the
training runtime contract. UI events are typed sub-turn evidence and do not
call an LLM or advance the authored schedule.

The canonical term **service model / служебная модель** means the one global
LLM selected by an administrator for long-term memory, world-state changes,
and character generation across all current and future parties. It is not a
narrator model. BYOK credentials are user-owned and party-scoped; service-model
requests use only stack-managed credentials.

World-pack prompts and `world-info/index.md` provide immutable context.
`state-seed.json` initializes each party. Canonical state and
`AUTHORITATIVE_OUTCOME` override prose memory.

See [Decision 017](decisions/017-worldpack-owned-training-runtime.md) for the
runtime contracts and prompt/scoring boundaries.

## Deployment

Ansible copies the committed source tree to `/srv/apps/rp-stack`, renders the
Compose and environment files, and runs Compose with orphan cleanup. Mutable
Gateway data remains under `/srv/app-data/rp-stack/gateway`.
