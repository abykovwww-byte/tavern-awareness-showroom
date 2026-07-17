# Iteration 4 Test Report

## Scope

FastAPI RP Gateway between SillyTavern and the NVIDIA OpenAI-compatible API.

## Automated Coverage

`rp-gateway/tests/test_gateway.py` covers:

- health and state endpoints;
- invalid JSON intent parser output;
- successful turn and transactional state update;
- failed persuasion with hard constraint;
- model hidden-compensation violation;
- single repair attempt;
- failed repair fallback;
- provider timeout;
- provider 429/rate-limit;
- idempotent repeated request;
- patch preview/apply;
- rollback;
- streaming response shape;
- 30-turn campaign with several NPC interactions, limited resource use, at least
  three failures, one rollback, player-claimed desired outcome, and hidden
  compensation validation.

## Server Commands

```bash
cd /srv/apps/rp-stack
docker compose build rp-gateway
docker compose run --rm rp-gateway pytest
docker compose up -d
docker compose ps
docker inspect --format='{{.State.Health.Status}}' rp-stack-gateway
docker inspect --format='{{.State.Health.Status}}' rp-stack-sillytavern
```

## Manual SillyTavern Check

Set SillyTavern:

```text
API: Chat Completion
Source: Custom OpenAI-compatible
Base URL: http://rp-gateway:8088/v1
Model: z-ai/glm-5.2
API key: NVIDIA key
```

Then send:

```text
/check persuasion target=advisor skill=2 difficulty=10 goal="secure a private meeting"
```
