# Decision 005: World Button UX

Date: 2026-07-18

## Context

The manual state workflow is reliable but too heavy for live play. A player
should be able to describe a world change in natural language and get a safe
preview without writing JSON.

Official references checked:

- SillyTavern STscript language reference:
  `https://docs.sillytavern.app/usage/st-script/`
- SillyTavern World Info reference:
  `https://docs.sillytavern.app/usage/core-concepts/worldinfo/`

## Decision

Add a `/world` chat command handled by `rp-gateway` before normal narration.
SillyTavern Quick Reply buttons send these commands through the same
OpenAI-compatible chat endpoint already used by the game.

```text
/world <natural-language instruction>
  -> draft pending StatePatch
  -> return readable preview

/world apply latest
  -> apply latest pending StatePatch transactionally

/world discard latest
  -> remove latest pending StatePatch from the apply queue
```

The LLM may draft JSON patches, but it cannot directly write canonical state.
The gateway validates operations, stores them in SQLite as `applied = 0`, and
only applies them after an explicit confirmation command.

## Consequences

- The user gets a simple SillyTavern button flow instead of manual JSON editing.
- The canonical source of truth remains `rp-gateway` state, not World Info.
- Quick Replies are sufficient for the MVP UI; a custom SillyTavern extension
  can reuse the new `/api/world/*` endpoints later.
- The gateway remains private to the Docker network; no unauthenticated state
  mutation endpoint is exposed on the LAN.
