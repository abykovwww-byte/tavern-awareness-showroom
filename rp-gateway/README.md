# RP Gateway

FastAPI arbitration gateway for the SillyTavern RP stack.

SillyTavern connects to this service as an OpenAI-compatible endpoint:

```text
Base URL: http://rp-gateway:8088/v1
Model: z-ai/glm-5.2
API key: your NVIDIA API key in the SillyTavern UI
```

The gateway accepts the Authorization header from SillyTavern and forwards it to
`https://integrate.api.nvidia.com/v1/chat/completions`. The key is not stored in
Git and does not need to be written to the server `.env`.

## Endpoints

```text
GET  /health
GET  /api/state
GET  /api/state/history
POST /api/state/patch/preview
POST /api/state/patch/apply
POST /api/turn/rollback
POST /v1/chat/completions
```

## Explicit Check Syntax

The MVP parser understands explicit commands embedded in the latest user
message:

```text
/check persuasion target=king skill=2 difficulty=14 goal="secure a private meeting"
/check resource resource=coin amount=1 difficulty=8 goal="bribe the guard"
```

Free-form text is treated as a low-confidence `feasibility` check. Claimed facts
remain unconfirmed until state is updated by the gateway.

## Tests

```bash
pytest
```
