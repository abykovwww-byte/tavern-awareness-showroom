# Decision 011: Admin LLM-vs-LLM Auto-tests

## Status

Accepted for Light GUI admin accounts.

## Goal

Allow an administrator to describe how an LLM should play a character and run
up to 30 automated player turns against the normal Gateway narrator. The
auto-player provider/model is selected independently from the party narrator
and is intentionally limited to OpenRouter or the private Local Gemma profile.

## Isolation and authority

- A run creates a new party from the selected party's world, character,
  scenario type, and narrator model. It starts from the world pack state seed;
  the source party is never mutated.
- Every generated player action enters the normal party message path. Gateway
  remains authoritative for scenario rules, checks, state, memory, validation,
  and narrator model routing.
- The auto-player sees only its public character description and the visible
  player/GM transcript. It never receives canonical state, hidden training
  scores, rubrics, answer keys, prompt-inspector blocks, or service data.
- Training parties therefore preserve the deterministic one-authored-turn rule
  and do not leak feedback before the authored debrief.

## Durability

Runs are stored in SQLite with requested/completed turn counts, status, phase,
selected player profile, prompt, last action, and error. A deterministic
idempotency key is used for each narrator turn. Running jobs resume after a
Gateway restart without duplicating a recorded turn.

Stop requests are cooperative: the current provider request is allowed to
finish so the Gateway does not leave a half-written turn request. The run stops
before the next player/narrator boundary.

While a run is active, its test party is readable in Light GUI but rejects
manual player messages. This prevents a human turn from racing the background
LLM-player against the same canonical state.

## API

All endpoints require an admin role when Gateway authentication is enabled:

```text
GET  /api/admin/autotests/models
GET  /api/admin/autotests
POST /api/admin/autotests
POST /api/admin/autotests/{run_id}/stop
```

`POST /api/admin/autotests` accepts a source party, player behavior prompt,
player model profile, and `turn_count` from 1 through 30.
