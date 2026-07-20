# Decision 008: Light GUI Party Tools

Date: 2026-07-20

## Status

Accepted.

## Context

Long Light GUI sessions need tools that SillyTavern users normally assemble
from extensions: prompt inspection, character cards, and session recaps.
Light GUI has better source data than SillyTavern because the gateway already
owns `Party = WorldPack + PlayerCharacter + ModelProfile + State + TurnHistory`.

## Decision

Add party-scoped gateway helpers and expose them through a right-side drawer in
Light GUI.

The drawer opens only by user action and overlays the current viewport. It does
not participate in the main chat grid, so long sessions cannot squeeze or break
the service panel.

Gateway endpoints:

```text
GET    /api/parties/{party_id}/characters
GET    /api/parties/{party_id}/journal
POST   /api/parties/{party_id}/journal/summarize
DELETE /api/parties/{party_id}/journal/latest
POST   /api/parties/{party_id}/prompt/preview
```

Prompt preview is a dry-run debug surface. It may construct candidate prompt
state from the current player text and deterministic rule outcome, but it must
not write state, turns, checks, pending patches, memory, or journal entries.

Character sheets are read-only projections from authoritative state. They are
not a new character database.

Journal entries are human recaps. They are separate from long-term memory:
memory is concise narrator context, journal is readable session history.

## Consequences

- All new helpers are isolated by party `state_campaign_id`.
- Rollback does not delete turns, memory, or journal; entries remain tagged
  with the state version that existed when they were generated.
- SillyTavern stays legacy/debug. Light GUI reads gateway memory/state/journal
  directly instead of depending on SillyTavern extensions.
