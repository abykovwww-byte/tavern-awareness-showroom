# Decision 013: Party Logs as Curated Training Data

## Status

Accepted. Decision 036 retains the retired scenario value only on the legacy
dataset read/export boundary.

## Goal

Preserve party play as an auditable source corpus that can later produce SFT
JSONL for LoRA or QLoRA without treating every model response as a good training
example.

## Capture contract

Gateway remains the only authority that records turns. Every new turn keeps:

- the exact prompt messages sent to the narrator and the final assistant text;
- provider response, state version, request and idempotency identifiers;
- stored scenario type (`rp`, `training`, or legacy `novel`) and world pack;
- narrator provider/model, authoritative outcome, validator result, repair count,
  fallback reason, and whether the turn came from a human line or an auto-test
  branch.

Raw turns, state history, checks, audit events, memory, and journals remain
operational records. They are not rewritten or deleted by dataset review.

## Review and tags

Dataset review is a separate overlay:

- every existing and new party starts as `review`;
- a party may be marked `excluded`, `review`, or `approved` and have up to 40
  normalized tags;
- every turn independently starts as `review` and may be labelled `excluded`,
  `review`, or `approved`, with tags and curator notes;
- automatic tags expose scenario type, main/branch origin, opening scenes,
  auto-test turns, repairs, validator failures, safe fallbacks, and the
  `player-liked` positive or `player-disliked` negative signal;
- an export contains a turn only when both its party and its turn are explicitly
  `approved`.

Approval is a quality and privacy gate. It must not be inferred from a successful
HTTP response or a passing narrative validator. Reviewers remain responsible for
personal data, copyrighted text, secrets, unwanted style, and incorrect model
behaviour.

## Player feedback

Light GUI and Showroom attach one reversible three-state rating to the complete pair
`player_message -> assistant_response`. The button is shown on the assistant
message, but the stored key is the Gateway turn ID, so the player cannot
accidentally rate only half of the exchange.

The rating is mutually exclusive: `positive`, `negative`, or `none`. Selecting
the active icon again clears it; selecting the opposite icon switches it in one
update. Existing boolean likes migrate to `positive`, and the compatibility
`liked` field remains available while clients move to `rating`.

Current feedback is stored separately from raw turns. Every update also creates
a `turn_feedback_updated` audit event with the turn ID, rating, derived boolean
flags, and the trusted server-side UI source (`light-gui` or `showroom`). Showroom
accepts the change only from the anonymous browser session that owns the run.

A positive value adds `player-liked`; a negative value adds `player-disliked`.
Both expose a structured `metadata.player_feedback` field in dataset candidates
and exports. Neither changes party or turn review status. Positive signals can
help filter SFT candidates. Negative signals are useful for exclusion, review,
and later preference-data construction, but are not by themselves a DPO pair
because no preferred replacement answer has been captured. Clearing the rating
removes either automatic tag while preserving the audit trail.

Legacy turns and world-command turns without a captured prompt are tagged
`missing-prompt` and omitted from SFT JSONL. The export reports their count in
`X-Dataset-Skipped-Missing-Prompt`; it never fabricates the missing training
input.

## Branches and leakage

Checkpoint branches copy the source transcript for runtime continuity, but copy
no dataset labels. Therefore inherited branch history stays `review` and cannot
duplicate an already approved main-line sample. New branch turns can be approved
individually.

Train, validation, and test splitting must use `metadata.group_id` (the state
campaign/branch), or a still coarser world-level group. Random per-turn splitting
is forbidden because adjacent turns share prompt history and would leak nearly
identical text across splits.

## Export

The admin JSONL endpoint emits `rp-gateway.sft.v1` records:

```json
{"messages":[{"role":"system","content":"..."},{"role":"user","content":"..."},{"role":"assistant","content":"..."}],"metadata":{"schema_version":"rp-gateway.sft.v1","sample_id":"campaign:turn","group_id":"campaign","scenario_type":"rp","worldpack_id":"demo","tags":["rp","main"]}}
```

LoRA and QLoRA use the same data format. Training should apply loss only to the
assistant completion; prompt messages remain input context. Metadata is retained
for filtering, group-aware splitting, provenance, and later preference-data
construction, but is not part of the model conversation.

## API

All dataset endpoints require an admin role when authentication is enabled:

```text
PATCH /api/admin/datasets/parties/{party_id}
GET   /api/admin/datasets/parties/{party_id}/turns?branch_id={optional}
PUT   /api/admin/datasets/parties/{party_id}/turns/{turn_id}?branch_id={optional}
GET   /api/admin/datasets/export.jsonl?scenario_type={optional}&include_branches=true
PUT   /api/parties/{party_id}/turns/{turn_id}/feedback
PUT   /api/showroom/runs/{run_id}/turns/{turn_id}/feedback
```
