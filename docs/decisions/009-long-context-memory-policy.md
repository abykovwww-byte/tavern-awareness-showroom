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

Make the memory policy configurable and measure text size rather than turns:

```text
PARTY_CONTEXT_MAX_TOKENS=131072
PARTY_CONTEXT_COMPLETION_RESERVE_TOKENS=16384
PARTY_CONTEXT_SYSTEM_RESERVE_TOKENS=32768
PARTY_CONTEXT_MIN_HISTORY_TOKENS=8192
MEMORY_SUMMARY_BATCH_TOKENS=65536
JOURNAL_AUTO_MIN_UNSUMMARIZED_TURNS=24
JOURNAL_MAX_BATCH_TURNS=48
POST_TURN_HELPERS_INLINE=false
MODEL_ATTEMPT_TIMEOUT_SECONDS=240
```

The selected model's known context window caps the working budget when it is
smaller than `PARTY_CONTEXT_MAX_TOKENS`. Gateway reserves 16k tokens for the
answer and 32k for rules, state, and long-term memory; the remainder holds the
newest complete user/GM turn pairs.

The same token budget feeds:

- party chat request construction;
- narrator message slicing;
- prompt preview;
- context estimation;
- memory's protected raw history.

As soon as a turn leaves the token-budgeted raw history, it is eligible for a
background summary. Until that request succeeds, Gateway keeps the unsummarized
overflow in the narrator prompt so no turn disappears during the handoff.

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

- Raw history adapts to message length: terse campaigns retain more turns;
  dense scenes summarize sooner.
- Auto-memory starts as soon as context overflows, without a fixed 16/48-turn
  waiting window.
- Existing `memory_summaries` and `journal_entries` remain valid; no migration
  is required.
- Smaller-context models can lower the limits through Ansible/env without code
  changes.
- Slow summarization no longer makes a completed GM response appear lost in the
  browser.
- Provider timeouts remain visible in audit logs, but the player receives a
  mechanically consistent fallback turn instead of an nginx/502 error.
