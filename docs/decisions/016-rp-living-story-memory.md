# Decision 016: RP-only living story memory

Date: 2026-08-03

## Status

Accepted; authority and projection semantics are superseded by Decisions 024
and 026. Decision 036 retires active Novel execution; any mention below is now
an archived storage/read boundary.

For `rp_contract_revision >= 8`, scheduling, section coverage, provider routing
and prompt projection are superseded by
[Decision 032](032-rp-history-first-prompt-and-sectioned-memory.md). This
Decision remains the compatibility contract for revisions `0..7`.

## Context

Raw turns preserve full evidence and immutable episodic chapters preserve old
scenes, but neither provides the narrator with one bounded, current registry of
a very long campaign. A manually maintained campaign summary demonstrated the
useful shape: canon, rules and abilities, inventory, characters, active and
resolved threads, unresolved hooks, current situation, and chronology.

The new mechanism must not change deterministic training behavior, replace
canonical state, delete raw history, or require the service model to read the
full 132k narrator prompt.

## Decision

Add a cumulative `rp_story_memory_snapshots` ledger and update it only when
`scenario_type == "rp"`.

- A global service model receives the previous bounded snapshot, the oldest
  new turn batch, and a compact canonical-state excerpt without NPC secrets.
- It returns a recoverable projection using the fixed v2 schema. Every entry
  carries a stable `fact_id`, text, provenance (`authority` and source turn IDs), and an explicit
  `active`, `superseded`, or `retracted` status.
- Starting with `rp_contract_revision >= 2`, Gateway merges the proposed
  projection with the previous snapshot. Omitted facts are retained; weak
  inference cannot create tombstones, retract stronger facts, or reactivate a
  retracted/superseded fact. A newer WorldPack/state/user correction may change
  terminal status while preserving the same `fact_id`.
- Default cadence is four new turns; a manual update may force a smaller batch.
- Each successful replacement is append-only, party-scoped, revisioned, and
  records turn coverage, state version, time, and model.
- A checkpoint fork copies only the newest snapshot fully covered by that
  checkpoint and resets its branch-local revision to one.
- The narrator receives only active `RP_STORY_MEMORY` entries after world
  instructions and before episodic chapters. Retracted and superseded entries
  remain auditable in snapshots but cannot enter the effective prompt. State,
  WorldPack absolute rules, and explicit user corrections remain higher authority.
- Background failures are fail-open and use durable service-job retry.

Training parties do not enqueue the job, load the snapshot, inject the prompt
block, expose the RP UI/API fields, or reserve context for it. Archived legacy
Novel rows cannot execute new turns or enqueue the job.

## Context budgets

The narrator stack keeps its 131072-token cap. RP reserves 10000 tokens for the
new dynamic layer, reducing default raw-history protection from 81920 to 71920
tokens. Other modes retain 81920.

The service-model request is separately bounded: previous snapshot up to 24000
characters, state excerpt up to 8000 characters, new turns up to about 6000
input tokens, and output up to 6000 tokens. This fits the configured 32768-token
local Gemma window without sending the complete campaign transcript.

## Consequences

- Very long RP campaigns gain a stable living continuity ledger similar to a
  maintained campaign-summary file.
- Immutable chapters and raw turns remain available for detail and audit.
- A service-model hallucination cannot directly mutate state, and can be
  superseded or retracted without deleting its audit entry.
- RP retains fewer verbatim recent turns inside the 132k prompt, by an explicit
  and observable 10k reserve.
- Training behavior and context budget remain unchanged.
