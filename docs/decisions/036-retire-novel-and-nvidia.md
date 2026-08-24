# Decision 036: Retire active Novel mode and NVIDIA provider

**Date:** 2026-08-24

## Status

**Decision status: Accepted.** The active RP Stack supports only `rp` and
`training` scenario creation and does not route any new request to NVIDIA.

**Implementation readiness: `подключено`.** Delivery evidence is tracked in
[`registry/036.yml`](registry/036.yml). PR
[#68](https://github.com/abykovwww-byte/ubuntu_ansible_palybooks/pull/68)
was merged as `0fb0ab0dd794e55eb9b2177c227c1591f97841c0`, applied through
`ansible-local-apply.service`, and verified against the rebuilt containers,
HTTP/API, read-only SQLite aggregates, and both browser clients on 2026-08-24.
The production-image forced-local-outage regression proves the no-switch path;
no real provider outage or historical NVIDIA party turn was triggered on live
data, so this evidence does not claim `наблюдается` or `держится`.

## Context

`novel` duplicated the non-D20 RP path while retaining separate prompts,
validation branches, UI choices, WorldPack declarations, and operational
documentation. NVIDIA was similarly spread across the provider catalog,
OpenAI-compatible transport field names, service-model fallback, credentials,
both interfaces, tests, Compose, and Ansible.

Removing either contour by hiding one UI option would leave silent runtime
routes and unsafe fallbacks. At the same time, rewriting stored parties,
profiles, traces, datasets, or logs would destroy historical evidence and could
silently change a campaign's model or scenario semantics.

## Decision

### Active scenario contract

- Party and Showroom create/update payloads accept only `rp | training`.
  Pydantic literal validation owns the invalid-input response, so `novel`
  naturally returns HTTP `422`; no parallel manual `400` branch is kept.
- Existing stored `novel` parties and Showroom scenarios are archived by an
  idempotent additive startup migration. They are not converted to `rp` and no
  turn, trace, dataset, or historical row is deleted.
- `archived` is terminal for gameplay. An archived party cannot be activated,
  started, or messaged. An archived Showroom scenario cannot be published or
  used to create a new run. Existing historical list/read, Turn Trace, dataset,
  and stored run projections may still expose the literal `novel`.
- New runtime code, both GUIs, active WorldPack manifests/prompts/setup/rules,
  and current contract descriptions contain no Novel execution path.

The word `novel` is therefore permitted only at the legacy
storage/read/migration boundary and in historical decision evidence that is
explicitly labelled as retired.

### Active provider contract

- Active narrator providers are Gemini, OpenRouter, and the explicitly enabled
  local OpenAI-compatible runner. NVIDIA is removed from catalogs, credentials,
  endpoint selection, UI, active provider fixtures, environment templates, Compose/Ansible
  variables, prompts, presets, and retry/fallback lists.
- Historical NVIDIA model profiles, party references, provider fields, and log
  rows remain readable. They are not rewritten to another provider. NVIDIA
  profiles are excluded from active narrator, Showroom, BYOK, and autotest
  selection, and cannot be attached to a new or updated party.
- Service-model routing is explicit per selected role choice: `local` uses only
  the local base URL with no credential; `openrouter` uses only the OpenRouter
  base URL and stack-managed service key. It does not inherit `LLM_PROVIDER`.
- If the selected local service model is unavailable, the service task fails or
  follows its existing bounded job retry/degradation path without changing
  provider. NVIDIA is never a fallback, retry target, or implicit default.
- Every new `service_call_log` row records the explicit attempted provider and
  model. A forced local-model outage must produce no new row with
  `provider='nvidia'`.

Generic OpenAI-compatible HTTP, redaction, provider-attempt diagnostics,
OpenRouter same-provider narrator fallbacks, and historical storage schemas are
shared components and remain.

### Data and migration boundary

The migration is additive and idempotent. It changes only active status of
legacy Novel aggregates. It does not delete history or rewrite NVIDIA rows.
Any pre-existing active party that still references an NVIDIA profile requires
an explicit product decision; it must never be silently reassigned. The
repository implementation fails closed for such a runtime binding.

## Consequences

- Users can create and play only RP or training scenarios.
- Administrators cannot publish Novel scenarios or select NVIDIA/BYOK routes.
- Local service-model outages are visible failures/degradation, not cloud
  provider switches.
- Old diagnostics and datasets remain useful, even when they contain retired
  scenario/provider values.

## Non-goals

- deleting or rewriting historical parties, profiles, turns, datasets, traces,
  or provider logs;
- migrating an NVIDIA party to OpenRouter, Gemini, or local model;
- adding a routing abstraction, provider registry service, or dependency;
- changing RP revision 8 mechanics, retention policy, or deployment state;
- manually deleting or rewriting live rows, bypassing the branch/PR/pull-based
  deployment path, or running a destructive provider canary against a legacy
  party.

## Related decisions

- [Decision 010](010-party-scenario-types.md)
- [Decision 012](012-public-showroom-scenarios.md)
- [Decision 022](022-readiness-and-observability-policy.md)
- [Decision 027](027-turn-trace-workbench.md)
