# Decision 019 Wave 0 contract freeze

Frozen on 2026-08-09 before S1 and lanes L1/L2/L4. This file records the exact
v3 contract introduced by Decision 019 and the course values that L1 must
preserve. S1 changes generic schema handling only; it does not edit WorldPacks.

## WorldPack runtime declaration

`worldpacks/awareness/manifest.json.training_runtime` has exactly these
references:

```json
{
  "schema_version": "rp-training-runtime.v3",
  "program": "training/program.json",
  "assessment": "training/assessment.json",
  "fallbacks": "training/fallbacks.json"
}
```

`program.json` uses `rp-training-program.v3`. Required top-level members are
`schema_version`, positive integer `revision`, `progression`, contiguous
`turns`, and `debrief`. `role_adapters` and `global_validation` use the same
generic shapes as `awareness-one-day`.

- `progression` owns `total_turns`, the three canonical resource IDs,
  `complete_value`, and `debrief_window`.
- Each turn owns `turn`, `window`, `header`, `instruction`, optional
  `variation_budget` and `visible_state_paths`, `question`, boolean
  `require_question`, complete authored `fallback`, and non-empty `surfaces`.
- Each surface owns a unique `type` (`email` or `messenger`), positive integer
  `count`, links policy (`none` or `artifact`), and its validation
  fields/patterns. Executable fallback text does not live in `surfaces` or
  `fallbacks.json`.
- Narrative validation requires exactly `count` blocks for every declared
  surface and rejects every undeclared block marker. Link auto-repair is
  disabled when a turn mixes link policies; that violation remains hard.
- `debrief` owns `header`, `instruction`, `scores`, `evidence_resources`, and
  its complete authored `fallback`.

Frozen v3 turn shape:

```json
{
  "turn": 1,
  "window": "Понедельник 10:00-14:00",
  "header": "Понедельник, 10:00-14:00",
  "instruction": "...",
  "question": "Что вы ответите и что сделаете?",
  "require_question": true,
  "variation_budget": ["..."],
  "fallback": "<two email blocks and one messenger block>",
  "surfaces": [
    {
      "type": "email",
      "count": 2,
      "links": "none",
      "must_include": ["..."],
      "required_patterns": ["..."],
      "forbidden_patterns": ["..."],
      "profile_adaptation": false
    },
    {
      "type": "messenger",
      "count": 1,
      "links": "artifact"
    }
  ]
}
```

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
  "note": "Turn and debrief fallbacks are colocated with their program turns so the active contract remains reviewable in one file."
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
  The only permitted textual changes are URL lines: artifact turns use
  `{{artifact.url}}`, while turns without an enabled artifact use
  `Ссылки: нет`. All other authored text and block structure stay unchanged.

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

The parameters are `(campaign_id, target_version)`. Existing public method
signatures and HTTP routes remain unchanged; L2 adds one generic, idempotent
route without deleting party data:

```text
POST /api/parties/{party_id}/complete
```

It sets `parties.status=completed` and preserves state, turns, audit events and
provider keys. The existing `/activate` route may reactivate the party.

Wave 4 adds `metadata_json.transport_status` with values `ok`,
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

## Compatibility and scaffold proof

Before S1, the canonical `awareness-one-day` v2 contract hash is:

```text
7011d55c45ebb21594dacb5a62ce451625799ec34a7e4298fc70b65f98660464
```

The temporary v3 scaffold contains one turn with two declared surfaces
(`email.count=2`, `messenger.count=1`), turn-level `require_question` and
turn-level `fallback`. It uses the unchanged assessment and fallbacks schemas.
The scaffold is not a shipped WorldPack. Acceptance by both validators is the
Wave 0/S1 boundary check and is recorded after the two schema maps are updated
in the single S1 commit.

The turn prompt contract is `rp-gateway.training-turn-contract.v2` and always
emits `surfaces` as a list. For v1/v2 programs the runtime normalizes the
existing singular `surface` into a one-element list without changing pack
files or their contract hashes.
