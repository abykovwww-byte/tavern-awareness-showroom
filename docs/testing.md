# Testing

## Source gate

On Windows, run the dependency-free wrapper from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\ci.ps1
```

It enforces exactly two WorldPacks, validates both state seeds and deterministic
training contracts, compiles Gateway sources, runs the explicit training-only
pytest list (including the public Showroom provider/start/turn/resume flow),
checks browser JavaScript syntax and executes the Showroom tests.

## Container gate

With an untracked `.env` and Docker available:

```bash
docker compose --env-file .env config --quiet
docker compose -f compose.yml -f compose.local-llm.yml --env-file .env config --quiet
docker compose --env-file .env build awareness-gateway showroom
docker compose --env-file .env run --rm awareness-gateway sh -lc \
  'pytest -q tests/test_awareness_one_day.py tests/test_training_runtime.py tests/test_training_artifacts.py tests/test_training_capabilities.py tests/test_showroom_portal.py tests/test_decision_019_contracts.py tests/test_training_gateway_mode_guard.py tests/test_training_showroom_mode_guard.py tests/test_training_gateway_flow.py tests/test_service_model_client.py'
```

## Shadow acceptance

After the authorized IaC apply:

```bash
sudo /srv/apps/awareness-showroom/scripts/check.sh
```

Then complete the browser/provider, persistence, scoring, artifact/workspace,
backup and restore checks in `docs/operations.md`. Healthy containers and HTTP
200 alone are not functional proof.
