# Operations

## Paths

```text
/srv/apps/rp-stack
/srv/app-data/rp-stack/gateway
/srv/backups/rp-stack
```

## Health and logs

```bash
cd /srv/apps/rp-stack
docker compose ps
docker compose logs --tail=100 rp-gateway rp-light-gui
docker inspect --format='{{.State.Health.Status}}' rp-stack-gateway
docker inspect --format='{{.State.Health.Status}}' rp-stack-light-gui
curl -fsS http://192.168.1.88:8010/health
curl -fsS http://192.168.1.88:8010/api/worldpacks
```

## Tests

```bash
cd /srv/apps/rp-stack
docker compose run --rm rp-gateway pytest
```

## Backup and restore

```bash
cd /srv/apps/rp-stack
bash scripts/backup.sh
```

To restore, stop the stack, unpack a selected archive at `/`, and start Compose
again. Always inspect the archive and target paths before restoring.

## Apply changes

Use the pull-based service after the required commit is on GitHub:

```bash
sudo systemctl start ansible-local-apply.service
sudo systemctl status ansible-local-apply.service --no-pager
```
