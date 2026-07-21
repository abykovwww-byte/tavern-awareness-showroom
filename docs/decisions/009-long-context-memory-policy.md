# Decision 009: Long-Context Memory Policy

Date: 2026-07-21

## Status

Accepted.

## Context

Decision 007 added party-scoped long-term memory for Light GUI. The first
policy was intentionally aggressive: keep a short raw tail, then summarize old
turns early. That protected small context windows but is wasteful for the
current preferred model profiles, where GLM and DeepSeek variants advertise
roughly 1M-token context windows.

For roleplay, raw scene history is often better than an early compression. The
gateway should use summarization as a durable checkpoint for genuinely long
sessions, not as the normal substitute for recent play.

## Decision

Make the memory policy configurable and default it for long-context models:

```text
PARTY_RAW_TURN_LIMIT=96
NARRATIVE_HISTORY_MESSAGE_LIMIT=0
MEMORY_AUTO_MIN_UNSUMMARIZED_TURNS=48
MEMORY_MAX_BATCH_TURNS=96
JOURNAL_AUTO_MIN_UNSUMMARIZED_TURNS=24
JOURNAL_MAX_BATCH_TURNS=48
POST_TURN_HELPERS_INLINE=false
MODEL_ATTEMPT_TIMEOUT_SECONDS=240
```

`NARRATIVE_HISTORY_MESSAGE_LIMIT=0` means the gateway derives the message limit
from `PARTY_RAW_TURN_LIMIT` as `2 * raw_turn_limit + 1`, preserving complete
user/GM pairs plus the next player turn.

The same raw-turn limit feeds:

- party chat request construction;
- narrator message slicing;
- prompt preview;
- context estimation;
- memory's protected raw tail.

Manual memory force still respects the protected raw tail. It bypasses the
minimum unsummarized threshold, but it does not summarize turns that are still
inside the direct raw window.

Post-turn helpers run outside the gameplay response path by default. A turn can
return to Light GUI as soon as state, checks, turn history, and audit are
persisted; auto-memory and auto-journal continue as best-effort background
helpers. Inline helper execution is reserved for deterministic tests and
debugging.

Narrative provider attempts get a longer timeout because prompts can now carry
more raw campaign context. If all provider attempts still fail after the
mechanical outcome is resolved, the gateway records a safe fallback narration
instead of surfacing HTTP 502 to the player.

## Consequences

- A 50-turn scenario can stay entirely raw in the narrator prompt by default.
- Auto-memory first appears only after there are enough old turns beyond the raw
  window.
- Existing `memory_summaries` and `journal_entries` remain valid; no migration
  is required.
- Smaller-context models can lower the limits through Ansible/env without code
  changes.
- Slow summarization no longer makes a completed GM response appear lost in the
  browser.
- Provider timeouts remain visible in audit logs, but the player receives a
  mechanically consistent fallback turn instead of an nginx/502 error.
