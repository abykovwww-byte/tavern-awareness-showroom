# Decision 015: Independent Training Interaction Capabilities

## Status

Accepted and implemented in the IaC repository. Deployment and live browser
verification are tracked separately from the source revision.

## Context

Interactive training sites and the department workspace are independently
enabled by immutable Showroom run flags. The workspace adds a second
interaction surface: a party-scoped folder tree with
static and dynamically materialized files.

The Showroom scenario editor needs two independent switches:

- `interactive_links_enabled` — «Подключить интерактивные ссылки»;
- `interactive_workspace_enabled` — «Подключить интерактивный диск».

The switches configure one Showroom scenario. They do not create copies of a
WorldPack or automatically generate four storefront cards.

## Decision

### Capability declaration and activation are separate

A training WorldPack declares support by carrying a valid detailed contract:

- `manifest.training_artifacts` means interactive links are supported;
- `manifest.training_workspace` means the interactive workspace is supported.

Gateway derives public support flags from those contracts. The manifest must
not repeat them in another boolean capability list.

A Showroom scenario selects an allowed subset with the two booleans above.
All four combinations are valid when the WorldPack supports them:

| Links | Workspace | Runtime surface |
| --- | --- | --- |
| off | off | ordinary training chat only |
| on | off | chat plus interactive simulated sites |
| off | on | chat plus department workspace |
| on | on | both interaction surfaces |

The flags are legal only for `scenario_type=training`. Gateway rejects an
enabled flag when the selected WorldPack does not expose the matching valid
contract. Non-training scenarios always store both flags as false.

### Scenario configuration and run snapshot

Add typed boolean columns to `showroom_scenarios` and snapshot them into
`showroom_runs` when a run is created. Runtime code reads only the run snapshot;
editing a scenario never changes an active party.

Existing published training scenarios with a valid site contract must be
backfilled to `interactive_links_enabled=true` to preserve deployed behavior.
Other existing scenarios remain false. New scenarios default both flags to
false and require an explicit administrator choice.

### Gateway remains authoritative

The Showroom UI only edits the requested flags. Gateway validates them against
scenario type and WorldPack support, snapshots them into the run, and gates:

- narrator artifact contracts and fallback selection;
- public artifact and workspace responses;
- event endpoints;
- RuleEngine evidence consumption;
- dataset, audit, debrief and leaderboard metadata.

An off capability cannot be re-enabled by narrator output, browser state, a
crafted event request, or the mere presence of a WorldPack catalog.

### Interactive links

Decision 014 remains the detailed site contract. This decision changes only
activation: a valid site catalog is capacity, while the scenario/run flag is
permission to use it.

When links are off, Gateway does not give the narrator an artifact slot
contract, does not materialize a site snapshot, and rejects site events. The
authored schedule must provide a capability-off fallback so the scenario stays
coherent and assessable without the clickable site.

### Interactive workspace

Add `TrainingWorkspaceService` as a logical module inside Gateway. It is not a
new synchronous deployable. It owns:

- validation of folder and file blueprints;
- party-scoped workspace snapshots and immutable file revisions;
- binding of versioned static training resources;
- materialization of allowed LLM-filled visible slots;
- idempotent typed file events and normalized RuleEngine evidence.

The browser renders only allowlisted application components. Model-provided
HTML, JavaScript, macros, executable files, paths, MIME types, hidden phishing
classification and scoring rules are forbidden.

Static resources such as an information-security policy are authored as
versioned IaC WorldPack resources and bound to stable folder IDs. A run records
the immutable file revision and content hash. Public/anonymous Showroom exposes
only `public_training` resources; `restricted_internal` documents require a
future authenticated participant and ingestion flow.

Dynamic files are materialized at party start or on an authored turn. The main
narrator completion fills only declared visible slots in the same response as
the narrative. Opening a file never calls an LLM. Authored fallback content is
mandatory.

### File events and scoring

The public file snapshot never contains `phishing`, correctness, score deltas
or answer keys. Server-only policy maps semantic events such as `file_opened`,
`file_downloaded`, `file_reported`, `link_opened` or
`active_content_enabled` to deterministic evidence and `score_rule_id`.

Opening an authored phishing file can therefore be a scored failure without
checking file contents or exposing its classification. Events are idempotent,
`score_once` is enforced server-side, and a later report adds evidence without
erasing an earlier unsafe open.

Workspace availability is lifecycle-based (`available_from_turn`, optional
`available_until_turn`, `deleted_at_turn`), not restricted to the current
surface turn. This differs from the existing one-turn site event gate and must
not be implemented by weakening site ownership checks globally.

### Performance boundary

Folder listing, preview and event recording are SQLite/JSON operations only.
No LLM request, filesystem scan or document conversion runs on open or click.
If Office/PDF conversion, MIME inspection or malware scanning is added, it runs
in an optional asynchronous worker during resource ingestion. Scenario publish
is blocked until required resources are ready; gameplay requests never wait for
that worker.

### Comparability

The two capability flags become result dimensions. Leaderboards, autotests,
dataset exports and analytics must include both values; scores from different
capability combinations are not silently merged.

## Consequences

- One WorldPack can support multiple Showroom configurations without copying
  authored state, prompts or scoring rules.
- Every optional capability needs a complete authored off-path and fallback.
- Gateway schemas, Showroom persistence, run creation, Showroom UI, party API,
  tests, Wiki and the training builder contract must change together.
- The existing site runtime remains compatible after a migration backfill.
- The workspace can be implemented in Gateway without adding latency to small
  API requests or requiring another narrator completion.

## Related decisions

- [Decision 010: Party Scenario Types](010-party-scenario-types.md)
- [Decision 012: Public Showroom Scenarios](012-public-showroom-scenarios.md)
- [Decision 014: Interactive Training Site Artifacts](014-interactive-training-site-artifacts.md)
