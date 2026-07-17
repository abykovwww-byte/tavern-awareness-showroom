# Testing

## Iteration 1 Acceptance Checks

- `docker compose up -d` starts SillyTavern.
- UI is reachable from LAN at `http://192.168.1.88:8000`.
- Basic Auth is enabled.
- NVIDIA API is configured as a custom OpenAI-compatible backend.
- Model `z-ai/glm-5.2` responds in Russian.
- A 10-turn Russian RP session is completed.
- Chat/settings survive `docker compose restart sillytavern`.
- No API key appears in Git or logs.

## Useful Commands

```bash
cd /srv/apps/rp-stack
docker compose ps
docker compose logs --tail=200 sillytavern
docker inspect --format='{{.State.Health.Status}}' rp-stack-sillytavern
```

