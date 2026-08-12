# RP Gateway

FastAPI authority and provider gateway for RP Stack.

Light GUI proxies party-scoped `/api` requests to this service. Gateway owns
authentication, world-pack discovery, character drafts, model profiles,
canonical state, turn history, memory chapters, legacy check compatibility, and training
progression. The administrator-selected **service model / служебная модель** is
global to the stack and handles long-term memory, world changes, and character
generation. Party BYOK credentials remain isolated to their owning party.

## Development

```bash
python -m pytest tests
uvicorn app.main:app --host 0.0.0.0 --port 8088
```

## Runtime endpoints

```text
GET  /health
GET  /api/worldpacks
GET  /api/model-profiles
GET  /api/parties
POST /api/parties
POST /api/parties/{party_id}/messages
POST /api/parties/{party_id}/checks
```

`POST /checks` is a compatibility endpoint. For `rp-core.v2` it enters the
ordinary narrative turn path without dice, difficulty, success/failure, or a
persisted check row. Existing `rp-core.v1` parties retain the legacy resolver
until an explicit migration.

The OpenAI-compatible `/v1/chat/completions` endpoint remains available for
generic integrations, but Light GUI uses the party-scoped API.
