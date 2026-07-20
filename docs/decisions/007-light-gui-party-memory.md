# Decision 007: Light GUI Party Memory

Date: 2026-07-20

## Status

Accepted. Implemented for party-scoped Light GUI play.

## Context

The party flow stores all turns in SQLite, but the narrator prompt used only a
short raw tail. For long parties this meant old events stayed recoverable in
storage but stopped informing the next narration.

The memory system must not replace authoritative state. Player claims,
unresolved possibilities and failed checks must stay distinct from confirmed
facts, and rollback must not delete turn history.

## Decision

Add a `memory_summaries` SQLite table owned by `StateStore` and scoped by
`campaign_id`:

```text
memory_summaries(
  campaign_id,
  from_turn_id,
  to_turn_id,
  state_version,
  summary_text,
  key_facts_json,
  open_threads_json,
  relationship_changes_json,
  player_promises_json,
  npc_obligations_json,
  created_at,
  model
)
```

After a successful turn, the gateway keeps the latest 8 turns raw and
best-effort summarizes older unsummarized turns in batches. Summary failure is
audited but does not fail the player turn.

The narrator prompt now contains:

```text
LONG_TERM_PARTY_MEMORY
Relevant state summary
AUTHORITATIVE_OUTCOME
recent raw turns
```

Current state and `AUTHORITATIVE_OUTCOME` always override memory. Each summary
records `state_version` so rollback can be reasoned about without deleting
turns or silently rewriting history.

## API

Light GUI uses party-scoped memory endpoints:

```text
GET    /api/parties/{party_id}/memory
POST   /api/parties/{party_id}/memory/summarize
DELETE /api/parties/{party_id}/memory/latest
```

The browser displays the latest summary and covered turns. GM controls can force
a summary attempt or delete the latest summary; neither action mutates canonical
state or deletes raw turns.

## Consequences

- New parties do not see another party's memory because every query is filtered
  through `state_campaign_id`.
- SillyTavern remains legacy/debug. Light GUI relies on gateway memory/state,
  not the SillyTavern summarization extension.
- Summary rows are durable context snapshots. Future work can add explicit
  regeneration of a full cumulative summary if needed.
