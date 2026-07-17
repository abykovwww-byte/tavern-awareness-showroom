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

Persistent data on server:

```text
/srv/app-data/rp-stack/config
/srv/app-data/rp-stack/data
/srv/app-data/rp-stack/plugins
/srv/app-data/rp-stack/extensions
/srv/backups/rp-stack
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

- Итерация 1 не содержит отдельного state store.
- Игровой outcome пока обеспечивается prompt-правилами, не rule engine.
- NVIDIA key на этой итерации вводится вручную в UI.
- FastAPI gateway появится только в итерации 4.

