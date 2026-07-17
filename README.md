# RP Stack

Локальный RP-стек для длинных кампаний. Итерация 1 поднимает SillyTavern в Docker, хранит пользовательские данные вне контейнера и готовит ручное подключение NVIDIA API как OpenAI-compatible backend.

## Архитектура текущей итерации

```text
Browser in LAN
  -> http://192.168.1.88:8000
  -> rp-stack-sillytavern container
  -> SillyTavern Chat Completions / Custom OpenAI-compatible endpoint
  -> https://integrate.api.nvidia.com/v1
```

SillyTavern image: `ghcr.io/sillytavern/sillytavern:1.18.0`.

Iteration 2 adds a semi-automatic state workflow:

```text
SillyTavern scene
  -> state-updater prompt produces proposed JSON patch
  -> scripts/validate-state.py validates state and patch
  -> scripts/apply-state-patch.py previews by default
  -> scripts/apply-state-patch.py --confirm writes state/current.json
  -> scripts/render-state-block.py renders AUTHORITATIVE_WORLD_STATE for prompt injection
```

Iteration 3 adds bounded check adjudication:

```text
Quick Reply / explicit command
  -> scripts/run-check.py fixes the result
  -> state/checks.log records roll and modifiers
  -> state/proposed/check-<id>.json awaits review
  -> STscript injects AUTHORITATIVE_OUTCOME
  -> GLM narrates the fixed result
```

Persistent data on server:

```text
/srv/app-data/rp-stack/config
/srv/app-data/rp-stack/data
/srv/app-data/rp-stack/plugins
/srv/app-data/rp-stack/extensions
/srv/backups/rp-stack
```

Runtime state:

```text
/srv/apps/rp-stack/state/schema.json
/srv/apps/rp-stack/state/current.json
/srv/apps/rp-stack/state/history/
/srv/apps/rp-stack/state/proposed/
/srv/apps/rp-stack/state/audit.log
/srv/apps/rp-stack/state/checks.log
/srv/apps/rp-stack/state/last-check.json
```

## Запуск и остановка

```bash
cd /srv/apps/rp-stack
docker compose up -d
docker compose down
docker compose restart sillytavern
```

## Логи и healthcheck

```bash
cd /srv/apps/rp-stack
docker compose ps
docker compose logs --tail=100 sillytavern
docker inspect --format='{{.State.Health.Status}}' rp-stack-sillytavern
```

## Доступ

URL: `http://192.168.1.88:8000`

Включены:

- bind только на LAN IP сервера `192.168.1.88`;
- SillyTavern whitelist для loopback и `192.168.0.0/16`;
- HTTP Basic Auth внутри SillyTavern.

Пароль Basic Auth не хранится в Git. На сервере его можно посмотреть так:

```bash
sudo cat /etc/ansible/rp-stack-sillytavern-basic-auth-password
```

## NVIDIA API

В SillyTavern выбери:

```text
API type: Chat Completion
Chat Completion Source: Custom (OpenAI-compatible)
Base URL: https://integrate.api.nvidia.com/v1
Model: z-ai/glm-5.2
```

API key вводится вручную в UI SillyTavern. Не сохраняй его в Git, compose-файлах или документации.

## RP-профиль

Шаблоны лежат в `configs/`:

- `configs/prompts/base-gm-system.md`;
- `configs/prompts/base-authors-note.md`;
- `configs/world-info/world-template.md`;
- `configs/characters/character-template.md`;
- `configs/presets/openai-compatible-glm-5.2.json`.

## State Workflow

Generate a proposed patch with `configs/prompts/state-updater.md`, then save it under `state/proposed/`, for example:

```bash
cd /srv/apps/rp-stack
python3 scripts/validate-state.py --patch state/proposed/turn-001.json
python3 scripts/apply-state-patch.py --patch state/proposed/turn-001.json
python3 scripts/apply-state-patch.py --patch state/proposed/turn-001.json --confirm
python3 scripts/render-state-block.py
```

The first `apply-state-patch.py` command is a dry-run. Nothing is written until `--confirm` is present.

Rollback:

```bash
cd /srv/apps/rp-stack
python3 scripts/apply-state-patch.py --rollback latest
python3 scripts/apply-state-patch.py --rollback latest --confirm
```

After applying or rolling back state, update the SillyTavern Chat Lorebook / World Info entry with the output of `render-state-block.py`.

## Check Workflow

Run a bounded check from `/srv/apps/rp-stack`:

```bash
python3 scripts/run-check.py --type persuasion --target advisor --skill 2 --difficulty 12
```

Supported check types:

```text
persuasion intimidation deception stealth information resource feasibility trust conflict random_event
```

Use the printed `<AUTHORITATIVE_OUTCOME>` with the Quick Reply snippets in
`configs/stscript/checks/`, especially `inject-last-outcome.stscript`. GLM should
then narrate with `configs/prompts/outcome-narration.md`.

Review and apply the generated patch only after validation:

```bash
python3 scripts/validate-state.py --patch state/proposed/check-<id>.json
python3 scripts/apply-state-patch.py --patch state/proposed/check-<id>.json
python3 scripts/apply-state-patch.py --patch state/proposed/check-<id>.json --confirm
```

Clear an un-narrated check:

```bash
python3 scripts/rollback-last-check.py --confirm
```

## Обновление

Изменения вносятся через GitHub IaC-репозиторий `ubuntu_ansible_palybooks`. На сервере применяется pull-based Ansible:

```bash
sudo systemctl start ansible-local-apply.service
```

## Backup

```bash
cd /srv/apps/rp-stack
bash scripts/backup.sh
```

## Rollback

1. Откатить commit в GitHub/IaC.
2. Запустить `sudo systemctl start ansible-local-apply.service`.
3. При необходимости восстановить архив из `/srv/backups/rp-stack`.

## Ручные шаги

- Найти NVIDIA API key.
- Ввести key в UI SillyTavern.
- Создать тестовый чат и провести минимум 10 ходов на русском.
- Проверить, что чат и настройки сохраняются после `docker compose restart sillytavern`.

## Известные ограничения

- Iteration 2 uses manual confirmation and helper scripts; it is intentionally not fully automatic.
- Iteration 3 uses a server helper for rule resolution and STscript for Quick Reply workflow and prompt injection.
- NVIDIA key на этой итерации вводится вручную в UI.
- FastAPI gateway появится только в итерации 4.
