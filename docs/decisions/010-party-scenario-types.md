# Decision 010: Party Scenario Types

## Status

Accepted.

## Problem

The Gateway previously treated every Light GUI party as the same D20 roleplay
loop. That made literary campaigns unnecessarily mechanical and made authored
training scenarios vulnerable to random outcomes and free-form drift.

## Decision

Persist an explicit `scenario_type` on each party. The player must choose it in
the party creation dialog; neither the selected worldpack nor the model may
silently choose or change it.

### `rp`

- Parse player intent and resolve it through the D20 RuleEngine.
- Apply skill, preparation, leverage, relationship, difficulty, and blockers.
- Persist check records and require narration to preserve the fixed outcome.
- Keep `/check` available in Light GUI.

### `novel`

- Do not roll dice or create skill-check records.
- Treat player prose, dialogue, and directorial input as collaborative fiction.
- Prioritize character voice, relationships, atmosphere, pacing, and continuity.
- Preserve player control of consequential actions, emotions, beliefs, and consent.
- Keep state authoritative and record only the turn boundary in the deterministic patch.

### `training`

- Do not roll dice or allow `/check` to alter correctness.
- Resolve only explicit player actions and advance exactly one authored turn.
- Treat schedule, templates, scoring fields, completion conditions, and output
  validators as hard runtime constraints.
- Do not expose hints, correctness, hidden scoring, or remediation before the
  authored debrief point.

## Prompt Precedence

1. Gateway scenario contract.
2. Worldpack `gm_system` and `authors_note`.
3. Long-term party memory.
4. Current authoritative state and outcome.
5. Recent raw turns.

State and authoritative outcome remain factual authority regardless of prompt
order. Worldpack prompts supplement the selected scenario type and cannot
re-enable mechanics forbidden by it.

## Compatibility

Worldpack manifests may declare:

```json
"scenario_types": {
  "recommended": "novel",
  "supported": ["novel", "rp"]
}
```

The UI filters unsupported combinations after the user chooses a type. The API
also rejects an unsupported combination. Generated prompt-worlds without this
metadata support all three types.

Existing SQLite databases add `parties.scenario_type` through an additive
migration. Existing Awareness parties migrate to `training`; other existing
parties migrate to `rp`. New parties always provide the field explicitly.
