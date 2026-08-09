# Decision 019 Wave 0 contract freeze

Frozen on 2026-08-09 before lanes L1-L4. This file records the existing
contract shape; it does not introduce a schema version or a new runtime
surface.

## WorldPack runtime declaration

`worldpacks/awareness/manifest.json.training_runtime` has exactly these
references:

```json
{
  "schema_version": "rp-training-runtime.v2",
  "program": "training/program.json",
  "assessment": "training/assessment.json",
  "fallbacks": "training/fallbacks.json"
}
```

`program.json` uses `rp-training-program.v2`. Required top-level members are
`schema_version`, positive integer `revision`, `progression`, contiguous
`turns`, and `debrief`. `role_adapters` and `global_validation` use the same
generic shapes as `awareness-one-day`.

- `progression` owns `total_turns`, the three canonical resource IDs,
  `complete_value`, and `debrief_window`.
- Each turn owns `turn`, `window`, `header`, `instruction`, optional
  `variation_budget` and `visible_state_paths`, `question`, and `surface`.
- A surface owns `type` (`email` or `messenger`), positive `count`, links policy
  (`none` or `artifact`), validation fields/patterns, and the complete authored
  `fallback`. Executable fallback text does not live in `fallbacks.json`.
- `debrief` owns `header`, `instruction`, `scores`, `evidence_resources`, and
  its complete authored `fallback`.

`assessment.json` uses `rp-training-assessment.v1` and contains
`schema_version`, positive integer `revision`, `detectors`, `rules`, and
`aggregates`. Allowed detector primitives are `text_regex`,
`text_regex_count`, `interaction_event`, `profile_overlap`, and `expression`.
Allowed effects are `increment`, `set`, and `append_evidence`; every referenced
resource must exist in `state-seed.json`.

`fallbacks.json` remains metadata only:

```json
{
  "schema_version": "rp-training-fallbacks.v1",
  "note": "Turn and debrief fallbacks are colocated with their program surfaces so the active contract remains reviewable in one file."
}
```

## Awareness course values

- 10 authored turns.
- Odd turns: the weekday's `10:00-14:00` window; even turns:
  `15:00-18:00`, Monday through Friday in order.
- The debrief is a separate response after the learner answers turn 10.
- Progression resources: `current-turn-window`, `turns-remaining`, and
  `completion-status`; complete value: `complete`.
- Score and evidence resources preserve the legacy contract:
  `awareness-score`, `safe-escalations`, `reporting-quality`,
  `unnecessary-forwarding`, `credential-exposure`, `unsafe-actions`,
  `suspicious-artifacts-opened`, `confidential-disclosures`, and
  `links-opened`.
- Text detectors preserve only explicitly stated learner actions. Typed site
  and workspace events remain higher-confidence evidence and each authored
  `score_rule_id` applies at most once.
- Turn and debrief fallback text is copied from the legacy Gateway path before
  that path is deleted; no text may remain available only in Gateway code.

## SQLite and API signatures

The storage contract is fixed by Decision 019 B.4:

```sql
ALTER TABLE turns ADD COLUMN excluded_from_memory INTEGER NOT NULL DEFAULT 0;
```

`turns_for_memory` filters `excluded_from_memory = 0`. `rollback` remains
append-only and, after inserting the restored state version, runs:

```sql
UPDATE turns SET excluded_from_memory = 1
WHERE campaign_id = ? AND state_version > ?
```

The parameters are `(campaign_id, target_version)`. Public method signatures
and HTTP routes remain unchanged.

Wave 3 adds `metadata_json.transport_status` with values `ok`,
`provider_error`, `provider_timeout`, or `invalid_response` for every scenario
type. Existing `validator_valid`, `repaired`, `fallback`, and `fallback_reason`
keys remain for one release; new RP turns record `fallback=false`.

## Gateway deletion set

Remove every `AWARENESS_*` constant, every `awareness_*` function/method and
their imports/call sites from `rp-gateway/app/`. Also remove the now-unused
course detectors and helpers currently used only by those functions:
`DOUBLE_EXTENSION_RE`, `DANGEROUS_FILE_ACTION_RE`, `SOC_REPORT_RE`,
`FORWARD_TO_OTHERS_RE`, `SUSPICIOUS_CONTENT_RE`, `CREDENTIAL_ACTION_RE`,
`EXTERNAL_LOGIN_RE`, `CONFIDENTIAL_DISCLOSURE_RE`, `REPORT_DETAIL_RE`,
`INDEPENDENT_VERIFY_RE`, `EXPLICIT_REFUSAL_RE`, `PROFESSIONAL_RESPONSE_RE`,
`ROLE_ALIGNED_ACTION_RE`, `ROLE_STOP_WORDS`, and `role_terms`.

The acceptance check is case-insensitive and must return no matches:

```text
rg -n -i awareness rp-gateway/app
```

## Wave 0 scaffold proof

The repository validator accepted a minimal one-turn scaffold with the shapes
above. The scaffold was temporary and was removed after the proof; it is not a
second example WorldPack or a shipped artifact.
