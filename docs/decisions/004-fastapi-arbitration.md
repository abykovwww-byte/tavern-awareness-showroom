# 004: FastAPI Arbitration Gateway

## Status

Accepted for iteration 4.

## Context

Iteration 3 fixed outcomes with a helper next to SillyTavern. The next step is
to put the arbiter directly between SillyTavern and the OpenAI-compatible
NVIDIA endpoint.

References checked:

- https://fastapi.tiangolo.com/deployment/docker/
- https://fastapi.tiangolo.com/tutorial/testing/
- https://fastapi.tiangolo.com/tutorial/sql-databases/
- https://platform.openai.com/docs/api-reference/chat/create
- https://docs.api.nvidia.com/nim/reference/llm-apis
- https://www.python-httpx.org/advanced/timeouts/

## Decision

Add `rp-gateway`, a FastAPI service in the same Docker Compose project.

SillyTavern connects to:

```text
http://rp-gateway:8088/v1
```

The NVIDIA API key remains entered in SillyTavern's API key field. SillyTavern
sends it as an Authorization header, and the gateway forwards that header to
NVIDIA. The key is not committed to Git and does not need to be stored in the
server `.env`.

The gateway uses FastAPI, Pydantic, httpx, SQLite through Python `sqlite3`, and a
persistent `/data/rp_gateway.db` volume.

## API

```text
GET  /health
GET  /api/state
GET  /api/state/history
POST /api/state/patch/preview
POST /api/state/patch/apply
POST /api/turn/rollback
POST /v1/chat/completions
```

## Pipeline

1. Read the latest user message.
2. Parse explicit `/check ...` commands, or use low-confidence `feasibility` for
   free text.
3. Load authoritative state.
4. Resolve the rule outcome before calling GLM.
5. Write a transactional SQLite state version and mirror `/state/current.json`.
6. Build the narrator prompt with state summary and `<AUTHORITATIVE_OUTCOME>`.
7. Call NVIDIA GLM-5.2.
8. Validate the narrative.
9. Run at most one repair attempt.
10. Return an OpenAI-compatible response.
11. Record audit metadata without secrets.

## Consequences

- SillyTavern can now route normal chat through the arbiter.
- GLM narrates but does not decide check outcomes.
- SQLite state history supports rollback without destroying history.
- The MVP parser is intentionally conservative; broad natural-language intent
  parsing remains future work.
