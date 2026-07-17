# Operations

## Paths

```text
/srv/apps/rp-stack
/srv/app-data/rp-stack
/srv/backups/rp-stack
```

## Commands

```bash
cd /srv/apps/rp-stack
docker compose up -d
docker compose ps
docker compose logs --tail=100 sillytavern
docker compose restart sillytavern
docker compose down
```

## Basic Auth Password

```bash
sudo cat /etc/ansible/rp-stack-sillytavern-basic-auth-password
```

Do not paste this password into GitHub issues, commits, docs, or chat logs.

## Backup

```bash
cd /srv/apps/rp-stack
bash scripts/backup.sh
```

## State Patch Workflow

Validate current state:

```bash
cd /srv/apps/rp-stack
python3 scripts/validate-state.py
```

Validate a proposed patch:

```bash
python3 scripts/validate-state.py --patch state/proposed/turn-001.json
```

Preview application:

```bash
python3 scripts/apply-state-patch.py --patch state/proposed/turn-001.json
```

Apply after review:

```bash
python3 scripts/apply-state-patch.py --patch state/proposed/turn-001.json --confirm
```

Render prompt block:

```bash
python3 scripts/render-state-block.py
```

Rollback:

```bash
python3 scripts/apply-state-patch.py --rollback latest
python3 scripts/apply-state-patch.py --rollback latest --confirm
```

## Restore

Stop the container, unpack the selected backup into `/srv/app-data/rp-stack`, then start the container again.

```bash
cd /srv/apps/rp-stack
docker compose down
sudo tar -xzf /srv/backups/rp-stack/<backup-file>.tar.gz -C /
docker compose up -d
```
