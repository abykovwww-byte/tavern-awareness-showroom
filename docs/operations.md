# Operations

## Production path contract

```text
/srv/apps/awareness-showroom
/srv/app-data/awareness-showroom/gateway
/srv/app-data/awareness-showroom/state
/srv/app-data/awareness-showroom/showroom-covers
/srv/backups/awareness-showroom
```

None of these paths may alias an RP Stack data directory. The source checkout
is immutable; database, state, covers and backups stay outside it.

Required deployment configuration includes `APP_REVISION`, the three
`AWARENESS_*_DIR` runtime paths, `AWARENESS_BACKUP_DIR`, distinct Gateway and
visitor cookie names, a non-default bootstrap administrator password, and the
selected provider credentials. Do not inherit a generic RP `.env`.

## Shadow preflight

The accepted shadow listener is `127.0.0.1:18011`. Production port `8011` is
changed by IaC only during the approved C1 cutover. Do not use `8012`.

```bash
cd /srv/apps/awareness-showroom
docker compose --env-file .env config --quiet
docker compose --env-file .env up -d --build
docker compose --env-file .env ps
docker compose --env-file .env logs --tail=100 awareness-gateway showroom
curl -fsS http://127.0.0.1:18011/health
```

With the optional external local model:

```bash
docker network inspect rp-llm
docker compose -f compose.yml -f compose.local-llm.yml --env-file .env config --quiet
docker compose -f compose.yml -f compose.local-llm.yml --env-file .env up -d --build
```

The standalone Compose project owns only `awareness-gateway`, `showroom`, and
its private application network. It does not start or stop the local model
runner.

## Fresh bootstrap

Use empty, access-controlled Gateway, state and cover directories. Do not copy
the mixed RP database, state tree, cookies, provider secrets or cover directory.
Keep the legacy deployment available read-only for historical results during
the acceptance and rollback window.

On first start, confirm the administrator bootstrap succeeds, then rotate or
remove bootstrap-only credentials according to the deployment secret workflow.
Never paste provider keys into Showroom or commit them to Git.

## Config-only migration

Perform this only after shadow acceptance and an O1 write freeze on the old
Showroom. Refresh the source inventory from the legacy SQLite in read-only mode
at that time; the earlier planning snapshot is not cutover evidence.

Recreate only published scenario configuration through the new admin API:

- title, description, status and sort order;
- Awareness WorldPack slug;
- model matched by `(provider, base_url, model)`, never by a legacy profile ID;
- leaderboard presentation and interactive links/workspace flags;
- cover uploaded again after the new scenario ID is returned.

Do not copy visitors, runs, internal parties, turns, state versions, events,
sessions, users, provider keys, feedback or leaderboard rows. Verify the new
published count and every mapped field in the admin UI before accepting the
import. `Мои прохождения` and the leaderboard intentionally start empty.

## Live acceptance

A source check, image build or healthy container is necessary but insufficient.
Before C1, verify through the shadow origin:

1. `/health` succeeds; separately verify the deployed `APP_REVISION` through
   the IaC/container configuration against the intended source revision.
2. Only `awareness` and `awareness-one-day` are selectable; no RP or novel mode
   can be created through the UI or direct Gateway request.
3. A new visitor receives `awareness_showroom_visitor`, while administrator
   login uses `awareness_gateway_session`; RP cookies remain untouched.
4. A full training run can open its portal, interactive site/workspace, commit
   typed evidence, finish, and display the same persisted result and leaderboard
   entry after refresh.
5. Uploaded covers survive a container recreate and are absent from RP storage.
6. A Gateway restart preserves the run and canonical state.
7. If enabled, provider traffic reaches only the explicitly configured route;
   local-LLM loss follows the configured training behavior without crossing to
   RP credentials.

Record browser-visible evidence plus read-only database/state evidence. Do not
infer functional success from HTTP 200 or fallback prose alone.

## Backup and restore

Before cutover, the operator/IaC backup must capture a consistent SQLite
snapshot plus the standalone state and cover directories into
`AWARENESS_BACKUP_DIR`. The backup directory is deliberately not mounted in the
containers. Validate archive contents and perform a restore rehearsal into
separate temporary paths before relying on it.

The repository scripts target only the standalone project:

```bash
sudo /srv/apps/awareness-showroom/scripts/check.sh
sudo /srv/apps/awareness-showroom/scripts/backup.sh
```

`check.sh` resolves the currently published Showroom address from Compose, so
the same command checks shadow `18011` and the later production `8011` binding.
`backup.sh` uses SQLite's online backup API, then archives the matching state
and cover trees into `/srv/backups/awareness-showroom`. A successful archive is
not restore proof.

Restore only a backup produced by this standalone service. Stop writers first,
resolve and verify every absolute target path, restore database/state/covers as
one versioned set, and then repeat the live-acceptance checks.

## Cutover and rollback

At C1, IaC changes the standalone listener from shadow `18011` to production
`8011` only after the original Showroom has released that port. Preserve the
original RP deployment and its data during the rollback window.

Rollback reverses routing/port ownership to the original Showroom and stops the
standalone project without deleting its data. Because the recommended migration
uses a fresh database, rollback does not require merging new training writes
back into the RP database. Export of post-cutover training results, if required,
is a separate reviewed operation.
