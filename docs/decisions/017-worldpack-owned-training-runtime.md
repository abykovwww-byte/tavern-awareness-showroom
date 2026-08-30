# Decision 017: WorldPack-owned Training Runtime

> **Source-era record with an active training contract.** WorldPack-owned
> scheduling, validation, fallback and scoring remain applicable. References
> below to parties/branches, the legacy compatibility resolver and RP Stack
> deployment tooling describe the former shared topology; standalone Showroom
> runs are the active boundary.

## Status

Accepted and implemented in the IaC source. Commit, deployment and live
verification are separate delivery states.

## Context

The first training worlds grew campaign-specific schedule, prompt, validation,
fallback and scoring branches inside Gateway. That made one awareness course
work only while Gateway knew its ID and Russian security phrases. Changing the
subject to fire safety, occupational safety or another curriculum would either
reuse incorrect phishing logic or require another application release.

Training still needs a universal trusted runtime. LLM narration alone cannot
own progression or scoring, while putting complete static scenes in Gateway
would remove generation and make WorldPacks presentational rather than
executable.

## Decision

### Gateway is a domain-neutral interpreter

Gateway owns only common mechanics:

- party authorization, idempotency, state versions and atomic turn commit;
- loading and validating a versioned runtime schema;
- snapshotting the resolved runtime contract per party and branch;
- evaluating allowlisted detector primitives and state effects;
- constructing a sanitized active-turn prompt contract;
- one initial narrator call, canonical normalization, at most one training-specific soft repair, output validation and deterministic fallback;
- integration with optional site/workspace evidence and public snapshots.

Gateway must not add a campaign-ID branch, sender schedule, phishing regex,
ОБЖ rule, score weight, answer key or course-specific fallback for a new world.
A new generic detector primitive or surface type is a versioned schema change;
a new subject rule is a WorldPack change.

### WorldPack owns the executable curriculum

A new deterministic training world declares:

```json
"training_runtime": {
  "schema_version": "rp-training-runtime.v2",
  "program": "training/program.json",
  "assessment": "training/assessment.json",
  "fallbacks": "training/fallbacks.json"
}
```

`program.json` owns ordered turns, windows, narrator instructions, required and
forbidden visible facts, output surfaces, role adapters, link policy, debrief
bindings and complete fallbacks. `assessment.json` owns observable text/UI
detectors, boolean rules, counter/score/evidence effects and bounded
aggregates. All referenced resources exist in `state-seed.json`.

The LLM remains responsible for fresh scene wording. The program constrains and
validates generation; it does not store a static final response. A provider or
validation failure uses the WorldPack fallback for that same turn.

### Runtime snapshot

On first use Gateway resolves the three files, validates them, computes a
content hash and stores the combined contract in
`training_runtime_snapshots`. Later WorldPack edits affect new parties only.
An existing party and every checkpoint branch retain the original contract,
including after the source pack is changed or removed.

### Prompt contract

For an active turn Gateway supplies `ACTIVE_TRAINING_TURN_CONTRACT` containing
only:

- contract hash, current turn/window, exact authored header/question and current instruction;
- current output surface and enabled link contract;
- learner name and stored role description;
- state paths explicitly allowlisted by that turn.

Gateway strips fallback text and does not serialize assessment rules, score
resources, answer keys or future turns before debrief. The surrounding prompt
keeps universal mode rules and WorldPack prompts separate. At debrief the active
contract includes only canonical score/evidence bindings required for the
authored explanation.

For plain turns the model returns fresh surface narration; Gateway replaces an
optional model-written boundary with the exact authored header and question and
normalizes a missing/distorted no-link marker when no URL is present. For interactive turns it returns one bare JSON
bundle with that complete narration in `narrative_text`. Gateway may unwrap one
provider-added Markdown JSON fence as transport normalization, but bundle
schema, visible slots and WorldPack narrative validation remain strict and
domain-neutral.

