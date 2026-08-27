# Awareness Gateway

FastAPI authority for the standalone Awareness Showroom. The Showroom proxies
same-origin `/api` requests to this service. Gateway owns authentication,
visitor identity, the two training WorldPacks, model profiles, canonical state,
turn history, deterministic training progression, scoring, artifacts and
workspace events.

The process starts only with `SCENARIO_TYPE=training`. RP, novel, generated
prompt worlds, Light GUI routes and `/v1/chat/completions` are not registered.
Persisted foreign-mode parties, runs, branches, autotests and covers are hidden
before state recovery, provider calls or new writes.

Some inherited internal turn-pipeline modules remain because Showroom training
runs reuse them. They are not public API authority; remove them only together
with their proven consumers and tests.

## Development

Run the repository training gate from the project root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci.ps1
```

For a prepared Python environment, a local process can be started with:

```bash
uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8088
```

## Active endpoint groups

```text
GET/POST /api/auth/*
GET      /api/worldpacks
GET      /api/model-profiles
GET/POST /api/showroom/*
GET/POST /api/admin/showroom/*
GET/POST /api/admin/autotests/*
GET      /health
```

The public browser contract is `/api/showroom/*`; internal `party_id` values
are not exposed to visitors.
