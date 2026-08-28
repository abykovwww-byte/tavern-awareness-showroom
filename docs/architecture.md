# Standalone architecture

## Product boundary

```text
Browser
  -> showroom:80
       static client
       same-origin /api and /health reverse proxy
  -> awareness-gateway:8088
       authentication and visitor identity
       training scenarios, runs, scoring and results
       portal, artifacts and workspace
       provider routing
  -> dedicated SQLite + state + covers
```

The standalone service has no runtime dependency on RP Gateway or Light GUI.
It owns its release, configuration, database, canonical state, uploaded covers,
session cookies and visitor cookies. The optional local LLM process may be
shared as infrastructure, but only through the explicit `rp-llm` network; its
credentials and routing policy remain owned by this deployment.

## API boundary

The browser speaks only to Showroom on one origin. Nginx serves static assets
and proxies `/api/*` and `/health` to `awareness-gateway`. The Gateway remains
the authority behind the existing `/api/showroom/*` contracts for visitor
identity, scenarios, runs, portal, leaderboard, cover assets, artifacts and
workspace events. Provider calls are never exposed directly to the browser.

The extraction preserves those public response shapes and cookie behavior so
the existing Showroom client can move without a coordinated browser rewrite.
Breaking changes require a versioned API migration, not a silent split-only
change.

## Data authority

| Data | Authority | Host storage |
| --- | --- | --- |
| Gateway SQLite | Awareness Gateway | `AWARENESS_GATEWAY_DATA_DIR` |
| Canonical state files | Awareness Gateway | `AWARENESS_STATE_DIR` |
| Uploaded covers | Awareness Gateway | `AWARENESS_SHOWROOM_COVER_DIR` |
| Training definitions | Git | `worldpacks/awareness*` |
| Published scenario settings and source covers | Git | `configs/showroom/` |
| Browser session/visitor tokens | Awareness Gateway; opaque to browser | dedicated cookie names |
| Provider secrets | deployment configuration | untracked environment/secret store |
| Backups | operator/IaC | `AWARENESS_BACKUP_DIR` |

The three mutable runtime mounts are intentionally distinct. They must not
point into RP Stack paths. Covers are mounted at `/data/showroom-covers`, while
the database uses `/data/awareness_gateway.db`; the nested container path does
not imply shared host storage.

## Training-only contract

`worldpacks` has exactly two top-level directories:
`awareness` and `awareness-one-day`. Each manifest recommends and supports only
`training` and declares `training_runtime`. CI enforces this closed set.

Gateway interprets the versioned training program, assessment and fallback
contracts. Interactive sites and workspace actions are typed evidence: they do
not independently advance the authored turn schedule or make a provider call.
The browser cannot author scores, run completion or canonical artifact state.

Process and resource guards close the active scenario surface before storage or
provider side effects. Some coupled internal Gateway modules remain until a
separate dependency-backed prune; live shadow acceptance is still required
before production cutover.

## Migration decision

The minimal safe migration starts with a fresh standalone SQLite database and
empty state/covers directories. Published scenario configuration is captured
in the versioned Git catalog and reconciled at Gateway start. Scenario identity
uses a stable catalog key, model profiles are mapped by
`(provider, base_url, model)`, and catalog covers pass the same size, signature
and MIME validation as an admin upload before they enter mutable cover storage.

Reconciliation is an idempotent upsert: unchanged rows keep their revision and
timestamp, and absent catalog rows are not deleted or archived. A missing or
ambiguous model tuple, invalid WorldPack contract or unsafe cover stops startup
instead of selecting a fallback.

Every managed row explicitly declares a relative cover path or `null`. `null`
removes a drifting runtime cover for that scenario; removing the whole catalog
row still preserves the scenario and all run data. All entries are validated
before writes. Applying several DB/file changes is retry-convergent rather than
one cross-filesystem transaction, and a failed reconciliation never starts the
HTTP application.

Mixed RP history, runs, state, old sessions, visitor tokens, identities and
provider keys remain readable in the original RP deployment but are not
copied. Existing users receive new standalone cookies and create new training
runs. Historical migration would require a separate explicit contract.
