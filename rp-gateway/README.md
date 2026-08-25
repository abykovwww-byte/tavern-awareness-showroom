# RP Gateway

FastAPI authority and provider gateway for RP Stack.

Light GUI proxies party-scoped `/api` requests to this service. Gateway owns
authentication, world-pack discovery, character drafts, model profiles,
canonical state, turn history, memory chapters, legacy check compatibility, and training
progression. The administrator-selected **service model / служебная модель** is
global to the stack and handles long-term memory, world changes, and character
generation. Party BYOK credentials remain isolated to their owning party.

For revision-8 RP parties Gateway also validates and copies authored WorldPack
Lore Cards at party creation without a provider call. The recent RAW scan selects
cards only through whole title/keyword triggers and stores final raised IDs in
turn metadata. `POST /api/parties/{party_id}/lore-cards/draft` makes one bounded
stack-key OpenRouter draft from complete turns; the existing create endpoint is
the explicit player-confirmation boundary.

Candidate revision-9 RP parties also have a separate GM correction channel.
Local Gemma classifies `auto` messages and drafts one bounded replacement or
retraction of an existing memory/RAW/absolute-rule target. Only explicit confirm
creates an excluded `gm_correction` turn and state version; party turn, scene and
time do not advance. Active corrections stay in a protected narrator overlay
until one affected OpenRouter story-memory section persists authority `user` and
the target coverage. Revision 9 is source-only until a separate activation.

Application startup uses FastAPI's `lifespan` context manager. Before accepting
requests, it reconciles interrupted party and branch work, resumes pending
service jobs, and schedules resumable autotest runs.

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
POST /api/parties/{party_id}/gm-corrections/decide
POST /api/parties/{party_id}/checks
```

`POST /checks` is a compatibility endpoint. For `rp-core.v2` it enters the
ordinary narrative turn path without dice, difficulty, success/failure, or a
persisted check row. Existing `rp-core.v1` parties retain the legacy resolver
until an explicit migration.

The OpenAI-compatible `/v1/chat/completions` endpoint remains available for
generic integrations, but Light GUI uses the party-scoped API.
