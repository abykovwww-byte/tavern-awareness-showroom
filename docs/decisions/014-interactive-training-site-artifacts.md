# Decision 014: Interactive Training Site Artifacts

> **Source-era record with an active training contract.** Artifact authority,
> validation, privacy and deterministic scoring remain applicable. References
> to Light GUI, RP Showroom and RP Stack deployment evidence describe the
> former topology and are superseded by the standalone Showroom and Awareness
> Gateway.

## Status

Accepted, implemented and deployed. The live verification snapshot for revision
`8b8a8fe` is recorded in `docs/wiki/09-operations-and-repository.md`.

## Decision

Interactive training sites are a logical Gateway capability, not a new
deployable service. `TrainingArtifactService` loads authored WorldPack site
blueprints and server-only scoring policies, gives the narrator an allowlisted
slot contract, validates its combined narrative-and-artifact response, and
stores the resulting immutable snapshot with the turn.

The main narrator produces visible site wording in the same completion as the
email or messenger surface. Opening, rendering, and interacting with the site
must not call an LLM or run Python. Light GUI and RP Showroom use the same static
JavaScript/CSS renderer and construct DOM only from the validated public
snapshot. Model-supplied HTML, CSS, JavaScript, URLs, renderer names, field
semantics, and scoring rules are forbidden.

## Authority and lifecycle

- WorldPack authors choose the site blueprint, fixed reserved-domain URL,
  renderer, fields, active turn, and event-to-score mapping.
- The narrator fills only declared visible string slots within their authored
  limits and repeats the fixed URL in the narrative message.
- Gateway creates the artifact ID, persists public and private parts
  separately, and returns only the public snapshot to clients and history.
- The browser emits idempotent semantic events. Form values never leave the
  browser; only IDs of non-empty fields are sent.
- Gateway validates party ownership, active authored turn, revision, action,
  and field IDs. A submitted authored credential field becomes
  `credentials_submitted` regardless of player prose.
- Events are append-only and remain pending until the next player turn records
  and consumes them transactionally. Typed UI evidence has precedence over
  contradictory free text.
- Dataset review and SFT metadata include the artifacts and consumed evidence
  for auditability, but never the server-only policy or entered values.

## Initial scope

The `awareness-one-day` WorldPack contains ten reusable site blueprints. Its
first active surfaces are the hostile corporate SSO site on turn 4 and the
legitimate cloud file-share site on turn 6. Additional sites are authored data,
not new frontend or Gateway code, provided they use an existing allowlisted
renderer and theme.

## Safety and performance

All simulated URLs use IANA-reserved domains. Both UIs apply a strict CSP with
same-origin scripts, styles, and API calls; forms cannot navigate. The renderer
uses no remote assets and never inserts model text as HTML. Click endpoints do
SQLite validation and insertion only, so they add no narrator latency and do
not contend with model inference.

## Consequences

This keeps scoring deterministic and party-scoped without adding another
container or a second LLM request. Adding a new interaction primitive requires
an explicit schema, Gateway validator, shared renderer, policy, and test update;
arbitrary generated websites remain outside the supported contract.