Runtime turns make one initial narrator request. A soft field/profile failure
may spend at most one repair completion controlled by `TRAINING_REPAIR_ATTEMPTS`;
the repair prompt lists only actual failed constraints in human language and
never exposes regexes. Hard sender/channel/shape/URL/attachment/forbidden-content
or canonical-score failures go directly to authored fallback. Provider failures
on party start follow the same path without repair, so a training run remains
playable without turning provider health into fake success.
Turn metadata records validation of the response actually delivered to the
learner and separately preserves whether the original failure came from the
provider or Gateway validation.

This revises the original one-call decision after a read-only audit of 46
`awareness-one-day` turns found 27 fallbacks (59%), including 25 validation
failures. The change does not alter deterministic scoring or authored schedule;
it reduces rejection of repairable presentation errors.

### Schema v2 and compatibility

`rp-training-runtime.v2` pairs with `rp-training-program.v2`. A turn may declare
optional `variation_budget` entries describing what narrator wording may vary,
and its surface may provide prose `must_include` requirements that mirror
machine-only regexes. Existing v1 runtime/program pairs remain accepted and do
not need either field. New builder output uses v2.

### Scoring contract

Assessment uses generic detector types:

- `text_regex` and `text_regex_count` over the current explicit learner action,
  with optional authored exclusions for negation/disqualifying context;
- `interaction_event` over normalized score-eligible site/workspace evidence;
- `profile_overlap` against the stored role description;
- boolean `expression` with `all`, `any` and `not`.

Rules target exact turns or all turns and apply `increment`, `set` or
`append_evidence`. Bounded aggregates are recomputed from canonical state after
the effects. Narrator text never becomes correctness evidence. Typed UI events
remain immutable sub-turn facts and are consumed with the next committed turn.

### Optional interaction capabilities stay orthogonal

`training_artifacts` and `training_workspace` continue to declare support.
Showroom scenario/run flags independently activate links and workspace, so all
four combinations remain valid when supported. Runtime may request an authored
artifact on a turn, but a disabled links capability produces the authored
no-link path and cannot be re-enabled by the narrator. Workspace follows the
same gate and is not required by the runtime schema.

### Compatibility

Training WorldPacks without `training_runtime` continue through the legacy
compatibility resolver. This prevents an in-place deployment from breaking old
parties. New packs and migrated packs use the generic runtime; campaign-specific
Gateway compatibility code is deprecated and receives no new subject logic.

## Validation and evidence

IaC runs `scripts/validate-training-runtime.py` before copying RP Stack source.
It rejects path escape, malformed JSON, unsupported schemas/surfaces/effects,
invalid regexes, non-contiguous turns, unknown detector references and any
state/debrief/fallback resource mismatch.

Gateway tests cover the migrated `awareness-one-day` world and a materially
different ОБЖ fixture. The latter loads different prompts, resources and score
rules through the same service without awareness constants. Tests also prove
the party contract snapshot remains immutable after source edits.

## Consequences

- A curriculum change can ship as reviewed WorldPack/IaC data without editing
  Gateway domain logic.
- Gateway remains the authoritative executor without becoming the author of
  the curriculum.
- Training narration remains generative while schedule, validation and score
  stay deterministic and reviewable.
- Runtime JSON is now deployable code and must pass preflight and focused tests.
- Existing legacy training worlds can be migrated incrementally without
  changing active parties.

## Related decisions

- [Decision 010: Party Scenario Types](https://github.com/abykovwww-byte/tavern-awareness-showroom/blob/1f0d5cb73afb23f36900623ad99f4ac762da9ff9/docs/decisions/010-party-scenario-types.md)
- [Decision 014: Interactive Training Site Artifacts](014-interactive-training-site-artifacts.md)
- [Decision 015: Independent Training Interaction Capabilities](015-training-scenario-interaction-capabilities.md)
- [Decision 016: RP Living Story Memory](https://github.com/abykovwww-byte/tavern-awareness-showroom/blob/1f0d5cb73afb23f36900623ad99f4ac762da9ff9/docs/decisions/016-rp-living-story-memory.md)
