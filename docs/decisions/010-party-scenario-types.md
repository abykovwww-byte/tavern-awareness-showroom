# Decision 010: Party Scenario Types

## Status

Accepted; active scenario creation is superseded by Decision 036 and now allows
only `rp` and `training`. Stored `novel` remains a legacy read/migration value.
RP resolution details are superseded by Decision 024 for parties on `rp-core.v2`.

## Problem

The Gateway previously treated every Light GUI party as the same D20 roleplay
loop. That made literary campaigns unnecessarily mechanical and made authored
training scenarios vulnerable to random outcomes and free-form drift.

## Decision

Persist an explicit `scenario_type` on each party. The player must choose it in
the party creation dialog; neither the selected worldpack nor the model may
silently choose or change it.

### `rp`

- Persist the WorldPack-selected RP contract version on the party.
- For `rp-core.v2`, treat free text and compatibility `/check` as neutral
  narrative continuation without D20, difficulty, score, success/failure, or a
  check record.
- Continue from canonical state, WorldPack constraints, information, resources,
  NPC goals, relationship pressure and prior consequences.
- Enforce typed absolute rules and player agency after narration, before commit.
- Keep legacy `rp-core.v1` behavior only for existing parties until an explicit
  versioned migration.

### `novel`

Retired from active creation and execution by
[Decision 036](036-retire-novel-and-nvidia.md). The following bullets describe
the historical contract only; stored rows remain readable and are archived
without conversion to RP.

- Do not roll dice or create skill-check records.
- Treat player prose, dialogue, and directorial input as collaborative fiction.
- Prioritize character voice, relationships, atmosphere, pacing, and continuity.
- Preserve player control of consequential actions, emotions, beliefs, and consent.
- Keep state authoritative and record only the turn boundary in the deterministic patch.

### `training`

- Do not roll dice or allow `/check` to alter correctness.
- Resolve only explicit player actions and advance exactly one authored turn.
- Treat schedule, templates, scoring fields, completion conditions, and output
  validators from a WorldPack `training_runtime` as hard runtime constraints.
- Keep Gateway domain-neutral: it interprets the generic runtime schema and
  snapshots it per party; subject logic stays in program/assessment files.
- Do not expose hints, correctness, hidden scoring, or remediation before the
  authored debrief point.

## Prompt Precedence

1. Gateway scenario contract.
2. Worldpack `gm_system` and `authors_note`.
3. Sanitized active WorldPack training-turn contract when present.
4. Long-term party memory.
5. Current authoritative state and outcome.
6. Recent raw turns.

State and authoritative outcome remain factual authority regardless of prompt
order. Worldpack prompts supplement the selected scenario type and cannot
re-enable mechanics forbidden by it.

## Compatibility

Historical WorldPack manifests could declare:

```json
"scenario_types": {
  "recommended": "novel",
  "supported": ["novel", "rp"]
}
```

This manifest is historical evidence only. The active UI does not offer the
retired value, and active request validation rejects it before WorldPack
compatibility is evaluated. Generated prompt-worlds without this metadata may
be used only with the two active request types.

Active manifests declare only `rp` and/or `training`. Existing SQLite databases add `parties.scenario_type` through an additive
migration. Existing Awareness parties migrate to `training`; other existing
parties migrate to `rp`. New parties always provide the field explicitly.

Decision 036 adds a second idempotent boundary migration that archives stored
`novel` parties without rewriting their scenario type or history.

The executable training contract and compatibility policy are defined by
[Decision 017](017-worldpack-owned-training-runtime.md).
